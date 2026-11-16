import os
import re
import json
import datetime
from typing import Generator
import streamlit as st
import pandas as pd
from openai import OpenAI, APITimeoutError, APIError, RateLimitError, BadRequestError
from database.connection import get_ai_sql_connection
from modules.admin_panel import validar_autorizacion_admin

# ---------------------------------------------------------------------------
# Esquema estático de respaldo (tablas base permitidas + 14 vistas materializadas)
# ---------------------------------------------------------------------------
_DATABASE_SCHEMA_STATIC = """\
[TABLAS BASE]
productos(id integer, user_id integer, cat_id integer, prov_id integer, sede_id integer, nombre text, fecha_ingreso text, vida_min integer)
sedes(id integer, user_id integer, nombre text, direccion text)
categorias(id integer, user_id integer, nombre text)
detalles(id integer, prod_id integer, serial text, marca text)
proveedores(id integer, user_id integer, nombre text, contacto text, nit text)
auditoria(id integer, user_id integer, accion text, fecha_hora text)

[VISTAS MATERIALIZADAS]
mv_usuarios_resumen(id integer, "user" text, correo text)
mv_usuarios_contacto(id integer, "user" text, telefono text)
mv_categorias_conteo(user_id integer, total_categorias bigint)
mv_categorias_listado(id integer, user_id integer, nombre text)
mv_proveedores_nit(id integer, nombre text, nit text)
mv_proveedores_contacto(id integer, nombre text, contacto text)
mv_sedes_resumen(id integer, nombre text, direccion text)
mv_sedes_conteo(user_id integer, total_sedes bigint)
mv_productos_activos(id integer, nombre text, vida_min integer)
mv_productos_resumen(sede_id integer, total_productos bigint)
mv_detalles_marcas(id integer, marca text)
mv_detalles_seriales(id integer, serial text)
mv_auditoria_log(id integer, accion text)
mv_auditoria_fechas(id integer, fecha_hora timestamp)"""

# Tablas base permitidas explícitamente para consultas de inventario (solo lectura)
ALLOWED_BASE_TABLES = {"productos", "sedes", "categorias", "detalles", "proveedores", "auditoria"}

# Tablas con información sensible estrictamente bloqueadas
BLOCKED_SENSITIVE_TABLES = {"usuarios"}


def _run_readonly_sql(sql_query: str) -> pd.DataFrame:
    """
    Ejecuta una consulta SQL de solo lectura usando get_ai_sql_connection().
    Si ocurre un error (p. ej. PendingRollbackError por transacción invalidada),
    se realiza rollback de la sesión SQLAlchemy y se reintenta una vez antes de relanzar.
    """
    conn = get_ai_sql_connection()
    try:
        return conn.query(sql_query, ttl=0)
    except Exception:
        try:
            conn.session.rollback()
        except Exception:
            pass
        return conn.query(sql_query, ttl=0)


@st.cache_data(ttl=3600, show_spinner=False)
def get_mv_schema_description() -> tuple[str, str]:
    """
    Consulta el catálogo de PostgreSQL para obtener los nombres reales y columnas
    de las vistas materializadas (mv_*) y de las tablas base permitidas.
    Devuelve: (schema_description, origin) donde origin es 'dinámico' o 'fallback'.
    """
    catalog_sql = """
        SELECT
            c.relname          AS object_name,
            c.relkind          AS object_kind,
            a.attname          AS col_name,
            pg_catalog.format_type(a.atttypid, a.atttypmod) AS col_type
        FROM pg_class c
        JOIN pg_namespace n  ON n.oid  = c.relnamespace
        JOIN pg_attribute a  ON a.attrelid = c.oid
        WHERE n.nspname  = 'public'
          AND a.attnum   > 0
          AND NOT a.attisdropped
          AND (
            (c.relkind = 'm' AND c.relname LIKE 'mv_%')
            OR
            (c.relkind = 'r' AND c.relname IN ('productos', 'sedes', 'categorias', 'detalles', 'proveedores', 'auditoria'))
          )
        ORDER BY c.relkind, c.relname, a.attnum;
    """
    try:
        df = _run_readonly_sql(catalog_sql)
        if df.empty:
            return _DATABASE_SCHEMA_STATIC, "fallback"

        base_tables: list[str] = []
        mat_views: list[str] = []

        for (object_name, kind), group in df.groupby(["object_name", "object_kind"], sort=True):
            cols = ", ".join(
                f"{row['col_name']} {row['col_type']}"
                for _, row in group.iterrows()
            )
            entry = f"{object_name}({cols})"
            if kind == "r":
                base_tables.append(entry)
            else:
                mat_views.append(entry)

        parts = []
        if base_tables:
            parts.append("[TABLAS BASE]\n" + "\n".join(base_tables))
        if mat_views:
            parts.append("[VISTAS MATERIALIZADAS]\n" + "\n".join(mat_views))

        return "\n\n".join(parts), "dinámico"
    except Exception:
        return _DATABASE_SCHEMA_STATIC, "fallback"

FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "TRUNCATE", "CREATE", "GRANT", "REVOKE", "REPLACE", "EXECUTE"
]

MODELO_PRINCIPAL = "openai/gpt-oss-120b"
MODELO_FALLBACK  = "openai/gpt-oss-20b"


# ---------------------------------------------------------------------------
# Cliente GroqCloud
# ---------------------------------------------------------------------------
def get_groq_client() -> OpenAI | None:
    """Obtiene el cliente OpenAI apuntando a GroqCloud."""
    api_key = None
    try:
        api_key = st.secrets.get("GROQ_API_KEY")
    except Exception:
        pass
    if not api_key:
        api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    return OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key,
        timeout=30.0,
        max_retries=1,
    )


# ---------------------------------------------------------------------------
# Wrapper de llamada con fallbacks
# ---------------------------------------------------------------------------
def execute_groq_chat_completion(client: OpenAI, **kwargs):
    """
    Llama a client.chat.completions.create con modelo principal.
    Fallbacks:
      - BadRequestError / TypeError por reasoning_effort → reintento sin ese parámetro.
      - RateLimitError / APITimeoutError / HTTP 413/429/5xx → reintento con modelo fallback.
    Si el intento de fallback también falla, se propaga el error final con el
    contexto de ambos intentos preservado.
    """
    def _create_call(params: dict):
        try:
            return client.chat.completions.create(**params)
        except (BadRequestError, TypeError) as exc:
            if "reasoning_effort" in params:
                reduced = {k: v for k, v in params.items() if k != "reasoning_effort"}
                return client.chat.completions.create(**reduced)
            raise exc

    def _should_fallback(exc: Exception) -> bool:
        if isinstance(exc, (RateLimitError, APITimeoutError)):
            return True
        if isinstance(exc, APIError):
            code = getattr(exc, "status_code", None)
            if code in (413, 429, 500, 502, 503, 504):
                return True
            msg = str(exc).lower()
            if any(k in msg for k in ("413", "429", "rate limit", "timeout", "tpm", "tokens per minute")):
                return True
        return False

    primary_params = dict(kwargs)
    primary_params["model"] = MODELO_PRINCIPAL
    try:
        return _create_call(primary_params)
    except Exception as primary_err:
        if _should_fallback(primary_err):
            fallback_params = dict(kwargs)
            fallback_params["model"] = MODELO_FALLBACK
            try:
                return _create_call(fallback_params)
            except Exception as fallback_err:
                # Propagar el error del fallback preservando contexto del primario
                raise RuntimeError(
                    f"[{MODELO_PRINCIPAL}] {primary_err} → "
                    f"[{MODELO_FALLBACK}] {fallback_err}"
                ) from fallback_err
        raise primary_err


