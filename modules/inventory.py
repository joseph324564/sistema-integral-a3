import streamlit as st
import pandas as pd
from database.connection import get_connection
from modules.db_queries import (
    get_inventario_usuario,
    get_kpis_usuario,
    invalidar_cache_y_refrescar,
)
from modules.reports import generar_excel_inventario, generar_pdf_inventario
from modules.registrar import formulario_producto


@st.dialog("➕ Registrar Nuevo Activo")
def modal_nuevo_activo():
    formulario_producto()


def mostrar_inventario():
    st.header("📋 Gestión e Inventario de Activos")
    st.caption("Inspección de ciclo de vida en tiempo real, filtros avanzados y control de activos.")

    user_id = st.session_state.get("user_id")
    if not user_id:
        st.warning("⚠️ Sesión no identificada. Inicie sesión nuevamente.")
        return

    # Barra superior de filtros rápidos y botón de acción
    col_search, col_sede, col_estado, col_btn = st.columns([0.35, 0.25, 0.22, 0.18])

    df = get_inventario_usuario(user_id)

    if df is None:
        df = pd.DataFrame()

    with col_btn:
        st.write("")
        if st.button("➕ Nuevo Activo", type="primary", width="stretch"):
            modal_nuevo_activo()

    sedes_disponibles = sorted(list(df["sd"].dropna().unique())) if not df.empty and "sd" in df.columns else []

    with col_search:
        filtro_texto = st.text_input("🔍 Buscar", placeholder="Nombre o serial...", label_visibility="collapsed")

    with col_sede:
        filtro_sedes = st.multiselect(
            "Filtrar por Sede",
            options=sedes_disponibles,
            placeholder="Todas las sedes",
            label_visibility="collapsed"
        )

    with col_estado:
        filtro_estado = st.selectbox(
            "Estado",
            options=["Todos", "Óptimo", "Desgaste Crítico", "Expirado"],
            label_visibility="collapsed"
        )

    if df.empty:
        st.info("ℹ️ Tu inventario está vacío. Haz clic en **'+ Nuevo Activo'** o accede a **Catálogos** para comenzar.")
        return

    # Normalizar pct_vida a número flotante
    df["pct_vida"] = pd.to_numeric(df["pct_vida"], errors="coerce").fillna(0.0)

    # Aplicar filtros
    df_filtrado = df.copy()

    if filtro_texto:
        texto_busqueda = filtro_texto.strip().lower()
        cond_nom = df_filtrado["nombre"].astype(str).str.lower().str.contains(texto_busqueda)
        cond_ser = df_filtrado["serial"].astype(str).str.lower().str.contains(texto_busqueda)
        cond_mar = df_filtrado["marca"].astype(str).str.lower().str.contains(texto_busqueda)
        df_filtrado = df_filtrado[cond_nom | cond_ser | cond_mar]

    if filtro_sedes:
        df_filtrado = df_filtrado[df_filtrado["sd"].isin(filtro_sedes)]

    if filtro_estado != "Todos":
        df_filtrado = df_filtrado[df_filtrado["estado"] == filtro_estado]

    # Barra superior de exportación de informes
    operador = st.session_state.get("username", f"ID-{user_id}")
    kpis_usuario = get_kpis_usuario(user_id)
    # Recalcular KPIs si hay filtros activos para que el reporte refleje la selección actual
    if len(df_filtrado) != len(df):
        pct_series = pd.to_numeric(df_filtrado["pct_vida"], errors="coerce").fillna(0.0)
        kpis_reporte = {
            "total_activos": len(df_filtrado),
            "avg_vida_util_pct": round(float(pct_series.mean()), 2) if not df_filtrado.empty else 0.0,
            "desgaste_critico": int(((pct_series >= 80) & (pct_series < 100)).sum()),
            "expirados": int((pct_series >= 100).sum()),
        }
    else:
        kpis_reporte = kpis_usuario

    col_exp_info, col_exp_xls, col_exp_pdf = st.columns([0.6, 0.2, 0.2])
    with col_exp_info:
        st.caption(f"Mostrando **{len(df_filtrado)}** de **{len(df)}** activos | Operador: **{operador}**")
    with col_exp_xls:
        excel_data = generar_excel_inventario(df_filtrado, kpis_reporte, operador)
        st.download_button(
            label="📊 Descargar Excel (.xlsx)",
            data=excel_data,
            file_name=f"inventario_activos_{user_id}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    with col_exp_pdf:
        pdf_data = generar_pdf_inventario(df_filtrado, kpis_reporte, operador)
        st.download_button(
            label="📄 Descargar PDF (.pdf)",
            data=pdf_data,
            file_name=f"inventario_activos_{user_id}.pdf",
            mime="application/pdf",
            width="stretch",
        )

    st.write("")

    # División en 2 columnas: 70% Izquierda (st.dataframe) / 30% Derecha (Panel de inspección)
    col_tabla, col_panel = st.columns([0.7, 0.3])

    with col_tabla:
        columnas_visibles = ["id", "nombre", "cat", "sd", "marca", "serial", "pct_vida", "estado"]
        # Filtrar solo columnas presentes
        columnas_df = [c for c in columnas_visibles if c in df_filtrado.columns]

        event = st.dataframe(
            df_filtrado[columnas_df],
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "id": st.column_config.NumberColumn("ID", format="%d", width="small"),
                "nombre": st.column_config.TextColumn("Activo", width="medium"),
                "cat": st.column_config.TextColumn("Categoría", width="small"),
                "sd": st.column_config.TextColumn("Sede", width="small"),
                "marca": st.column_config.TextColumn("Marca", width="small"),
                "serial": st.column_config.TextColumn("Serial", width="medium"),
                "pct_vida": st.column_config.ProgressColumn(
                    "Vida Consumida",
                    help="Porcentaje del ciclo de vida consumido",
                    format="%.1f%%",
                    min_value=0.0,
                    max_value=100.0,
                    width="medium"
                ),
                "estado": st.column_config.TextColumn("Estado", width="small")
            }
        )

    with col_panel:
        with st.container(border=True):
            st.subheader("🔍 Detalle del Activo")

            filas_seleccionadas = event.selection.rows if event and hasattr(event, "selection") else []

            if filas_seleccionadas:
                indice_fila = filas_seleccionadas[0]
                activo = df_filtrado.iloc[indice_fila]

                st.markdown(f"### {activo.get('nombre', 'Activo')}")
                st.markdown(f"- **ID:** `{activo.get('id', 'N/A')}`")
                st.markdown(f"- **Serial:** `{activo.get('serial', 'N/A')}`")
                st.markdown(f"- **Marca:** {activo.get('marca', 'N/A')}")
                st.markdown(f"- **Sede:** {activo.get('sd', 'N/A')}")
                st.markdown(f"- **Categoría:** {activo.get('cat', 'N/A')}")
                st.markdown(f"- **Fecha Ingreso:** {activo.get('fecha_ingreso', 'N/A')}")
                st.markdown(f"- **Estado:** `{activo.get('estado', 'N/A')}`")

                pct = float(activo.get("pct_vida", 0.0))
                st.progress(min(max(pct / 100.0, 0.0), 1.0))

                st.write("---")
                # Confirmación y eliminación de activo
                confirmar = st.checkbox(f"Confirmar eliminación del ID {activo.get('id')}", key="check_del_prod")
                if st.button("🗑️ Eliminar Activo", type="primary", width="stretch", disabled=not confirmar):
                    prod_id = int(activo["id"])
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                # Borrar en cascada o primero detalles
                                cur.execute("DELETE FROM detalles WHERE prod_id = %s", (prod_id,))
                                cur.execute("DELETE FROM productos WHERE id = %s AND user_id = %s", (prod_id, user_id))
                            conn.commit()
                        invalidar_cache_y_refrescar()
                        st.success(f"Activo #{prod_id} eliminado exitosamente.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al eliminar activo: {e}")
            else:
                st.info("👈 Haz clic en una fila de la tabla para inspeccionar sus especificaciones técnicas y opciones de gestión.")


def gestionar_eliminacion():
    """Función de depuración mantenida para compatibilidad."""
    from modules.registrar import mostrar_catalogos
    mostrar_catalogos()
