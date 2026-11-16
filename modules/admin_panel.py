import re
import datetime
from typing import Tuple
import streamlit as st
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from database.connection import get_connection

# Rol canónico de administrador verificado en base de datos
ROL_ADMIN = "admin"
ROLES_PERMITIDOS = ("admin", "operador")

IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validar_autorizacion_admin() -> Tuple[bool, int | None, str | None]:
    """
    Comprobación central de autorización.
    Exige sesión autenticada, user_id válido y rol de administrador confirmado.
    Devuelve: (es_valido, user_id, username)
    """
    if not st.session_state.get("logged_in", False):
        return False, None, None

    user_id = st.session_state.get("user_id")
    if not user_id or not isinstance(user_id, int):
        return False, None, None

    rol = str(st.session_state.get("rol", "")).strip().lower()
    if rol != ROL_ADMIN:
        return False, None, None

    username = st.session_state.get("username", f"user_{user_id}")
    return True, user_id, str(username)


def registrar_auditoria(user_id: int, accion: str, cur=None) -> bool:
    """
    Registra un evento de auditoría en la tabla real `auditoria`.
    Columnas reales verificadas: (user_id, accion, fecha_hora).
    fecha_hora se almacena en formato timestamp 'YYYY-MM-DD HH:MM:SS'.
    """
    ahora_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = "INSERT INTO auditoria (user_id, accion, fecha_hora) VALUES (%s, %s, %s)"

    if cur is not None:
        cur.execute(sql, (user_id, accion, ahora_str))
        return True

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (user_id, accion, ahora_str))
            conn.commit()
        return True
    except Exception:
        return False


def _limpiar_caches_admin():
    """Limpia la caché de datos de Streamlit y el esquema cacheado del Copiloto."""
    st.cache_data.clear()
    try:
        from modules.ai_copilot import get_mv_schema_description
        get_mv_schema_description.clear()
    except Exception:
        pass


