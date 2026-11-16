import streamlit as st
import pandas as pd
from sqlalchemy import text

VISTAS_MATERIALIZADAS_VALIDAS = {
    "mv_usuarios_resumen",
    "mv_usuarios_contacto",
    "mv_categorias_conteo",
    "mv_categorias_listado",
    "mv_proveedores_nit",
    "mv_proveedores_contacto",
    "mv_sedes_resumen",
    "mv_sedes_conteo",
    "mv_productos_activos",
    "mv_productos_resumen",
    "mv_detalles_marcas",
    "mv_detalles_seriales",
    "mv_auditoria_log",
    "mv_auditoria_fechas",
}


@st.cache_data(ttl=60)
def get_inventario_usuario(user_id: int) -> pd.DataFrame:
    """Consulta el inventario del usuario calculando pct_vida y estado directamente en SQL."""
    conn = st.connection("neon", type="sql")
    query = """
        SELECT 
            p.*, 
            c.nombre AS cat, 
            s.nombre AS sd, 
            d.serial, 
            d.marca,
            ROUND(
                (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100)::numeric,
                2
            ) AS pct_vida,
            CASE 
                WHEN (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100) >= 100 THEN 'Expirado'
                WHEN (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100) >= 80 THEN 'Desgaste Crítico'
                ELSE 'Óptimo'
            END AS estado
        FROM productos p
        JOIN categorias c ON p.cat_id = c.id
        JOIN sedes s ON p.sede_id = s.id
        JOIN detalles d ON p.id = d.prod_id
        WHERE p.user_id = :user_id
        ORDER BY p.id DESC
    """
    return conn.query(query, params={"user_id": user_id})


@st.cache_data(ttl=60)
def get_kpis_usuario(user_id: int) -> dict:
    """Devuelve total de activos, promedio de vida útil %, cantidad en desgaste crítico (>=80%) y expirados (>=100%)."""
    conn = st.connection("neon", type="sql")
    query = """
        WITH calc AS (
            SELECT 
                (EXTRACT(EPOCH FROM (NOW() - p.fecha_ingreso::timestamp)) / 60.0 / NULLIF(p.vida_min, 0) * 100) AS pct
            FROM productos p
            WHERE p.user_id = :user_id
        )
        SELECT 
            COUNT(*)::int AS total_activos,
            COALESCE(ROUND(AVG(pct)::numeric, 2), 0) AS avg_vida_util_pct,
            COUNT(*) FILTER (WHERE pct >= 80 AND pct < 100)::int AS desgaste_critico,
            COUNT(*) FILTER (WHERE pct >= 100)::int AS expirados
        FROM calc
    """
    df = conn.query(query, params={"user_id": user_id})
    if not df.empty:
        return df.iloc[0].to_dict()
    return {
        "total_activos": 0,
        "avg_vida_util_pct": 0.0,
        "desgaste_critico": 0,
        "expirados": 0,
    }


@st.cache_data(ttl=60)
def get_vista_materializada(nombre_vista: str) -> pd.DataFrame:
    """Valida el nombre contra lista blanca de 14 vistas materializadas y consulta hasta 500 registros."""
    if nombre_vista not in VISTAS_MATERIALIZADAS_VALIDAS:
        raise ValueError(
            f"Vista materializada '{nombre_vista}' no permitida. Vistas válidas: {sorted(VISTAS_MATERIALIZADAS_VALIDAS)}"
        )
    conn = st.connection("neon", type="sql")
    query = f"SELECT * FROM {nombre_vista} LIMIT 500"
    return conn.query(query)


def invalidar_cache_y_refrescar() -> None:
    """Limpia el caché de Streamlit e intenta refrescar concurrentemente vistas materializadas clave."""
    st.cache_data.clear()
    
    vistas_a_refrescar = [
        "mv_productos_activos",
        "mv_productos_resumen",
        "mv_categorias_conteo",
        "mv_sedes_conteo",
    ]
    
    conn = st.connection("neon", type="sql")
    with conn.session as session:
        for vista in vistas_a_refrescar:
            try:
                # Neon Postgres: requiere índice único para REFRESH MATERIALIZED VIEW CONCURRENTLY
                session.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {vista}"))
                session.commit()
            except Exception:
                session.rollback()
                try:
                    # Fallback opcional a refresco estándar si falta el índice único
                    session.execute(text(f"REFRESH MATERIALIZED VIEW {vista}"))
                    session.commit()
                except Exception:
                    session.rollback()
