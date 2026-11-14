import os
import psycopg2

# Tu URL de conexión directa a Neon.tech
DATABASE_URL = os.environ.get(
    "DATABASE_URL", 
    "postgresql://neondb_owner:npg_5FJZRDbwMeC9@ep-long-wildflower-bezdc0jh-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
)

def init_db():
    """
    Función de inicialización para PostgreSQL en la nube.
    
    """
    pass

def get_connection():
    """
    Abre y retorna una conexión activa a la base de datos PostgreSQL en Neon.
    """
    conn = psycopg2.connect(DATABASE_URL)
    return conn