# ---------------------------------------------------------------------------
# Generador de stream limpio (filtra <think>...</think> y <reasoning>...</reasoning>)
# ---------------------------------------------------------------------------
def stream_generator_clean(stream) -> Generator[str, None, None]:
    """
    Itera todos los chunks del stream. Filtra bloques <think>...</think> y <reasoning>...</reasoning>.
    Devuelve (yield) texto visible de inmediato sin tragarse operadores matemáticos
    como '< 0', '<=', '< 100', etc.
    """
    TAG_PAIRS = [
        ("<think>", "</think>"),
        ("<reasoning>", "</reasoning>"),
    ]
    OPEN_TAGS = [pair[0] for pair in TAG_PAIRS]
    MAX_OPEN_LEN = max(len(tag) for tag in OPEN_TAGS)

    inside_tag: str | None = None
    buffer = ""
    last_finish_reason = None

    for chunk in stream:
        if not chunk.choices or len(chunk.choices) == 0:
            continue
        choice = chunk.choices[0]
        # Capturar finish_reason si está disponible en este chunk
        if getattr(choice, "finish_reason", None):
            last_finish_reason = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if not delta:
            continue
        content = getattr(delta, "content", None)
        if not content:
            continue

        buffer += content

        while buffer:
            if inside_tag is None:
                # Buscar el inicio del tag abierto más próximo
                first_open_idx = -1
                matched_open_tag = None
                matched_close_tag = None
                for open_tag, close_tag in TAG_PAIRS:
                    idx = buffer.find(open_tag)
                    if idx != -1:
                        if first_open_idx == -1 or idx < first_open_idx:
                            first_open_idx = idx
                            matched_open_tag = open_tag
                            matched_close_tag = close_tag

                if matched_open_tag is not None:
                    # Emitir todo lo anterior al tag
                    if first_open_idx > 0:
                        yield buffer[:first_open_idx]
                    inside_tag = matched_close_tag
                    buffer = buffer[first_open_idx + len(matched_open_tag):]
                else:
                    # Verificar si el final del buffer coincide parcialmente con algún tag de apertura
                    # (ej. '<', '<t', '<th', '<thi', '<thin', '<r', etc.)
                    partial_len = 0
                    for open_tag in OPEN_TAGS:
                        for l in range(min(len(buffer), len(open_tag) - 1), 0, -1):
                            if buffer.endswith(open_tag[:l]):
                                if l > partial_len:
                                    partial_len = l

                    if partial_len > 0:
                        # Yield todo excepto el prefijo parcial sospechoso
                        prefix = buffer[:-partial_len]
                        if prefix:
                            yield prefix
                        buffer = buffer[-partial_len:]
                        # Esperar a que lleguen más caracteres en el siguiente chunk
                        break
                    else:
                        # No hay tags ni prefijos de tags conocidos; yield todo el buffer inmediatamente
                        yield buffer
                        buffer = ""
            else:
                # Estamos dentro de un bloque de pensamiento; descartar contenido hasta encontrar inside_tag
                idx_close = buffer.find(inside_tag)
                if idx_close != -1:
                    buffer = buffer[idx_close + len(inside_tag):]
                    inside_tag = None
                else:
                    # Descartar lo acumulado preservando un posible cierre parcial al final
                    partial_close_len = 0
                    for l in range(min(len(buffer), len(inside_tag) - 1), 0, -1):
                        if buffer.endswith(inside_tag[:l]):
                            partial_close_len = l
                            break
                    if partial_close_len > 0:
                        buffer = buffer[-partial_close_len:]
                    else:
                        buffer = ""
                    break

    if buffer and inside_tag is None:
        yield buffer

    # Emitir finish_reason como atributo accesible si es necesario
    stream_generator_clean._last_finish_reason = last_finish_reason


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------
def sanitize_sql(raw_sql: str) -> str:
    """Elimina delimitadores de código Markdown (```sql ... ```) y espacios."""
    if not raw_sql:
        return ""
    sql = raw_sql.strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip()


def validate_sql_security(sql: str) -> tuple[bool, str]:
    """
    Valida que la consulta sea estrictamente SELECT de solo lectura,
    bloqueando sentencias múltiples, palabras clave de modificación y tablas sensibles (ej. usuarios).
    Permite tablas base autorizadas de inventario y vistas materializadas (mv_*).
    """
    cleaned = sanitize_sql(sql)
    stmt = cleaned.rstrip().rstrip(";")
    if ";" in stmt:
        return False, "Rechazado: No se permiten múltiples sentencias SQL (;)."
    if not re.match(r"^\s*(SELECT|WITH)\b", stmt, re.IGNORECASE):
        return False, "Rechazado: La consulta debe comenzar con SELECT o WITH."
    
    # Bloqueo de palabras clave de modificación/escritura
    pattern = r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b"
    m = re.search(pattern, stmt, re.IGNORECASE)
    if m:
        return False, f"Rechazado: Uso prohibido de '{m.group(0).upper()}'."
    
    # Bloqueo de acceso a tablas sensibles
    sensitive_pattern = r"\b(" + "|".join(BLOCKED_SENSITIVE_TABLES) + r")\b"
    sm = re.search(sensitive_pattern, stmt, re.IGNORECASE)
    if sm:
        return False, f"Rechazado: Acceso restringido a la tabla protegida '{sm.group(0)}'."
    
    return True, stmt


