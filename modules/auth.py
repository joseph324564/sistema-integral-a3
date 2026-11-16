import streamlit as st
import re
import pyotp
import qrcode
import io
import bcrypt
from database.connection import get_connection


def _obtener_campo(registro, clave, indice):
    """Accede de manera segura a un campo ya sea registro un dict (RealDictRow) o una tupla."""
    if isinstance(registro, dict):
        return registro.get(clave)
    try:
        return registro[clave]
    except (TypeError, KeyError):
        return registro[indice]


def validar_password(password: str):
    if len(password) < 6:
        return False, "❌ Mínimo 6 caracteres."
    if not re.search(r"[A-Z]", password):
        return False, "❌ Falta una MAYÚSCULA."
    if not re.search(r"[0-9]", password):
        return False, "❌ Falta un NÚMERO."
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "❌ Falta un CARÁCTER ESPECIAL."
    return True, ""


def verificar_y_actualizar_password(usuario_id: int, pw_ingresada: str, pw_almacenada: str) -> bool:
    """
    Verifica con bcrypt si empieza con '$2b$'. Si está en texto plano,
    valida igualdad directa y actualiza automáticamente el registro con bcrypt.
    """
    if not pw_almacenada:
        return False

    if pw_almacenada.startswith("$2b$"):
        try:
            return bcrypt.checkpw(pw_ingresada.encode("utf-8"), pw_almacenada.encode("utf-8"))
        except Exception:
            return False
    else:
        # Texto plano
        if pw_ingresada == pw_almacenada:
            nuevo_hash = bcrypt.hashpw(pw_ingresada.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute('UPDATE usuarios SET pw = %s WHERE id = %s', (nuevo_hash, usuario_id))
                    conn.commit()
            except Exception:
                pass
            return True
        return False


def mostrar_login():
    st.markdown("<h1 style='text-align: center;'>🔐 Sistema de Gestión A3</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #9CA3AF;'>Módulo de Autenticación de Doble Factor (2FA)</p>", unsafe_allow_html=True)

    col_left, col_center, col_right = st.columns([1, 1.2, 1])

    with col_center:
        tab1, tab2 = st.tabs(["🔑 Iniciar Sesión", "📝 Crear Cuenta"])

        with tab1:
            with st.container(border=True):
                with st.form("login_form"):
                    user = st.text_input("👤 Usuario", key="login_user").strip()
                    pw = st.text_input("🔑 Contraseña", type="password", key="login_pw")
                    btn_login = st.form_submit_button("Verificar Identidad", type="primary", width="stretch")

                if btn_login:
                    if user and pw:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    'SELECT id, "user", pw, otp_secret, COALESCE(rol, \'operador\') AS rol FROM usuarios WHERE "user" = %s',
                                    (user,)
                                )
                                res = cur.fetchone()

                        if res:
                            u_id = _obtener_campo(res, "id", 0)
                            u_name = _obtener_campo(res, "user", 1)
                            u_pw = _obtener_campo(res, "pw", 2)
                            u_otp = _obtener_campo(res, "otp_secret", 3)
                            u_rol = _obtener_campo(res, "rol", 4) or "operador"

                            if verificar_y_actualizar_password(u_id, pw, u_pw):
                                st.session_state.temp_user_id = u_id
                                st.session_state.temp_username = u_name or user
                                st.session_state.temp_otp_secret = u_otp
                                st.session_state.temp_rol = u_rol
                                st.session_state.paso_2fa = True
                                st.toast("🔑 Credenciales válidas. Ingrese su código 2FA.")
                            else:
                                st.error("Credenciales incorrectas.")
                        else:
                            st.error("Credenciales incorrectas.")
                    else:
                        st.warning("Por favor complete todos los campos.")

                if st.session_state.get('paso_2fa', False):
                    st.write("---")
                    st.warning("🔒 Verificación de Doble Factor Requerida.")
                    token = st.text_input("🔢 Código de 6 dígitos de tu App Google Authenticator", key="input_token")

                    if st.button("Confirmar Código 2FA", width="stretch", type="primary"):
                        otp_secret = st.session_state.get("temp_otp_secret", "")
                        totp = pyotp.TOTP(otp_secret) if otp_secret else None
                        if totp and totp.verify(token):
                            st.session_state.logged_in = True
                            st.session_state.user_id = st.session_state.temp_user_id
                            st.session_state.username = st.session_state.get("temp_username", user)
                            st.session_state.rol = st.session_state.get("temp_rol", "operador")

                            # Limpiar temporales
                            st.session_state.pop("paso_2fa", None)
                            st.session_state.pop("temp_user_id", None)
                            st.session_state.pop("temp_username", None)
                            st.session_state.pop("temp_otp_secret", None)
                            st.session_state.pop("temp_rol", None)

                            st.success("Acceso Concedido.")
                            st.rerun()
                        else:
                            st.error("Código incorrecto o expirado.")

        with tab2:
            with st.container(border=True):
                with st.form("register_form"):
                    new_user = st.text_input("👤 Nombre de Usuario", key="reg_user").strip()
                    new_pw = st.text_input("🔑 Contraseña", type="password", key="reg_pw")
                    correo = st.text_input("📧 Correo Electrónico (Obligatorio)", key="reg_mail").strip()
                    telefono = st.text_input("📱 Número Telefónico (Obligatorio)", key="reg_phone").strip()
                    btn_registro = st.form_submit_button("Registrar Cuenta y Generar QR Security", width="stretch", type="primary")

                if btn_registro:
                    valido, msg = validar_password(new_pw)
                    if not (new_user and new_pw and correo and telefono):
                        st.error("❌ Todos los campos son obligatorios para la auditoría de seguridad.")
                    elif not valido:
                        st.error(msg)
                    else:
                        hashed_pw = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                        secret_key = pyotp.random_base32()
                        with get_connection() as conn:
                            try:
                                with conn.cursor() as cur:
                                    cur.execute('''INSERT INTO usuarios ("user", pw, correo, telefono, otp_secret, rol)
                                                   VALUES (%s, %s, %s, %s, %s, %s)''',
                                                (new_user, hashed_pw, correo, telefono, secret_key, 'operador'))
                                conn.commit()
                                st.session_state.reg_exito = True
                                st.session_state.reg_secret = secret_key
                                st.session_state.reg_name = new_user
                            except Exception as e:
                                conn.rollback()
                                st.warning(f"El usuario ya existe o hubo un error: {e}")

                if st.session_state.get('reg_exito', False):
                    st.write("---")
                    st.success("🎉 ¡Cuenta creada con éxito! Vincula tu cuenta:")
                    totp_url = pyotp.totp.TOTP(st.session_state.reg_secret).provisioning_uri(
                        name=st.session_state.reg_name, issuer_name="Sistema_A3"
                    )
                    qr = qrcode.QRCode(version=1, box_size=10, border=4)
                    qr.add_data(totp_url)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    st.image(buf.getvalue(), caption="Escanea este código QR en tu app Google Authenticator", width=220)
                    st.info(f"Código manual en caso de fallas: `{st.session_state.reg_secret}`")
