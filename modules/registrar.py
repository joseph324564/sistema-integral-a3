import streamlit as st
import pandas as pd
import psycopg
from database.connection import get_connection
from modules.db_queries import invalidar_cache_y_refrescar


def _obtener_campo(registro, clave, indice):
    if isinstance(registro, dict):
        return registro.get(clave)
    try:
        return registro[clave]
    except (TypeError, KeyError):
        return registro[indice]


def mostrar_catalogos():
    st.header("🗂️ Gestión de Catálogos Maestros")
    st.caption("Administra tus categorías, proveedores y sedes vinculadas a tu cuenta.")

    tab_cat, tab_prov, tab_sede = st.tabs(["📂 Categorías", "🚚 Proveedores", "🏢 Sedes"])

    # PESTAÑA 1: CATEGORÍAS
    with tab_cat:
        with st.container(border=True):
            st.subheader("➕ Nueva Categoría")
            with st.form("form_nueva_cat", clear_on_submit=True):
                n_cat = st.text_input("Nombre de la Categoría").strip()
                submitted = st.form_submit_button("Crear Categoría", type="primary")

                if submitted:
                    if n_cat:
                        try:
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "INSERT INTO categorias (user_id, nombre) VALUES (%s, %s)",
                                        (st.session_state.user_id, n_cat)
                                    )
                                conn.commit()
                            invalidar_cache_y_refrescar()
                            st.success(f"✅ Categoría '{n_cat}' registrada con éxito.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al registrar categoría: {e}")
                    else:
                        st.error("El nombre de la categoría no puede estar vacío.")

        st.write("---")
        st.subheader("📋 Categorías Registradas")
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, nombre FROM categorias WHERE user_id = %s ORDER BY id DESC",
                    (st.session_state.user_id,)
                )
                raw_cats = cur.fetchall()

        if not raw_cats:
            st.info("No tienes categorías registradas aún.")
        else:
            df_cats = pd.DataFrame([
                {"ID": _obtener_campo(r, "id", 0), "Nombre": _obtener_campo(r, "nombre", 1)}
                for r in raw_cats
            ])
            st.dataframe(df_cats, width="stretch", hide_index=True)

            with st.container(border=True):
                st.markdown("#### 🗑️ Eliminar Categoría")
                cat_dict = {f"#{row['ID']} - {row['Nombre']}": row['ID'] for _, row in df_cats.iterrows()}
                cat_selected_label = st.selectbox("Seleccionar categoría a eliminar", list(cat_dict.keys()), key="sel_del_cat")
                cat_del_id = cat_dict[cat_selected_label]

                if st.button("Eliminar Categoría Seleccionada", key="btn_del_cat", type="primary"):
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "DELETE FROM categorias WHERE id = %s AND user_id = %s",
                                    (cat_del_id, st.session_state.user_id)
                                )
                            conn.commit()
                        invalidar_cache_y_refrescar()
                        st.success("✅ Categoría eliminada correctamente.")
                        st.rerun()
                    except psycopg.IntegrityError:
                        st.error("⚠️ No se puede eliminar esta categoría porque tiene productos asociados en el inventario.")
                    except Exception as e:
                        st.error(f"Error al eliminar categoría: {e}")

    # PESTAÑA 2: PROVEEDORES
    with tab_prov:
        with st.container(border=True):
            st.subheader("➕ Nuevo Proveedor")
            with st.form("form_nuevo_prov", clear_on_submit=True):
                n_prov = st.text_input("Razón Social").strip()
                t_prov = st.text_input("Teléfono / Contacto").strip()
                no_nit = st.checkbox("No maneja NIT")
                nit_prov = st.text_input("NIT (Opcional si no maneja)")
                submitted_prov = st.form_submit_button("Crear Proveedor", type="primary")

                if submitted_prov:
                    nit_final = "N/A" if no_nit or not nit_prov else nit_prov.strip()
                    if n_prov and t_prov:
                        try:
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "INSERT INTO proveedores (user_id, nombre, contacto, nit) VALUES (%s, %s, %s, %s)",
                                        (st.session_state.user_id, n_prov, t_prov, nit_final)
                                    )
                                conn.commit()
                            invalidar_cache_y_refrescar()
                            st.success(f"✅ Proveedor '{n_prov}' registrado con éxito.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al registrar proveedor: {e}")
                    else:
                        st.error("Por favor completa los campos obligatorios (Razón Social y Contacto).")

        st.write("---")
        st.subheader("📋 Proveedores Registrados")
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, nombre, contacto, nit FROM proveedores WHERE user_id = %s ORDER BY id DESC",
                    (st.session_state.user_id,)
                )
                raw_provs = cur.fetchall()

        if not raw_provs:
            st.info("No tienes proveedores registrados aún.")
        else:
            df_provs = pd.DataFrame([
                {
                    "ID": _obtener_campo(r, "id", 0),
                    "Razón Social": _obtener_campo(r, "nombre", 1),
                    "Contacto": _obtener_campo(r, "contacto", 2),
                    "NIT": _obtener_campo(r, "nit", 3)
                }
                for r in raw_provs
            ])
            st.dataframe(df_provs, width="stretch", hide_index=True)

            with st.container(border=True):
                st.markdown("#### 🗑️ Eliminar Proveedor")
                prov_dict = {f"#{row['ID']} - {row['Razón Social']}": row['ID'] for _, row in df_provs.iterrows()}
                prov_selected_label = st.selectbox("Seleccionar proveedor a eliminar", list(prov_dict.keys()), key="sel_del_prov")
                prov_del_id = prov_dict[prov_selected_label]

                if st.button("Eliminar Proveedor Seleccionado", key="btn_del_prov", type="primary"):
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "DELETE FROM proveedores WHERE id = %s AND user_id = %s",
                                    (prov_del_id, st.session_state.user_id)
                                )
                            conn.commit()
                        invalidar_cache_y_refrescar()
                        st.success("✅ Proveedor eliminado correctamente.")
                        st.rerun()
                    except psycopg.IntegrityError:
                        st.error("⚠️ No se puede eliminar este proveedor porque tiene productos asociados en el inventario.")
                    except Exception as e:
                        st.error(f"Error al eliminar proveedor: {e}")

    # PESTAÑA 3: SEDES
    with tab_sede:
        with st.container(border=True):
            st.subheader("➕ Nueva Sede")
            with st.form("form_nueva_sede", clear_on_submit=True):
                n_sede = st.text_input("Nombre de la Sede").strip()
                d_sede = st.text_input("Dirección").strip()
                loc = st.selectbox("Ubicación Bogotá / Región", ["Suba", "Kennedy", "Engativá", "Usaquén", "Chapinero", "Fontibón", "Otro"])
                submitted_sede = st.form_submit_button("Crear Sede", type="primary")

                if submitted_sede:
                    if n_sede and d_sede:
                        try:
                            direccion_completa = f"{d_sede} ({loc})"
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "INSERT INTO sedes (user_id, nombre, direccion) VALUES (%s, %s, %s)",
                                        (st.session_state.user_id, n_sede, direccion_completa)
                                    )
                                conn.commit()
                            invalidar_cache_y_refrescar()
                            st.success(f"✅ Sede '{n_sede}' registrada con éxito.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al registrar sede: {e}")
                    else:
                        st.error("Por favor completa el nombre de la sede y su dirección.")

        st.write("---")
        st.subheader("📋 Sedes Registradas")
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, nombre, direccion FROM sedes WHERE user_id = %s ORDER BY id DESC",
                    (st.session_state.user_id,)
                )
                raw_sedes = cur.fetchall()

        if not raw_sedes:
            st.info("No tienes sedes registradas aún.")
        else:
            df_sedes = pd.DataFrame([
                {
                    "ID": _obtener_campo(r, "id", 0),
                    "Sede": _obtener_campo(r, "nombre", 1),
                    "Dirección": _obtener_campo(r, "direccion", 2)
                }
                for r in raw_sedes
            ])
            st.dataframe(df_sedes, width="stretch", hide_index=True)

            with st.container(border=True):
                st.markdown("#### 🗑️ Eliminar Sede")
                sede_dict = {f"#{row['ID']} - {row['Sede']}": row['ID'] for _, row in df_sedes.iterrows()}
                sede_selected_label = st.selectbox("Seleccionar sede a eliminar", list(sede_dict.keys()), key="sel_del_sede")
                sede_del_id = sede_dict[sede_selected_label]

                if st.button("Eliminar Sede Seleccionada", key="btn_del_sede", type="primary"):
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "DELETE FROM sedes WHERE id = %s AND user_id = %s",
                                    (sede_del_id, st.session_state.user_id)
                                )
                            conn.commit()
                        invalidar_cache_y_refrescar()
                        st.success("✅ Sede eliminada correctamente.")
                        st.rerun()
                    except psycopg.IntegrityError:
                        st.error("⚠️ No se puede eliminar esta sede porque tiene productos asociados en el inventario.")
                    except Exception as e:
                        st.error(f"Error al eliminar sede: {e}")