# ---------------------------------------------------------------------------
# Tabla de acción priorizada construida en Python (sin depender del modelo)
# ---------------------------------------------------------------------------
def _build_action_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construye la tabla consolidada de acción priorizada a partir de df_criticos.
    Regla sanitaria: expirados (pct_vida >= 100) → solo 'Retiro y baja sanitaria'.
    Críticos no expirados (80 <= pct_vida < 100) → 'Revisión y rotación preventiva'.
    Ordena: expirados primero, luego críticos, de mayor a menor pct_vida.
    """
    df_work = df.copy()
    df_work["pct_vida"] = pd.to_numeric(df_work["pct_vida"], errors="coerce").fillna(0.0)

    def _accion(row) -> str:
        if row["pct_vida"] >= 100:
            return "Retiro y baja sanitaria"
        return "Revisión y rotación preventiva"

    def _plazo(row) -> str:
        if row["pct_vida"] >= 100:
            return "Inmediato (0-24 h)"
        if row["pct_vida"] >= 90:
            return "Urgente (1-3 días)"
        return "Corto plazo (3-7 días)"

    def _prioridad(row) -> str:
        if row["pct_vida"] >= 100:
            return "🔴 CRÍTICO"
        if row["pct_vida"] >= 90:
            return "🟠 ALTO"
        return "🟡 MEDIO"

    df_work["Prioridad"]        = df_work.apply(_prioridad, axis=1)
    df_work["Acción Inmediata"] = df_work.apply(_accion, axis=1)
    df_work["Plazo"]            = df_work.apply(_plazo, axis=1)
    df_work["Estado (% Vida)"]  = df_work["pct_vida"].apply(lambda v: f"{v:.1f}%")

    # Ordenar: primero expirados (pct_vida>=100), luego críticos; desc pct_vida
    df_work["_expired"] = df_work["pct_vida"] >= 100
    df_work = df_work.sort_values(["_expired", "pct_vida"], ascending=[False, False]).drop(columns=["_expired"])

    cols = ["Prioridad", "sede", "producto", "Estado (% Vida)", "Acción Inmediata", "Plazo"]
    available = [c for c in cols if c in df_work.columns]
    return df_work[available].rename(columns={"sede": "Sede", "producto": "Producto"}).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Flujo seguro de intención estructurada para operadores
# ---------------------------------------------------------------------------
ENTIDADES_PERMITIDAS = {
    "productos",
    "categorias",
    "proveedores",
    "sedes",
    "detalles",
    "auditoria",
    "conteo_general",
}

CAMPOS_PERMITIDOS_PRODUCTOS = {
    "id", "nombre", "fecha_ingreso", "vida_min", "categoria", "sede", "serial", "marca", "pct_vida", "dias_restantes"
}


def _extraer_intencion_operador(client: OpenAI, pregunta: str) -> dict | None:
    """
    Solicita al modelo clasificar la intención de la consulta del operador en una
    estructura JSON estricta y predefinida. No genera SQL.
    """
    system_prompt = """Eres un asistente de enrutamiento de consultas de inventario para un usuario operador.
Tu tarea es convertir la pregunta del operador a un objeto JSON estrictamente válido que describa su intención.
NO produzcas código SQL bajo ninguna circunstancia.

ENTIDADES PERMITIDAS:
1. "productos": Consultas sobre productos/activos, existencias, vencimiento, marcas, seriales, sedes o categorías.
2. "categorias": Consultas para ver la lista de categorías del usuario o conteo de categorías.
3. "proveedores": Consultas para ver la lista de proveedores del usuario o sus contactos/NIT.
4. "sedes": Consultas para ver las sedes o ubicaciones del usuario.
5. "auditoria": Consultas para ver los registros de actividad o auditoría del propio usuario.
6. "conteo_general": Consultas que preguntan cuántos productos, categorías, sedes o proveedores tiene en total.

