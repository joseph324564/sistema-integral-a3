import streamlit as st
import pandas as pd
from datetime import datetime
from database.connection import get_connection

def mostrar_inventario():
    st.subheader("📋 Auditoría de Activos del Operador")
    
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT p.*, c.nombre as cat, s.nombre as sd, d.serial, d.marca 
            FROM productos p 
            JOIN categorias c ON p.cat_id = c.id 
            JOIN sedes s ON p.sede_id = s.id 
            JOIN detalles d ON p.id = d.prod_id 
            WHERE p.user_id = %s""", conn, params=(st.session_state.user_id,))
            
    if df.empty:
        st.info("Tu inventario está vacío. Los datos de otros usuarios están protegidos.")
        return

    st.dataframe(df[['nombre', 'cat', 'sd', 'marca', 'serial', 'fecha_ingreso']], use_container_width=True)

    for _, r in df.iterrows():
        ing = datetime.strptime(r['fecha_ingreso'], "%Y-%m-%d %H:%M:%S")
        pct = ((datetime.now()-ing).total_seconds()/60/r['vida_min'])*100
        
        with st.expander(f"📦 {r['nombre'].upper()} ({r['marca']})"):
            st.write(f"**Ubicación:** {r['sd']} | **Serial:** `{r['serial']}`")
            st.progress(min(max(pct/100, 0.0), 1.0))

def gestionar_eliminacion():
    st.subheader("🗑️ Consola de Depuración de Datos")
    tab1, tab2, tab3, tab4 = st.tabs(["Productos", "Categorías", "Proveedores", "Sedes"])
    
    with get_connection() as conn:
        with tab1:
            prods = pd.read_sql("SELECT id, nombre FROM productos WHERE user_id = %s", conn, params=(st.session_state.user_id,))
            if not prods.empty:
                p_del = st.selectbox("Selecciona tu Producto", prods['nombre'], key="del_p")
                if st.button("Eliminar mi Producto", use_container_width=True, type="primary"):
                    p_id = int(prods[prods['nombre']==p_del]['id'].iloc[0])
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM productos WHERE id = %s", (p_id,))
                    conn.commit()
                    st.rerun()
            else: st.write("No tienes productos registrados.")
            
        with tab2:
            cats = pd.read_sql("SELECT id, nombre FROM categorias WHERE user_id = %s", conn, params=(st.session_state.user_id,))
            if not cats.empty:
                c_del = st.selectbox("Selecciona tu Categoría", cats['nombre'], key="del_c")
                if st.button("Eliminar mi Categoría", use_container_width=True, type="primary"):
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM categorias WHERE nombre = %s AND user_id = %s", (c_del, st.session_state.user_id))
                    conn.commit()
                    st.rerun()
            else: st.write("No tienes categorías registradas.")

        with tab3:
            provs = pd.read_sql("SELECT id, nombre FROM proveedores WHERE user_id = %s", conn, params=(st.session_state.user_id,))
            if not provs.empty:
                pr_del = st.selectbox("Selecciona tu Proveedor", provs['nombre'], key="del_pr")
                if st.button("Eliminar mi Proveedor", use_container_width=True, type="primary"):
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM proveedores WHERE nombre = %s AND user_id = %s", (pr_del, st.session_state.user_id))
                    conn.commit()
                    st.rerun()
            else: st.write("No tienes proveedores registrados.")

        with tab4:
            seds = pd.read_sql("SELECT id, nombre FROM sedes WHERE user_id = %s", conn, params=(st.session_state.user_id,))
            if not seds.empty:
                s_del = st.selectbox("Selecciona tu Sede", seds['nombre'], key="del_s")
                if st.button("Eliminar mi Sede", use_container_width=True, type="primary"):
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM sedes WHERE nombre = %s AND user_id = %s", (s_del, st.session_state.user_id))
                    conn.commit()
                    st.rerun()
            else: st.write("No tienes sedes registradas.")