def formulario_producto():
    st.subheader("📝 Ficha Técnica de Registro de Activos")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nombre FROM categorias WHERE user_id = %s ORDER BY nombre", (st.session_state.user_id,))
            raw_cats = cur.fetchall()
            cur.execute("SELECT id, nombre FROM proveedores WHERE user_id = %s ORDER BY nombre", (st.session_state.user_id,))
            raw_provs = cur.fetchall()
            cur.execute("SELECT id, nombre FROM sedes WHERE user_id = %s ORDER BY nombre", (st.session_state.user_id,))
            raw_sedes = cur.fetchall()

    if not raw_cats or not raw_provs or not raw_sedes:
        st.warning("⚠️ Debes registrar primero al menos una Sede, Proveedor y Categoría en tu cuenta antes de crear un activo.")
        return

    cats_dict = {_obtener_campo(r, "nombre", 1): _obtener_campo(r, "id", 0) for r in raw_cats}
    provs_dict = {_obtener_campo(r, "nombre", 1): _obtener_campo(r, "id", 0) for r in raw_provs}
    sedes_dict = {_obtener_campo(r, "nombre", 1): _obtener_campo(r, "id", 0) for r in raw_sedes}

    with st.form("prod_form"):
        nom = st.text_input("Nombre del Activo (ej. Laptop Dell XPS)").strip()
        c_s = st.selectbox("Categoría", list(cats_dict.keys()))
        p_s = st.selectbox("Proveedor", list(provs_dict.keys()))
        s_s = st.selectbox("Sede de Destino", list(sedes_dict.keys()))
        vd = st.number_input("Ciclo de Vida Útil (Minutos de prueba sugeridos)", min_value=1, value=60)
        sr = st.text_input("Número de Serial Único").strip()
        mr = st.text_input("Marca").strip()

        submitted = st.form_submit_button("Asegurar Registro en Servidor", type="primary", width="stretch")

        if submitted:
            if nom and sr:
                c_id = cats_dict[c_s]
                p_id = provs_dict[p_s]
                s_id = sedes_dict[s_s]

                try:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """INSERT INTO productos (user_id, cat_id, prov_id, sede_id, nombre, fecha_ingreso, vida_min) 
                                   VALUES (%s, %s, %s, %s, %s, NOW(), %s) RETURNING id""",
                                (st.session_state.user_id, c_id, p_id, s_id, nom, vd)
                            )
                            res = cur.fetchone()
                            prod_id = _obtener_campo(res, "id", 0)

                            cur.execute(
                                "INSERT INTO detalles (prod_id, serial, marca) VALUES (%s, %s, %s)",
                                (prod_id, sr, mr or "Genérico")
                            )
                        conn.commit()
                    invalidar_cache_y_refrescar()
                    st.success(f"🚀 Activo '{nom}' registrado correctamente e indexado a tu inventario.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al registrar activo: {e}")
            else:
                st.error("El nombre del activo y el serial son campos obligatorios.")


# Aliases para mantener compatibilidad si algún archivo antiguo los llamaba
def formulario_categoria():
    mostrar_catalogos()


def formulario_proveedor():
    mostrar_catalogos()


def formulario_sede():
    mostrar_catalogos()