def mostrar_panel_admin():
    """
    Consola administrativa segura.
    Verifica autorización previa antes de renderizar cualquier dato o ejecutar acciones.
    """
    autorizado, actor_id, actor_name = validar_autorizacion_admin()
    if not autorizado or actor_id is None:
        st.error("⛔ Acceso denegado: Se requieren credenciales de Administrador para ver esta sección.")
        return

    st.header("⚙️ Consola de Administración del Sistema")
    st.caption(f"Sesión administrativa activa: **{actor_name}** (ID: `{actor_id}`)")

    tab_vistas, tab_usuarios, tab_auditoria = st.tabs([
        "🔄 Vistas Materializadas (BI & IA)",
        "👥 Usuarios y Roles",
        "📜 Auditoría del Sistema",
    ])

    # -------------------------------------------------------------------------
    # PESTAÑA 1: Vistas Materializadas (BI & IA)
    # -------------------------------------------------------------------------
    with tab_vistas:
        st.subheader("🔄 Gestión y Refresco de Vistas Materializadas")
        st.write(
            "Las vistas materializadas consolidan métricas clave para el módulo de BI "
            "y el Copiloto de IA sobre Neon PostgreSQL."
        )

        vistas_disponibles = []
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT matviewname 
                        FROM pg_matviews 
                        WHERE schemaname = 'public' AND left(matviewname, 3) = 'mv_'
                        ORDER BY matviewname;
                    """)
                    vistas_disponibles = [r["matviewname"] for r in cur.fetchall()]
        except Exception as e:
            st.error(f"Error al consultar el catálogo de vistas materializadas: {e}")

        if vistas_disponibles:
            st.info(f"Se encontraron **{len(vistas_disponibles)}** vistas materializadas oficiales (`mv_*`) en el esquema público.")
            with st.expander("Ver lista de vistas materializadas detectadas"):
                st.write(", ".join([f"`{v}`" for v in vistas_disponibles]))
        else:
            st.warning("No se encontraron vistas materializadas con prefijo `mv_` en el esquema público.")

        st.warning(
            "⚠️ **Advertencia de rendimiento:** El refresco síncrono de las vistas materializadas "
            "actualiza los datos consolidados y puede tomar varios segundos, bloqueando temporalmente "
            "las lecturas de las vistas afectadas."
        )

        confirmar_refresco = st.checkbox(
            "He leído la advertencia y confirmo el refresco de las vistas materializadas.",
            key="chk_confirm_refresh"
        )

        btn_refrescar = st.button(
            "⚡ Iniciar Refresco de Vistas Materializadas",
            type="primary",
            disabled=not (confirmar_refresco and bool(vistas_disponibles)),
            width="stretch"
        )

        if btn_refrescar:
            # Revalidar autorización del actor
            autorizado_run, actor_id_run, _ = validar_autorizacion_admin()
            if not autorizado_run or actor_id_run is None:
                st.error("⛔ Acción cancelada: Sesión no autorizada.")
                st.stop()

            st.write("---")
            st.write("#### ⏳ Ejecución de Refresco por Vista")
            progress_bar = st.progress(0.0)
            status_text = st.empty()

            vistas_exitosas = []
            vistas_fallidas = []

            for idx, vista_nombre in enumerate(vistas_disponibles):
                status_text.text(f"Procesando ({idx + 1}/{len(vistas_disponibles)}): {vista_nombre}...")

                # Validación estricta del identificador SQL
                if not IDENTIFIER_REGEX.match(vista_nombre):
                    vistas_fallidas.append((vista_nombre, "Nombre de vista inválido (riesgo de inyección)"))
                    continue

                refresh_sql = f'REFRESH MATERIALIZED VIEW public."{vista_nombre}";'

                try:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(refresh_sql)
                        conn.commit()
                    vistas_exitosas.append(vista_nombre)
                except Exception as err:
                    vistas_fallidas.append((vista_nombre, str(err)[:200]))

                progress_bar.progress((idx + 1) / len(vistas_disponibles))

            status_text.empty()

            # Registrar en auditoria el resultado
            resumen_accion = (
                f"Refresco de vistas materializadas: {len(vistas_exitosas)} exitosas, "
                f"{len(vistas_fallidas)} fallidas de {len(vistas_disponibles)} totales."
            )
            audit_ok = registrar_auditoria(actor_id_run, resumen_accion)

            # Invalidar caché solo si hubo al menos un refresco exitoso
            if vistas_exitosas:
                _limpiar_caches_admin()

            # Reportar resultados
            st.write("---")
            if vistas_exitosas:
                st.success(f"✅ Se refrescaron exitosamente {len(vistas_exitosas)} vista(s): " + ", ".join([f"`{v}`" for v in vistas_exitosas]))
            if vistas_fallidas:
                st.error(f"❌ Falló el refresco de {len(vistas_fallidas)} vista(s):")
                for v_nombre, v_err in vistas_fallidas:
                    st.caption(f"- `{v_nombre}`: {v_err}")

            if not audit_ok:
                st.warning("⚠️ Nota de Auditoría: No fue posible guardar el registro de auditoría en la base de datos.")
            else:
                st.info("📜 Registro de auditoría guardado satisfactoriamente.")

    # -------------------------------------------------------------------------
    # PESTAÑA 2: Usuarios y Roles
    # -------------------------------------------------------------------------
    with tab_usuarios:
        st.subheader("👥 Gestión de Cuentas y Asignación de Roles")
        st.write("Administración centralizada de usuarios del sistema. Consulta y modifica privilegios de acceso.")

        # Advertencia explícita sobre columna 'activo' inexistente en el esquema
        st.caption("ℹ️ *Nota de arquitectura:* El esquema actual no cuenta con columna `activo`/`inactivo`. La gestión se realiza mediante el rol asignado (`admin` / `operador`).")

        df_usuarios = None
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Lista explícita de columnas NO sensibles (sin pw ni otp_secret)
                    cur.execute("""
                        SELECT id, "user" AS username, correo, telefono, COALESCE(rol, 'operador') AS rol
                        FROM usuarios
                        ORDER BY id ASC;
                    """)
                    filas = cur.fetchall()
                    df_usuarios = pd.DataFrame(filas)
        except Exception as e:
            st.error(f"Error al consultar la lista de usuarios: {e}")

        if df_usuarios is not None and not df_usuarios.empty:
            st.dataframe(df_usuarios, width="stretch", hide_index=True)

            st.write("---")
            st.write("#### 🛡️ Modificar Rol de Usuario")

            lista_users = [
                f"ID {r['id']} - {r['username']} (Rol actual: {r['rol']})"
                for _, r in df_usuarios.iterrows()
            ]

            usuario_sel_str = st.selectbox("Seleccione el usuario a gestionar:", lista_users)
            user_target_id = int(usuario_sel_str.split()[1])
            usuario_row = df_usuarios[df_usuarios["id"] == user_target_id].iloc[0]
            rol_actual = str(usuario_row["rol"]).strip().lower()

            nuevo_rol = st.selectbox(
                "Nuevo Rol a asignar:",
                options=list(ROLES_PERMITIDOS),
                index=0 if rol_actual == "admin" else 1
            )

            confirmar_rol = st.checkbox(
                f"Confirmo que deseo cambiar el rol del usuario '{usuario_row['username']}' (ID: {user_target_id}) a '{nuevo_rol}'.",
                key="chk_confirm_rol"
            )

            if st.button("💾 Guardar Cambio de Rol", type="primary", disabled=not confirmar_rol):
                # 1. Revalidar autorización del actor
                autorizado_actor, actor_id_actor, _ = validar_autorizacion_admin()
                if not autorizado_actor or actor_id_actor is None:
                    st.error("⛔ Acción cancelada: Sesión no autorizada.")
                    st.stop()

                # 2. Impedir que el administrador actual se degrade a sí mismo
                if user_target_id == actor_id_actor and nuevo_rol != ROL_ADMIN:
                    st.error("⛔ Operación bloqueada: No puedes retirar tu propio rol de administrador.")
                    st.stop()

                # 3. Transacción atómica: verificar último admin, actualizar y auditar
                try:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            # Contar administradores actuales con bloqueo FOR UPDATE
                            cur.execute("SELECT id FROM usuarios WHERE LOWER(TRIM(rol)) = %s FOR UPDATE;", (ROL_ADMIN,))
                            admins_existentes = [r["id"] for r in cur.fetchall()]

                            if rol_actual == ROL_ADMIN and nuevo_rol != ROL_ADMIN:
                                if len(admins_existentes) <= 1 and user_target_id in admins_existentes:
                                    st.error("⛔ Operación bloqueada: No se puede retirar al único administrador del sistema.")
                                    return

                            # Actualizar rol
                            cur.execute(
                                'UPDATE usuarios SET rol = %s WHERE id = %s;',
                                (nuevo_rol, user_target_id)
                            )

                            # Registrar auditoría en la MISMA transacción
                            accion_audit = (
                                f"Cambio de rol de usuario ID {user_target_id} ({usuario_row['username']}): "
                                f"'{rol_actual}' -> '{nuevo_rol}' por admin ID {actor_id_actor}."
                            )
                            ahora_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            cur.execute(
                                "INSERT INTO auditoria (user_id, accion, fecha_hora) VALUES (%s, %s, %s);",
                                (actor_id_actor, accion_audit, ahora_str)
                            )

                        conn.commit()

                    st.success(f"✅ Rol actualizado exitosamente para el usuario '{usuario_row['username']}' a '{nuevo_rol}'.")
                    _limpiar_caches_admin()
                    st.rerun()

                except Exception as e:
                    st.error(f"❌ Error al actualizar rol (la transacción fue revertida): {e}")

        elif df_usuarios is not None and df_usuarios.empty:
            st.info("No se encontraron registros de usuarios.")

    # -------------------------------------------------------------------------
    # PESTAÑA 3: Auditoría del Sistema
    # -------------------------------------------------------------------------
    with tab_auditoria:
        st.subheader("📜 Registro y Bitácora de Auditoría")
        st.write("Visualización de los eventos del sistema registrados en la tabla de auditoría (máximo 200 eventos).")

        col_filtro, _ = st.columns([2, 1])
        with col_filtro:
            filtro_texto = st.text_input("🔍 Filtrar por acción o nombre de actor:", placeholder="Ej: lácteos, admin, retiro...")

        df_auditoria = None
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    if filtro_texto and filtro_texto.strip():
                        param_like = f"%{filtro_texto.strip()}%"
                        cur.execute("""
                            SELECT 
                                a.id,
                                a.user_id,
                                COALESCE(u."user", 'Sistema/Desconocido') AS actor,
                                a.accion,
                                a.fecha_hora
                            FROM auditoria a
                            LEFT JOIN usuarios u ON a.user_id = u.id
                            WHERE a.accion ILIKE %s OR u."user" ILIKE %s
                            ORDER BY a.id DESC
                            LIMIT 200;
                        """, (param_like, param_like))
                    else:
                        cur.execute("""
                            SELECT 
                                a.id,
                                a.user_id,
                                COALESCE(u."user", 'Sistema/Desconocido') AS actor,
                                a.accion,
                                a.fecha_hora
                            FROM auditoria a
                            LEFT JOIN usuarios u ON a.user_id = u.id
                            ORDER BY a.id DESC
                            LIMIT 200;
                        """)
                    filas_audit = cur.fetchall()
                    df_auditoria = pd.DataFrame(filas_audit)
        except Exception as e:
            st.error(f"Error al consultar la bitácora de auditoría: {e}")

        if df_auditoria is not None and not df_auditoria.empty:
            st.caption(f"Mostrando {len(df_auditoria)} evento(s) reciente(s).")
            st.dataframe(
                df_auditoria.rename(columns={
                    "id": "ID",
                    "user_id": "ID Actor",
                    "actor": "Usuario / Actor",
                    "accion": "Acción Registrada",
                    "fecha_hora": "Fecha y Hora"
                }),
                width="stretch",
                hide_index=True
            )
        elif df_auditoria is not None and df_auditoria.empty:
            st.info("No se encontraron registros de auditoría que coincidan con la búsqueda.")
