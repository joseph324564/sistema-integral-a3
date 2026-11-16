import streamlit as st
import pandas as pd
from database.connection import init_db
from modules.auth import mostrar_login
from modules.dashboard import mostrar_dashboard
from modules.registrar import formulario_producto, mostrar_catalogos, formulario_categoria, formulario_proveedor, formulario_sede
from modules.inventory import mostrar_inventario, gestionar_eliminacion
from modules.notifications import mostrar_notificaciones
from modules.ai_copilot import mostrar_copiloto_ia
from modules.admin_panel import mostrar_panel_admin, validar_autorizacion_admin

st.set_page_config(page_title="Sistema Integral A3", page_icon="🛡️", layout="wide")

# Inicialización de la base de datos
init_db()

if 'logged_in' not in st.session_state: 
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    mostrar_login()
else:
    # Verificación de autorización de administrador
    es_admin, user_id, username = validar_autorizacion_admin()

    if es_admin:
        st.sidebar.markdown(f"### 🛡️ Administrador: `{username}` (ID: `{user_id}`)")
    else:
        rol_display = str(st.session_state.get("rol", "operador")).strip()
        user_display = st.session_state.get("username", f"ID {st.session_state.get('user_id')}")
        st.sidebar.markdown(f"### 👤 {rol_display.capitalize()}: `{user_display}`")

    st.sidebar.write("---")

    if st.sidebar.button("🤖 Copiloto de IA", type="primary", width="stretch"):
        mostrar_copiloto_ia()

    st.sidebar.write("---")

    # Opciones de menú según el rol verificado
    menu_opciones = [
        "📊 Centro de Control",
        "📋 Ver mi Inventario",
        "🗂️ Catálogos Maestros",
        "🔔 Centro de Notificaciones",
        "📝 Registrar Producto", 
        "➕ Crear Categoría", 
        "➕ Crear Proveedor", 
        "➕ Crear Sede",
        "🗑️ Gestionar / Eliminar",
    ]

    if es_admin:
        menu_opciones.append("⚙️ Administración")

    opcion = st.sidebar.selectbox("🎯 MENÚ DEL SISTEMA", menu_opciones)
    
    st.sidebar.write("---")
    if st.sidebar.button("🔒 Cerrar Sesión", width="stretch"):
        st.session_state.clear() 
        st.rerun()

    # Enrutador de módulos
    if opcion == "📊 Centro de Control":
        mostrar_dashboard()
        
    elif opcion == "📋 Ver mi Inventario": 
        mostrar_inventario()

    elif opcion == "🗂️ Catálogos Maestros":
        mostrar_catalogos()
        
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

    elif opcion == "⚙️ Administración":
        mostrar_panel_admin()