import streamlit as st
import pandas as pd
from database.connection import init_db, get_connection
from modules.auth import mostrar_login
from modules.registrar import formulario_producto, formulario_categoria, formulario_proveedor, formulario_sede
from modules.inventory import mostrar_inventario, gestionar_eliminacion
from modules.notifications import mostrar_notificaciones

st.set_page_config(page_title="Sistema Integral A3", page_icon="🛡️", layout="wide")

# Inicialización de la base de datos
init_db()

if 'logged_in' not in st.session_state: 
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    mostrar_login()
else:
    st.sidebar.markdown(f"### 👤 Operador Autenticado ID: `{st.session_state.user_id}`")
    st.sidebar.write("---")
    
    opcion = st.sidebar.selectbox("🎯 MENÚ TÉCNICO", [
        "📋 Ver mi Inventario",
        "🔔 Centro de Notificaciones",
        "📝 Registrar Producto", 
        "➕ Crear Categoría", 
        "➕ Crear Proveedor", 
        "➕ Crear Sede",
        "🗑️ Gestionar / Eliminar",
        "🖥️ Consola DB (Profesor)"
    ])
    
    st.sidebar.write("---")
    if st.sidebar.button("🔒 Cerrar Sesión", use_container_width=True):
        st.session_state.clear() 
        st.rerun()

    # Enrutador técnico de módulos
    if opcion == "📋 Ver mi Inventario": 
        mostrar_inventario()
        
    elif opcion == "🔔 Centro de Notificaciones": 
        mostrar_notificaciones()
        
    elif opcion == "📝 Registrar Producto": 
        formulario_producto()
        
    elif opcion == "➕ Crear Categoría": 
        formulario_categoria()
        
    elif opcion == "➕ Crear Proveedor": 
        formulario_proveedor()
        
    elif opcion == "➕ Crear Sede": 
        formulario_sede()
        
    elif opcion == "🗑️ Gestionar / Eliminar": 
        gestionar_eliminacion()
        
    elif opcion == "🖥️ Consola DB (Profesor)":
        st.header("🖥️ Centro de Control y Auditoría de Base de Datos")
        st.write("Herramientas avanzadas de inspección del motor relacional PostgreSQL en la nube (Neon).")
        
        # Validar credencial maestra solicitada por el usuario
        clave_maestra = st.text_input("🔑 Ingrese la clave de acceso de auditoría:", type="password")
        
        if clave_maestra == "Admin343":
            st.success("🔓 Acceso de auditoría concedido. Modo Administrador Activo.")
            st.write("---")
            
            pestana1, pestana2, pestana3 = st.tabs([
                "📊 Consultas Rápidas (7 Tablas)", 
                "🔍 Vistas Materializadas", 
                "📐 Metadatos PostgreSQL"
            ])
            
            # PESTAÑA 1: SELECT FROM 7 TABLAS
            with pestana1:
                st.subheader("📋 Consultor Dinámico de las 7 Tablas")
                tabla_seleccionada = st.selectbox(
                    "Seleccione la tabla física a consultar en caliente:", 
                    ["usuarios", "categorias", "proveedores", "sedes", "productos", "detalles", "auditoria"]
                )
                
                with get_connection() as conn:
                    try:
                        df_query = pd.read_sql(f"SELECT * FROM {tabla_seleccionada} LIMIT 50", conn)
                        st.markdown(f"**Query ejecutado internamente:** `SELECT * FROM {tabla_seleccionada} LIMIT 50;`")
                        if df_query.empty:
                            st.info(f"La tabla '{tabla_seleccionada}' se encuentra estructurada pero actualmente no contiene registros.")
                        else:
                            st.dataframe(df_query, use_container_width=True)
                    except Exception as e:
                        st.error(f"Error en la consulta: {e}")
            
            # PESTAÑA 2: VISTAS MATERIALIZADAS
            with pestana2:
                st.subheader("🔍 Inspección de Vistas Materializadas")
                st.write("Demostración formal del uso de Vistas Materializadas en PostgreSQL para optimizar consultas complejas.")
                
                vista_seleccionada = st.selectbox(
                    "Seleccione la Vista Materializada a consultar:",
                    [
                        "mv_usuarios_resumen", "mv_usuarios_contacto",
                        "mv_categorias_conteo", "mv_categorias_listado",
                        "mv_proveedores_nit", "mv_proveedores_contacto",
                        "mv_sedes_resumen", "mv_sedes_conteo",
                        "mv_productos_activos", "mv_productos_resumen",
                        "mv_detalles_marcas", "mv_detalles_seriales",
                        "mv_auditoria_log", "mv_auditoria_fechas"
                    ]
                )
                
                if st.button("Consultar Vista Materializada", type="primary"):
                    with get_connection() as conn:
                        try:
                            df_vista = pd.read_sql(f"SELECT * FROM {vista_seleccionada} LIMIT 50", conn)
                            st.markdown(f"**Query ejecutado internamente:** `SELECT * FROM {vista_seleccionada} LIMIT 50;`")
                            st.dataframe(df_vista, use_container_width=True)
                        except Exception as e:
                            st.error(f"Error al leer la vista materializada: {e}")
                            
            # PESTAÑA 3: METADATOS DE POSTGRESQL (INFORMATION_SCHEMA)
            with pestana3:
                st.subheader("📐 Diccionario de Datos e Información del Sistema")
                st.write("Inspección de la arquitectura relacional basada en los metadatos oficiales de PostgreSQL.")
                
                with get_connection() as conn:
                    try:
                        df_tablas = pd.read_sql("""
                            SELECT table_name 
                            FROM information_schema.tables 
                            WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
                        """, conn)
                        st.markdown("#### 📊 Tablas oficiales registradas en el esquema público")
                        st.dataframe(df_tablas, use_container_width=True)
                        
                        df_vistas_mat = pd.read_sql("""
                            SELECT matviewname FROM pg_matviews;
                        """, conn)
                        st.markdown("#### 📦 Vistas Materializadas activas")
                        st.dataframe(df_vistas_mat, use_container_width=True)
                            
                    except Exception as e:
                        st.error(f"Error al procesar metadatos de PostgreSQL: {e}")
                        
        elif clave_maestra != "":
            st.error("❌ Clave de auditoría incorrecta. Acceso denegado a los datos del sistema.")