CAMPOS DEL JSON (responde ÚNICAMENTE con este formato JSON):
{
  "entidad": "productos" | "categorias" | "proveedores" | "sedes" | "auditoria" | "conteo_general",
  "filtro_texto": "texto para buscar en nombre, marca, serial o contacto" | null,
  "filtro_sede": "nombre de la sede a filtrar" | null,
  "filtro_categoria": "nombre de la categoría a filtrar" | null,
  "solo_proximos_a_vencer": true | false,
  "solo_expirados": true | false,
  "solo_criticos": true | false,
  "orden": "vencimiento_asc" | "pct_vida_desc" | "recientes" | "nombre_asc",
  "limite": 1 a 100
}
Si la pregunta no corresponde a ninguna de las entidades permitidas o solicita ver datos ajenos/globales de otros usuarios, responde con:
{"error": "consulta_no_soportada", "motivo": "explicación breve de lo que se puede consultar"}
"""
    try:
        resp = execute_groq_chat_completion(
            client,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Pregunta del operador: {pregunta.strip()}"},
            ],
            temperature=0.1,
            max_completion_tokens=500,
            reasoning_effort="low",
            stream=False,
        )
        if not resp.choices:
            return None
        content = resp.choices[0].message.content or ""
        content = content.strip()
        # Limpiar bloques markdown si vinieran
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _construir_sql_operador_seguro(intencion: dict, user_id: int) -> tuple[str | None, str | None]:
    """
    Construye de forma determinista y segura en el servidor una consulta SQL
    con plantillas fijas y parámetros enlazados, forzando obligatoriamente
    el user_id del operador autenticado.
    Devuelve: (sql_query, mensaje_error)
    """
    if "error" in intencion:
        return None, intencion.get("motivo", "La consulta solicitada no está disponible dentro de las operaciones permitidas.")

    entidad = str(intencion.get("entidad", "")).strip().lower()
    if entidad not in ENTIDADES_PERMITIDAS:
        return None, f"La entidad '{entidad}' no está soportada. Puedes consultar sobre tus productos, categorías, proveedores, sedes o auditoría."

    u_id = int(user_id)
    limite = min(max(int(intencion.get("limite") or 50), 1), 100)

    # 1. ENTIDAD: PRODUCTOS
    if entidad == "productos":
        condiciones = [f"p.user_id = {u_id}", "p.fecha_ingreso IS NOT NULL", "p.vida_min > 0"]

        filtro_texto = intencion.get("filtro_texto")
        if filtro_texto and isinstance(filtro_texto, str) and filtro_texto.strip():
            txt = filtro_texto.strip().replace("'", "''")
            condiciones.append(f"(p.nombre ILIKE '%{txt}%' OR d.marca ILIKE '%{txt}%' OR d.serial ILIKE '%{txt}%')")

        filtro_sede = intencion.get("filtro_sede")
        if filtro_sede and isinstance(filtro_sede, str) and filtro_sede.strip():
            sd = filtro_sede.strip().replace("'", "''")
            condiciones.append(f"s.nombre ILIKE '%{sd}%'")

        filtro_cat = intencion.get("filtro_categoria")
        if filtro_cat and isinstance(filtro_cat, str) and filtro_cat.strip():
            cat = filtro_cat.strip().replace("'", "''")
            condiciones.append(f"c.nombre ILIKE '%{cat}%'")

        if intencion.get("solo_expirados"):
            condiciones.append("(EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100) >= 100")
        elif intencion.get("solo_criticos"):
            condiciones.append(
                "((EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100) >= 80 "
                "AND (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100) < 100)"
            )
        elif intencion.get("solo_proximos_a_vencer"):
            condiciones.append(
                "((p.fecha_ingreso::timestamp + (p.vida_min || ' minutes')::interval) <= NOW() + INTERVAL '7 days' "
                "OR (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100) >= 80)"
            )

        orden_solicitado = intencion.get("orden")
        if orden_solicitado == "vencimiento_asc":
            order_clause = "ORDER BY fecha_vencimiento ASC"
        elif orden_solicitado == "pct_vida_desc":
            order_clause = "ORDER BY pct_vida DESC"
        elif orden_solicitado == "nombre_asc":
            order_clause = "ORDER BY p.nombre ASC"
        else:
            order_clause = "ORDER BY p.id DESC"

        where_clause = " AND ".join(condiciones)

        sql = f"""
            SELECT
                p.id,
                p.nombre AS producto,
                COALESCE(c.nombre, 'Sin Categoría') AS categoria,
                COALESCE(s.nombre, 'Sin Sede') AS sede,
                COALESCE(d.marca, 'N/A') AS marca,
                COALESCE(d.serial, 'N/A') AS serial,
                ROUND((EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100)::numeric, 2) AS pct_vida,
                ROUND((EXTRACT(EPOCH FROM ((p.fecha_ingreso::timestamp + (p.vida_min || ' minutes')::interval) - NOW())) / 86400.0)::numeric, 1) AS dias_restantes,
                (p.fecha_ingreso::timestamp + (p.vida_min || ' minutes')::interval)::date AS fecha_vencimiento
            FROM productos p
            LEFT JOIN categorias c ON p.cat_id = c.id
            LEFT JOIN sedes s ON p.sede_id = s.id
            LEFT JOIN detalles d ON p.id = d.prod_id
            WHERE {where_clause}
            {order_clause}
            LIMIT {limite}
        """.strip()
        return sql, None

    # 2. ENTIDAD: CATEGORÍAS
    elif entidad == "categorias":
        condiciones = [f"c.user_id = {u_id}"]
        filtro_texto = intencion.get("filtro_texto")
        if filtro_texto and isinstance(filtro_texto, str) and filtro_texto.strip():
            txt = filtro_texto.strip().replace("'", "''")
            condiciones.append(f"c.nombre ILIKE '%{txt}%'")
        where_clause = " AND ".join(condiciones)
        sql = f"""
            SELECT 
                c.id, 
                c.nombre AS categoria,
                COUNT(p.id) AS total_productos
            FROM categorias c
            LEFT JOIN productos p ON c.id = p.cat_id AND p.user_id = {u_id}
            WHERE {where_clause}
            GROUP BY c.id, c.nombre
            ORDER BY c.nombre ASC
            LIMIT {limite}
        """.strip()
        return sql, None

    # 3. ENTIDAD: PROVEEDORES
    elif entidad == "proveedores":
        condiciones = [f"pr.user_id = {u_id}"]
        filtro_texto = intencion.get("filtro_texto")
        if filtro_texto and isinstance(filtro_texto, str) and filtro_texto.strip():
            txt = filtro_texto.strip().replace("'", "''")
            condiciones.append(f"(pr.nombre ILIKE '%{txt}%' OR pr.contacto ILIKE '%{txt}%' OR pr.nit ILIKE '%{txt}%')")
        where_clause = " AND ".join(condiciones)
        sql = f"""
            SELECT 
                pr.id, 
                pr.nombre AS proveedor, 
                COALESCE(pr.contacto, 'Sin contacto') AS contacto, 
                COALESCE(pr.nit, 'Sin NIT') AS nit,
                COUNT(p.id) AS total_productos
            FROM proveedores pr
            LEFT JOIN productos p ON pr.id = p.prov_id AND p.user_id = {u_id}
            WHERE {where_clause}
            GROUP BY pr.id, pr.nombre, pr.contacto, pr.nit
            ORDER BY pr.nombre ASC
            LIMIT {limite}
        """.strip()
        return sql, None

    # 4. ENTIDAD: SEDES
    elif entidad == "sedes":
        condiciones = [f"s.user_id = {u_id}"]
        filtro_texto = intencion.get("filtro_texto")
        if filtro_texto and isinstance(filtro_texto, str) and filtro_texto.strip():
            txt = filtro_texto.strip().replace("'", "''")
            condiciones.append(f"(s.nombre ILIKE '%{txt}%' OR s.direccion ILIKE '%{txt}%')")
        where_clause = " AND ".join(condiciones)
        sql = f"""
            SELECT 
                s.id, 
                s.nombre AS sede, 
                COALESCE(s.direccion, 'Sin dirección') AS direccion,
                COUNT(p.id) AS total_productos
            FROM sedes s
            LEFT JOIN productos p ON s.id = p.sede_id AND p.user_id = {u_id}
            WHERE {where_clause}
            GROUP BY s.id, s.nombre, s.direccion
            ORDER BY s.nombre ASC
            LIMIT {limite}
        """.strip()
        return sql, None

    # 5. ENTIDAD: AUDITORÍA DEL USUARIO
    elif entidad == "auditoria":
        condiciones = [f"a.user_id = {u_id}"]
        filtro_texto = intencion.get("filtro_texto")
        if filtro_texto and isinstance(filtro_texto, str) and filtro_texto.strip():
            txt = filtro_texto.strip().replace("'", "''")
            condiciones.append(f"a.accion ILIKE '%{txt}%'")
        where_clause = " AND ".join(condiciones)
        sql = f"""
            SELECT 
                a.id, 
                a.accion, 
                a.fecha_hora
            FROM auditoria a
            WHERE {where_clause}
            ORDER BY a.id DESC
            LIMIT {limite}
        """.strip()
        return sql, None

    # 6. ENTIDAD: CONTEO GENERAL
    elif entidad == "conteo_general":
        sql = f"""
            SELECT
                (SELECT COUNT(*) FROM productos WHERE user_id = {u_id})::int AS total_productos,
                (SELECT COUNT(*) FROM categorias WHERE user_id = {u_id})::int AS total_categorias,
                (SELECT COUNT(*) FROM sedes WHERE user_id = {u_id})::int AS total_sedes,
                (SELECT COUNT(*) FROM proveedores WHERE user_id = {u_id})::int AS total_proveedores
        """.strip()
        return sql, None

    return None, "No se pudo interpretar la estructura de la consulta solicitada."


# ---------------------------------------------------------------------------
# Diálogo principal
# ---------------------------------------------------------------------------
@st.dialog("Copiloto de IA", width="large")
def mostrar_copiloto_ia():
    """Diálogo modal del Copiloto de IA basado en GroqCloud sobre Neon PostgreSQL."""
    # Validación estricta de sesión y rol reconocido
    if not st.session_state.get("logged_in", False):
        st.error("⛔ Acceso denegado: Se requiere iniciar sesión para usar el Copiloto.")
        return

    user_id_actual = st.session_state.get("user_id")
    if not user_id_actual or not isinstance(user_id_actual, int):
        st.error("⛔ Acceso denegado: Sesión de usuario no válida.")
        return

    rol_sesion = str(st.session_state.get("rol", "")).strip().lower()
    if rol_sesion not in ("admin", "operador"):
        st.error("⛔ Acceso denegado: Rol de usuario no reconocido en el sistema.")
        return

    # Desempaquetado explícito de la tupla centralizada de autorización
    es_admin, _, _ = validar_autorizacion_admin()

    st.markdown("### 🤖 Copiloto Inteligente de Inventario y Operaciones")
    st.caption("Alimentado por GroqCloud (`openai/gpt-oss-120b`) sobre Neon PostgreSQL.")

    client = get_groq_client()
    if not client:
        st.warning(
            "⚠️ **API Key no configurada:** No se encontró `GROQ_API_KEY` en `st.secrets` "
            "ni en las variables de entorno. Define `GROQ_API_KEY` en "
            "`.streamlit/secrets.toml` o en las variables del sistema para habilitar el copiloto."
        )

    tab_consulta, tab_renovacion = st.tabs(["🔍 Consulta Inteligente", "🔄 Plan de Renovación"])

    # -----------------------------------------------------------------------
    # TAB 1: Consulta Inteligente (Habilitada para Admin y Operador)
    # -----------------------------------------------------------------------
    with tab_consulta:
        st.markdown("#### Consulta Inteligente en Lenguaje Natural")
        if es_admin:
            st.write(
                "Modo Administrador: Haz preguntas sobre inventario global, sedes, proveedores, marcas o auditoría. "
                "El modelo generará y ejecutará consultas SQL sobre las vistas materializadas y tablas base autorizadas."
            )
        else:
            st.write(
                "Haz preguntas en lenguaje natural sobre tus productos, categorías, sedes, proveedores o fechas de vencimiento. "
                "El sistema interpretará tu pregunta y recuperará únicamente los registros de tu inventario."
            )

        # --- FORMULARIO: visible y operativo para admin y operador ---
        with st.form(key="form_consulta_inteligente", clear_on_submit=False):
            pregunta = st.text_area(
                "¿Qué deseas consultar?",
                placeholder="Ej: ¿Cuáles son mis productos próximos a vencer? o ¿Cuántos productos tengo por sede?",
                key="ai_copilot_pregunta",
            )
            ejecutar_consulta = st.form_submit_button("🚀 Consultar", type="primary", width="stretch")

        # --- Panel de diagnóstico (solo visible para administradores) ---
        log_expander = None
        log_box = None
        if es_admin:
            log_expander = st.expander("🛠️ Log de diagnóstico (paso a paso)", expanded=True)
            log_box = log_expander.empty()

        logs: list[str] = []

        def _log(msg: str) -> None:
            if es_admin and log_box is not None:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                logs.append(f"[{ts}] {msg}")
                log_box.code("\n".join(logs), language="text")

        # --- Procesamiento ---
        if ejecutar_consulta:
            # Paso 1: validar pregunta
            if not pregunta.strip():
                st.warning("Por favor, ingresa una pregunta antes de consultar.")
                _log("SUBMIT recibido — pregunta vacía, flujo detenido.")
            elif not client:
                st.error("No se puede contactar a GroqCloud porque falta `GROQ_API_KEY`.")
                _log("SUBMIT recibido — GROQ_API_KEY no disponible, flujo detenido.")
            else:
                _log(f"SUBMIT recibido — pregunta presente ({len(pregunta.strip())} caracteres). Rol: {'admin' if es_admin else 'operador'}.")

                resultado_sql = None
                sql_valido = False

                # -------------------------------------------------------------
                # RUTA A: ADMINISTRADOR (SQL generado con validación de seguridad)
                # -------------------------------------------------------------
                if es_admin:
                    # Cargar esquema real
                    schema_desc, schema_origin = get_mv_schema_description()
                    _log(f"Origen del esquema de base de datos: '{schema_origin}' (ttl=3600s).")

                    system_prompt = f"""Eres un motor SQL de solo lectura experto para un sistema de inventario en PostgreSQL (Neon).
