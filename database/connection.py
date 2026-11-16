import os
from contextlib import contextmanager
import streamlit as st
import psycopg
from psycopg.rows import dict_row


def get_sql_connection():
    """Conexión SQL administrada de Streamlit para Neon con pool_pre_ping activado."""
    return st.connection("neon", type="sql", pool_pre_ping=True)


def get_ai_sql_connection():
    """Conexión SQL administrada de solo lectura para las consultas de la IA."""
    return st.connection("neon_ia_readonly", type="sql")


@contextmanager
def get_connection():
    """Generador de contexto para conexiones directas con psycopg (v3).

    Garantiza commit(), rollback() en caso de error y close() en finally.
    """
    db_url = None
    try:
        db_url = st.secrets["connections"]["neon"]["url"]
    except Exception:
        try:
            db_url = st.secrets.get("neon", {}).get("url")
        except Exception:
            pass

    if not db_url:
        db_url = os.getenv("DATABASE_URL")

    if db_url and db_url.startswith("postgresql+psycopg://"):
        db_url = db_url.replace("postgresql+psycopg://", "postgresql://", 1)

    conn = psycopg.connect(db_url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Alias temporal para compatibilidad hacia atrás
get_db_connection = get_connection


def init_db():
    pass
