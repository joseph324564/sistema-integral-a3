import streamlit as st
import pandas as pd
import plotly.express as px
from modules.db_queries import get_kpis_usuario, get_inventario_usuario
from modules.reports import generar_excel_inventario, generar_pdf_inventario


def mostrar_dashboard():
    st.header("📊 Centro de Control Ejecutivo")
    st.caption("Visión panorámica y métricas de ciclo de vida de los activos corporativos.")

    user_id = st.session_state.get("user_id")
    if not user_id:
        st.warning("⚠️ Sesión no identificada. Inicie sesión nuevamente.")
        return

    kpis = get_kpis_usuario(user_id)
    df_inv = get_inventario_usuario(user_id)

    # Si el inventario está vacío, mostrar estado vacío instructivo
    if df_inv is None or df_inv.empty or kpis.get("total_activos", 0) == 0:
        st.info("ℹ️ **Tu inventario corporativo se encuentra vacío.**")
        with st.container(border=True):
            st.markdown("""
            ### 🚀 Comienza configurando tu infraestructura en 4 sencillos pasos:
            1. **Sedes Locales:** Dirígete a *Catálogos* o *Crear Sede* para registrar tus oficinas, bodegas o sucursales físicas.
            2. **Proveedores:** Registra las empresas y contactos oficiales de aprovisionamiento tecnológico.
            3. **Categorías:** Clasifica tus activos (p. ej., Laptops, Servidores, Dispositivos de Red, Periféricos).
            4. **Registrar Activo:** Vincula tu primer producto con su serial y ciclo de vida sugerido para activar el monitoreo inteligente en tiempo real.
            """)
        return

    # Barra de exportación de informes
    operador = st.session_state.get("username", f"ID-{user_id}")
    col_exp_info, col_exp_xls, col_exp_pdf = st.columns([0.6, 0.2, 0.2])
    with col_exp_info:
        st.caption(f"Operador activo: **{operador}** | Exportación de métricas y parque de activos")
    with col_exp_xls:
        excel_data = generar_excel_inventario(df_inv, kpis, operador)
        st.download_button(
            label="📊 Descargar Excel (.xlsx)",
            data=excel_data,
            file_name=f"reporte_ejecutivo_{user_id}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    with col_exp_pdf:
        pdf_data = generar_pdf_inventario(df_inv, kpis, operador)
        st.download_button(
            label="📄 Descargar PDF (.pdf)",
            data=pdf_data,
            file_name=f"reporte_ejecutivo_{user_id}.pdf",
            mime="application/pdf",
            width="stretch",
        )

    st.write("")

    # Fila 1: 4 tarjetas st.metric dentro de contenedores con borde
    total_activos = kpis.get("total_activos", 0)
    avg_vida = kpis.get("avg_vida_util_pct", 0.0)
    criticos = kpis.get("desgaste_critico", 0)
    expirados = kpis.get("expirados", 0)

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        with st.container(border=True):
            st.metric(label="Activos Totales", value=f"{total_activos}")
    with m2:
        with st.container(border=True):
            st.metric(label="Vida Útil Promedio", value=f"{avg_vida:.1f}%")
    with m3:
        with st.container(border=True):
            st.metric(label="En Desgaste Crítico (≥80%)", value=f"{criticos}", delta=f"{criticos} en alerta" if criticos > 0 else "0", delta_color="inverse")
    with m4:
        with st.container(border=True):
            st.metric(label="Activos Expirados (100%)", value=f"{expirados}", delta=f"{expirados} vencidos" if expirados > 0 else "0", delta_color="inverse")

    st.write("")

    # Fila 2: Dos columnas (60% / 40%) con gráficos interactivos de Plotly en tema oscuro
    col_izq, col_der = st.columns([0.6, 0.4])

    with col_izq:
        with st.container(border=True):
            st.subheader("🏢 Distribución de Activos por Sede")
            if "sd" in df_inv.columns:
                df_sedes = df_inv["sd"].value_counts().reset_index()
                df_sedes.columns = ["Sede", "Cantidad"]
                fig_bar = px.bar(
                    df_sedes,
                    x="Cantidad",
                    y="Sede",
                    orientation="h",
                    text="Cantidad",
                    color="Cantidad",
                    color_continuous_scale="Blues"
                )
                fig_bar.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=20, r=20, t=30, b=20),
                    showlegend=False,
                    yaxis=dict(autorange="reversed")
                )
                fig_bar.update_traces(textposition="outside")
                st.plotly_chart(fig_bar, width="stretch")
            else:
                st.write("Sin datos de sede.")

    with col_der:
        with st.container(border=True):
            st.subheader("🏷️ Distribución por Categoría")
            if "cat" in df_inv.columns:
                df_cats = df_inv["cat"].value_counts().reset_index()
                df_cats.columns = ["Categoría", "Cantidad"]
                fig_donut = px.pie(
                    df_cats,
                    names="Categoría",
                    values="Cantidad",
                    hole=0.55,
                    color_discrete_sequence=px.colors.sequential.Tealgrn_r
                )
                fig_donut.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=20, r=20, t=30, b=20)
                )
                fig_donut.update_traces(textinfo="percent+label")
                st.plotly_chart(fig_donut, width="stretch")
            else:
                st.write("Sin datos de categoría.")

    st.write("")

    # Fila 3: Panel de Alertas Prioritarias mostrando en tarjetas compactas los activos con pct_vida >= 80% y expirados
    st.subheader("🚨 Panel de Alertas Prioritarias")
    
    # Asegurar tipo numérico para pct_vida
    df_inv["pct_vida_num"] = pd.to_numeric(df_inv["pct_vida"], errors="coerce").fillna(0.0)
    df_alertas = df_inv[df_inv["pct_vida_num"] >= 80].copy()

    if df_alertas.empty:
        st.success("✅ Todo el parque de activos opera dentro de los rangos óptimos de vida útil (< 80%).")
    else:
        # Mostrar tarjetas compactas en filas de hasta 3 columnas
        cols = st.columns(3)
        for idx, (_, row) in enumerate(df_alertas.iterrows()):
            with cols[idx % 3]:
                es_expirado = row["pct_vida_num"] >= 100
                borde_color = "red" if es_expirado else "orange"
                badge = "🔴 EXPIRADO" if es_expirado else "🟡 DESGASTE CRÍTICO"

                with st.container(border=True):
                    st.markdown(f"**{badge}**")
                    st.markdown(f"#### {row.get('nombre', 'Activo')} ({row.get('marca', 'N/A')})")
                    st.markdown(f"- **Serial:** `{row.get('serial', 'S/N')}`")
                    st.markdown(f"- **Sede:** {row.get('sd', 'N/A')} | **Cat:** {row.get('cat', 'N/A')}")
                    st.markdown(f"- **Desgaste:** `{row['pct_vida_num']:.1f}%`")
                    st.progress(min(max(float(row['pct_vida_num']) / 100.0, 0.0), 1.0))