Tu única tarea es producir UNA consulta SQL válida y ejecutable que recupere los datos factuales de la base de datos necesarios para responder la pregunta del usuario.

ESQUEMA EXACTO DISPONIBLE (tablas base de inventario y vistas materializadas):
{schema_desc}

RELACIONES VERIFICADAS EN EL SISTEMA:
- productos.sede_id = sedes.id (sede/ubicación donde se encuentra el producto)
- productos.cat_id = categorias.id (categoría del producto)
- productos.prov_id = proveedores.id (proveedor del producto)
- detalles.prod_id = productos.id (marca y serial físico del producto)
- auditoria.user_id = sedes.user_id / productos.user_id (registro de eventos)
NUNCA asumas ni inventes relaciones que no estén listadas (ej: NUNCA hagas JOIN productos.id = sedes.id).

SEMÁNTICA CRÍTICA DE VENCIMIENTO Y SEDE (REGLAS OBLIGATORIAS):
1. 'vida_min' (en minutos) representa la duración TOTAL de vida útil del producto desde su ingreso. NUNCA uses 'vida_min <= 30' para indicar "vence en 30 días" ni compares 'vida_min' con días.
2. Fecha estimada de vencimiento:
   (p.fecha_ingreso::timestamp + (p.vida_min || ' minutes')::interval)
3. Días restantes hasta el vencimiento:
   ROUND((EXTRACT(EPOCH FROM ((p.fecha_ingreso::timestamp + (p.vida_min || ' minutes')::interval) - NOW())) / 86400.0)::numeric, 1)
4. Porcentaje de vida consumido (% vida):
   ROUND((EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100)::numeric, 2)
   (OJO: 'pct_vida' es un cálculo, NO existe como columna física en las tablas base).
5. Para consultar productos de una sede: haz JOIN productos p con sedes s (p.sede_id = s.id) y filtra con ILIKE (ej: s.nombre ILIKE '%Suba%').
6. Para "próximos a vencer", si el usuario no especifica una ventana de tiempo exacta, aplica por defecto una ventana de 7 días:
   (p.fecha_ingreso::timestamp + (p.vida_min || ' minutes')::interval) <= NOW() + INTERVAL '7 days'
   o un desgaste crítico (pct_vida >= 80).
7. Excluye siempre registros sin fecha o con vida_min nula o cero:
   WHERE p.fecha_ingreso IS NOT NULL AND p.vida_min > 0
8. Ordena por fecha de vencimiento más próxima (ASC) o por pct_vida DESC.

REGLAS DE SEGURIDAD Y FORMATO:
1. Responde ÚNICAMENTE con código SQL ejecutable. Sin explicaciones, sin markdown de más, sin comentarios ni texto adicional.
2. Usa EXCLUSIVAMENTE sentencias SELECT o WITH … SELECT. Solo lectura.
3. Prohibido consultar la tabla 'usuarios' o campos de contraseñas.
4. NUNCA hardcodees recomendaciones comerciales, promociones, porcentajes de descuento ni textos de negocio dentro de literales del SELECT. El SQL solo debe devolver hechos de la base de datos (nombres, sedes, fechas, cálculos de días y pct_vida).
5. Añade siempre LIMIT 100 para evitar desbordamientos.
"""
                    user_prompt = f"Pregunta del administrador: {pregunta.strip()}"
                    _log(f"Llamada a Groq (Admin) — modelo: {MODELO_PRINCIPAL}")

                    raw_sql = ""
                    try:
                        with st.spinner("Generando consulta SQL administrativa…"):
                            response = execute_groq_chat_completion(
                                client,
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt},
                                ],
                                temperature=0.1,
                                max_completion_tokens=4096,
                                reasoning_effort="low",
                                stream=False,
                            )
                        if response.choices and response.choices[0].message:
                            raw_sql = response.choices[0].message.content or ""
                    except Exception as err:
                        _log(f"ERROR en llamada Groq: {type(err).__name__}: {str(err)[:200]}")
                        st.error(f"❌ Error al comunicarse con el servicio de IA: {err}")

                    if raw_sql.strip():
                        clean_sql = sanitize_sql(raw_sql)
                        valido, sec_resultado = validate_sql_security(clean_sql)
                        if not valido:
                            _log(f"SQL rechazado por seguridad — motivo: {sec_resultado}")
                            st.error(f"🛡️ **Filtro de Seguridad Activado:** {sec_resultado}")
                            with st.expander("Ver consulta rechazada"):
                                st.code(clean_sql, language="sql")
                        else:
                            resultado_sql = sec_resultado
                            sql_valido = True
                            _log(f"SQL validado — PASS (len={len(resultado_sql)} chars).")
                            with st.expander("Ver consulta SQL generada", expanded=False):
                                st.code(resultado_sql, language="sql")
                    else:
                        st.error("El modelo no generó una consulta SQL. Intenta reformular tu pregunta.")

                # -------------------------------------------------------------
                # RUTA B: OPERADOR (Flujo seguro de intención estructurada)
                # -------------------------------------------------------------
                else:
                    with st.spinner("Interpretando consulta estructurada para tu inventario…"):
                        intencion_dict = _extraer_intencion_operador(client, pregunta)

                    if not intencion_dict:
                        st.warning(
                            "ℹ️ No fue posible interpretar tu pregunta de forma estructurada. "
                            "Por favor realiza preguntas concretas como: '¿Cuáles son mis productos próximos a vencer?', "
                            "'¿Cuántos productos tengo por categoría?' o 'Mostrar mis productos en la sede Norte'."
                        )
                    else:
                        sql_construido, err_intencion = _construir_sql_operador_seguro(intencion_dict, user_id_actual)
                        if err_intencion or not sql_construido:
                            st.info(
                                f"ℹ️ {err_intencion or 'Consulta no disponible.'}\n\n"
                                "**Consultas que puedes realizar:**\n"
                                "- Productos propios, fechas de vencimiento y alertas de ciclo de vida.\n"
                                "- Listado de tus categorías, sedes y proveedores vinculados.\n"
                                "- Conteo y resumen de tus propios activos y registros de auditoría personal."
                            )
                        else:
                            # Validación adicional de seguridad con validate_sql_security
                            valido, sec_sql = validate_sql_security(sql_construido)
                            if valido:
                                resultado_sql = sec_sql
                                sql_valido = True
                            else:
                                st.error("❌ La consulta generada internamente no superó la validación de seguridad.")

                # -------------------------------------------------------------
                # EJECUCIÓN Y PRESENTACIÓN DE RESULTADOS (Admin y Operador)
                # -------------------------------------------------------------
                if sql_valido and resultado_sql:
                    df_resultado = None
                    try:
                        with st.spinner("Consultando base de datos en Neon PostgreSQL…"):
                            df_resultado = _run_readonly_sql(resultado_sql)
                    except Exception as db_err:
                        if es_admin:
                            _log(f"ERROR en Neon: {type(db_err).__name__}: {str(db_err)[:200]}")
                            st.error(f"❌ Error al ejecutar la consulta en Neon: {db_err}")
                        else:
                            st.error("❌ Ocurrió un error al recuperar los datos del sistema. Intente nuevamente.")

                    if df_resultado is not None:
                        total_filas = len(df_resultado)
                        posible_truncado = (total_filas >= 100)

                        if df_resultado.empty:
                            st.markdown("##### 📊 Resultados de la Base de Datos")
                            st.info(
                                "ℹ️ La consulta se ejecutó exitosamente, pero no se encontraron "
                                "registros en la base de datos que coincidan exactamente con esos criterios o ventana temporal. "
                                "Si buscabas productos próximos a vencer, considera ampliar el rango de búsqueda o verificar la sede."
                            )

                            # Síntesis honesta sin alucinaciones cuando hay 0 filas
                            st.markdown("---")
                            st.markdown("##### 💡 Síntesis Estratégica y Recomendaciones")
                            resumen_system_vacio = (
                                "Eres el Copiloto Experto en Inventario y Gestión Operativa del sistema. "
                                "La consulta a la base de datos se ejecutó de forma correcta pero arrojó CERO registros (0 filas). "
                                "REGLA ESTRICTA DE TRANSPARENCIA: NO inventes ni nombres productos, cantidades, fechas ni descuentos inexistentes. "
                                "Comunica claramente que no hay productos que coincidan con la sede o criterios en la ventana evaluada, "
                                "y sugiere acciones operativas lógicas como verificar el nombre de la sede o ampliar la ventana de días de vencimiento."
                            )
                            resumen_user_vacio = (
                                f"Pregunta del usuario: {pregunta.strip()}\n\n"
                                "Resultado de la base de datos: 0 registros encontrados.\n\n"
                                "Genera una respuesta breve, transparente y profesional explicando el resultado sin alucinar productos ni inventar promociones."
                            )
                            try:
                                stream_resumen = execute_groq_chat_completion(
                                    client,
                                    messages=[
                                        {"role": "system", "content": resumen_system_vacio},
                                        {"role": "user", "content": resumen_user_vacio},
                                    ],
                                    temperature=0.2,
                                    max_completion_tokens=1024,
                                    reasoning_effort="low",
                                    stream=True,
                                )
                                st.write_stream(stream_generator_clean(stream_resumen))
                            except Exception as empty_err:
                                if es_admin:
                                    st.warning(f"No fue posible generar el resumen: {empty_err}")
                                else:
                                    st.warning("No fue posible generar la síntesis automatizada.")
                        else:
                            st.markdown(f"##### 📊 Resultados de la Base de Datos ({total_filas} registros encontrados)")
                            if posible_truncado:
                                st.caption("⚠️ La lista puede haber sido truncada por el límite de seguridad de 100 registros.")
                            st.dataframe(df_resultado, width="stretch")

                            # Separación visual entre hechos de BD y síntesis estratégica
                            st.markdown("---")
                            st.markdown("##### 💡 Síntesis Estratégica y Recomendaciones")

                            filas_enviadas = min(30, total_filas)
                            muestra = df_resultado.head(filas_enviadas).to_json(orient="records", date_format="iso")

                            if total_filas > filas_enviadas:
                                st.caption(f"ℹ️ El análisis estratégico se elaboró considerando una muestra priorizada de las primeras {filas_enviadas} filas de un total de {total_filas} registros devueltos.")

                            resumen_system = (
                                "Eres el Copiloto Experto en Inventario y Gestión Estratégica del sistema. "
                                "Tu objetivo es sintetizar los datos factuales obtenidos de la base de datos y responder de forma directa, "
                                "transparente y accionable a la pregunta del usuario.\n\n"
                                "REGLAS OBLIGATORIAS:\n"
                                "1. NO DUPLICAR TABLAS: NO vuelvas a imprimir una tabla Markdown con las filas de la base de datos, ya que st.dataframe "
                                "las muestra directamente arriba. Resume los hallazgos brevemente en viñetas simples y pasa directamente a recomendaciones estratégicas concretas.\n"
                                "2. REGLA SANITARIA ABSOLUTA: Para productos expirados/vencidos (pct_vida >= 100 o días restantes < 0 o fecha vencida): "
                                "NUNCA recomiendes venderlos, aplicarles descuento, armar combos ni donarlos. Su única acción permitida es RETIRO INMEDIATO Y BAJA SANITARIA.\n"
                                "3. ACCIONES PARA NO EXPIRADOS: Para productos próximos a vencer NO expirados (pct_vida < 100): sugiere acciones ilustrativas de rotación o descuentos "
                                "priorizando los más críticos (80 <= pct_vida < 100), siempre sujetos a verificación de empaque e inocuidad.\n"
                                "4. Como la base de datos NO contiene costos ni márgenes financieros, etiqueta explícitamente cualquier porcentaje de descuento sugerido "
                                "como 'propuesta ilustrativa sujeta a validación de margen y costo'.\n"
                                "5. Separa claramente los hechos verificados en la base de datos de tus recomendaciones estratégicas.\n"
                                "6. SEGURIDAD: NO reveles consultas SQL, tablas internas, claves foráneas ni user_ids en tu respuesta."
                            )
                            # Si es admin se puede contextualizar con resultado_sql en el prompt interno; para operador solo hechos
                            contexto_consulta = f"Consulta ejecutada internamente:\n{resultado_sql}\n\n" if es_admin else ""
                            resumen_user = (
                                f"Pregunta del usuario:\n{pregunta.strip()}\n\n"
                                f"Total de filas recuperadas en base de datos: {total_filas}\n"
                                f"Filas enviadas para análisis ({filas_enviadas} de {total_filas}):\n{muestra}\n\n"
                                f"{contexto_consulta}"
                                "Proporciona una respuesta ejecutiva y estratégica sin duplicar la tabla de datos en Markdown, "
                                "resumiendo los hallazgos en viñetas y yendo directo a las recomendaciones estratégicas concretas, "
                                "cumpliendo estrictamente las normas sanitarias y la advertencia de margen financiero."
                            )
                            finish_reason_resumen = None
                            try:
                                stream_resumen = execute_groq_chat_completion(
                                    client,
                                    messages=[
                                        {"role": "system", "content": resumen_system},
                                        {"role": "user", "content": resumen_user},
                                    ],
                                    temperature=0.3,
                                    max_completion_tokens=4096,
                                    reasoning_effort="low",
                                    stream=True,
                                )
                                gen_resumen = stream_generator_clean(stream_resumen)
                                st.write_stream(gen_resumen)
                                finish_reason_resumen = getattr(stream_generator_clean, "_last_finish_reason", None)
                            except APITimeoutError:
                                st.warning("⚠️ **Tiempo de espera agotado** al generar la síntesis. Intenta de nuevo.")
                            except APIError as api_err:
                                msg_s = (api_err.message if hasattr(api_err, "message") else str(api_err))[:200]
                                if es_admin:
                                    st.warning(f"⚠️ **Error de API** al generar resumen: {msg_s}")
                                else:
                                    st.warning("⚠️ Error al generar la síntesis con el servicio de IA.")
                            except Exception as res_err:
                                if es_admin:
                                    st.warning(f"No fue posible generar el resumen automatizado: {res_err}")
                                else:
                                    st.warning("No fue posible generar el resumen automatizado.")
                            else:
                                if finish_reason_resumen and finish_reason_resumen != "stop":
                                    st.warning(
                                        f"⚠️ La síntesis estratégica terminó con estado `{finish_reason_resumen}` "
                                        "(posiblemente por límite de tokens o longitud). "
                                        "Los datos de la consulta arriba se muestran íntegros."
                                    )

    # -----------------------------------------------------------------------
    # TAB 2: Plan de Renovación
    # -----------------------------------------------------------------------
    with tab_renovacion:
        st.markdown("#### Plan de Renovación y Rotación Preventiva")
        st.write(
            "Detecta automáticamente todos los activos cuyo desgaste sea **≥ 80%** "
            "(estado crítico o expirado) y genera un plan de acción priorizado."
        )

        col_renov_btn, _ = st.columns([1, 3])
        generar_plan = col_renov_btn.button("📋 Analizar y Generar Plan", type="primary", width="stretch")

        if generar_plan:
            if not client:
                st.error("No se puede contactar a GroqCloud porque falta `GROQ_API_KEY`.")
            else:
                # --- Consulta SQL a Neon con aislamiento por rol ---
                # Si el usuario es administrador, consulta todos los activos del sistema.
                # Si es operador, la consulta se aísla estructuralmente a p.user_id = user_id_actual.
                filtro_usuario = "" if es_admin else f"AND p.user_id = {int(user_id_actual)}" if user_id_actual else "AND 1=0"

                query_criticos = f"""
                    SELECT
                        p.id,
                        p.nombre AS producto,
                        p.fecha_ingreso,
                        p.vida_min,
                        COALESCE(c.nombre, 'Sin Categoría')    AS categoria,
                        COALESCE(s.nombre, 'Sin Sede Asignada') AS sede,
                        COALESCE(d.serial, 'N/A')              AS serial,
                        COALESCE(d.marca,  'N/A')              AS marca,
                        ROUND(
                            (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp))
                             / 60.0 / NULLIF(p.vida_min, 0) * 100)::numeric,
                            2
                        ) AS pct_vida,
                        CASE
                            WHEN (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp))
                                  / 60.0 / NULLIF(p.vida_min, 0) * 100) >= 100
                            THEN 'Expirado'
                            ELSE 'Desgaste Crítico'
                        END AS estado_alerta
                    FROM productos p
                    LEFT JOIN categorias c ON p.cat_id  = c.id
                    LEFT JOIN sedes      s ON p.sede_id = s.id
                    LEFT JOIN detalles   d ON p.id      = d.prod_id
                    WHERE ROUND(
                        (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp))
                         / 60.0 / NULLIF(p.vida_min, 0) * 100)::numeric, 2
                    ) >= 80
                    {filtro_usuario}
                    ORDER BY pct_vida DESC;
                """

                df_criticos = None
                try:
                    with st.spinner("Consultando activos con desgaste ≥ 80% en Neon PostgreSQL…"):
                        df_criticos = _run_readonly_sql(query_criticos)
                except Exception as err_db:
                    if es_admin:
                        st.error(f"❌ Error al consultar activos críticos en Neon: {err_db}")
                    else:
                        st.error("❌ Error al consultar activos críticos.")

                if df_criticos is None:
                    pass  # error ya mostrado
                elif df_criticos.empty:
                    st.success(
                        "✅ **Excelente estado:** No se detectaron activos con desgaste ≥ 80%. "
                        "El inventario opera en condiciones óptimas."
                    )
                else:
                    n_total     = len(df_criticos)
                    n_expirados = int((pd.to_numeric(df_criticos["pct_vida"], errors="coerce").fillna(0) >= 100).sum())
                    n_criticos  = n_total - n_expirados

                    st.warning(
                        f"⚠️ Se detectaron **{n_total}** activo(s): "
                        f"**{n_expirados}** expirado(s) y **{n_criticos}** crítico(s) no expirado(s)."
                    )

                    # --- Sección 1: Resumen ejecutivo + Acciones (generado por modelo) ---
                    sedes_afectadas = (
                        df_criticos["sede"].value_counts().head(3).index.tolist()
                        if "sede" in df_criticos.columns else []
                    )

                    plan_system = (
                        "Eres un Director de Cadena de Suministro de nivel senior. "
                        "Redacta EXACTAMENTE las dos secciones indicadas a continuación, "
                        "sin introducción previa ni conclusión adicional. Sé breve y concreto.\n\n"
                        "### 📊 Resumen Ejecutivo\n"
                        "Exactamente 3 viñetas: (1) cantidad de expirados, "
                        "(2) cantidad de críticos no expirados, "
                        "(3) sedes más afectadas.\n\n"
                        "### 🎯 4 Acciones Operativas Clave\n"
                        "Exactamente 4 acciones numeradas, breves y concretas.\n\n"
                        "REGLA SANITARIA OBLIGATORIA: "
                        "Los productos expirados (>= 100% de vida consumida) DEBEN recibir "
                        "EXCLUSIVAMENTE 'Retiro y baja sanitaria'. "
                        "NUNCA recomiendes venderlos, descontarlos, incluirlos en combos ni donarlos. "
                        "Descuentos o rotación preventiva solo para críticos no expirados y solo "
                        "si su inocuidad y normativa aplicable lo permiten."
                    )

                    plan_user = (
                        f"Datos del inventario:\n"
                        f"- Expirados (pct_vida >= 100): {n_expirados}\n"
                        f"- Críticos no expirados (80 <= pct_vida < 100): {n_criticos}\n"
                        f"- Sedes más afectadas: {', '.join(sedes_afectadas) if sedes_afectadas else 'N/A'}\n\n"
                        "Genera el resumen ejecutivo y las 4 acciones operativas clave según las instrucciones."
                    )

                    # --- Sección 2: Tabla construida en Python (garantizada, sin truncamiento) ---
                    st.markdown("---")
                    st.markdown("### 📋 Tabla Única de Acción Priorizada")
                    df_tabla = _build_action_table(df_criticos)
                    st.dataframe(df_tabla, width="stretch", hide_index=True)

                    # --- Generar secciones de texto via streaming ---
                    st.markdown("---")

                    finish_reason_plan = None
                    try:
                        with st.spinner("Generando resumen y acciones con GroqCloud…"):
                            stream_plan = execute_groq_chat_completion(
                                client,
                                messages=[
                                    {"role": "system", "content": plan_system},
                                    {"role": "user", "content": plan_user},
                                ],
                                temperature=0.2,
                                max_completion_tokens=6000,
                                reasoning_effort="low",
                                stream=True,
                            )
                        # Consumir stream y capturar finish_reason
                        gen = stream_generator_clean(stream_plan)
                        st.write_stream(gen)
                        finish_reason_plan = getattr(stream_generator_clean, "_last_finish_reason", None)

                    except APITimeoutError:
                        st.warning(
                            "⚠️ **Tiempo de espera agotado** al generar el resumen del plan. "
                            "La tabla de datos está completa arriba."
                        )
                    except APIError as api_err:
                        msg_s = (api_err.message if hasattr(api_err, "message") else str(api_err))[:200]
                        if es_admin:
                            st.warning(f"⚠️ **Error de API** al generar el plan: {msg_s}")
                        else:
                            st.warning("⚠️ Error al generar el plan con el servicio de IA.")
                    except Exception as plan_err:
                        if es_admin:
                            st.error(f"❌ Error al generar el plan con GroqCloud: {plan_err}")
                        else:
                            st.error("❌ Error al generar el plan de rotación.")
                    else:
                        if finish_reason_plan and finish_reason_plan != "stop":
                            st.warning(
                                f"⚠️ La generación de texto terminó por `{finish_reason_plan}` "
                                "(posiblemente por límite de tokens). "
                                "La tabla de datos arriba está completa e íntegra."
                            )
