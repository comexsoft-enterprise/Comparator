'''
Conector centralizado para PostgreSQL.

Este módulo proporciona una función reutilizable para establecer conexiones
con la base de datos PostgreSQL, leyendo la configuración desde variables de entorno.

Uso:
    from src.connectors.postgresql_connector import get_postgresql_connection
    
    conn = get_postgresql_connection()
    cursor = conn.cursor()
    # ... usar la conexión
    cursor.close()
    conn.close()
'''

import os
import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv
from typing import Optional
import logging

# Cargar variables de entorno
load_dotenv()

# Global connection pool
_connection_pool = None


def get_connection_pool(minconn=20, maxconn=2000):
    """
    Obtiene o crea un pool de conexiones a PostgreSQL.
    
    Args:
        minconn: Número mínimo de conexiones en el pool (default: 5)
        maxconn: Número máximo de conexiones en el pool (default: 30)
    
    Returns:
        psycopg2.pool.SimpleConnectionPool: Pool de conexiones
    """
    global _connection_pool
    
    if _connection_pool is None:
        db_config = {
            'host': os.getenv('POSTGRES_HOST'),
            'port': os.getenv('POSTGRES_PORT'),
            'database': os.getenv('POSTGRES_DB'),
            'user': os.getenv('POSTGRES_USER'),
            'password': os.getenv('POSTGRES_PASSWORD')
        }
        
        try:
            _connection_pool = pool.ThreadedConnectionPool(
                minconn,
                maxconn,
                **db_config
            )
            logging.info(f"✅ PostgreSQL connection pool created (min={minconn}, max={maxconn})")
        except psycopg2.Error as e:
            raise psycopg2.Error(f"Error creating connection pool: {e}")
    
    return _connection_pool


def get_pooled_connection(max_retries=3, retry_delay=0.5):
    """
    Obtiene una conexión del pool con reintentos.
    Para contextos multithreading, crea una conexión directa en lugar de usar pool.
    
    Args:
        max_retries: Número máximo de intentos (default: 3)
        retry_delay: Segundos de espera entre reintentos (default: 0.5)
    
    Returns:
        psycopg2.connection: Conexión de PostgreSQL
        
    Raises:
        Exception: Si no se puede obtener conexión después de los reintentos
    """
    import time
    
    db_config = {
        'host': os.getenv('POSTGRES_HOST'),
        'port': os.getenv('POSTGRES_PORT'),
        'database': os.getenv('POSTGRES_DB'),
        'user': os.getenv('POSTGRES_USER'),
        'password': os.getenv('POSTGRES_PASSWORD')
    }
    
    for attempt in range(max_retries):
        try:
            return psycopg2.connect(**db_config)
        except psycopg2.Error as e:
            if attempt < max_retries - 1:
                logging.warning(f"⚠️ Connection failed, retrying in {retry_delay}s... (attempt {attempt+1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                logging.error(f"❌ Failed to connect after {max_retries} attempts")
                raise Exception(f"Failed to connect to PostgreSQL after {max_retries} retries: {e}")
    
    raise Exception("Failed to connect to PostgreSQL")


def return_pooled_connection(conn):
    """
    Cierra una conexión directa.
    En contextos multithreading, simplemente cerramos la conexión.
    
    Args:
        conn: Conexión a cerrar
    """
    try:
        if conn and not conn.closed:
            conn.close()
    except Exception as e:
        logging.warning(f"⚠️ Error closing connection: {e}")


def get_postgresql_connection():
    """
    Establece y retorna una conexión a PostgreSQL usando variables de entorno.
    
    Variables de entorno requeridas:
        - POSTGRES_HOST: Host del servidor PostgreSQL
        - POSTGRES_PORT: Puerto del servidor PostgreSQL
        - POSTGRES_DB: Nombre de la base de datos
        - POSTGRES_USER: Usuario de la base de datos
        - POSTGRES_PASSWORD: Contraseña del usuario
    
    Returns:
        psycopg2.connection: Objeto de conexión a PostgreSQL
        
    Raises:
        ValueError: Si faltan variables de entorno requeridas
        psycopg2.Error: Si hay error al conectar a la base de datos
    """
    # Validar que existan las variables de entorno
    required_vars = ['POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        raise ValueError(f"Faltan variables de entorno requeridas: {', '.join(missing_vars)}")
    
    # Configuración de conexión
    db_config = {
        'host': os.getenv('POSTGRES_HOST'),
        'port': os.getenv('POSTGRES_PORT'),
        'database': os.getenv('POSTGRES_DB'),
        'user': os.getenv('POSTGRES_USER'),
        'password': os.getenv('POSTGRES_PASSWORD')
    }
    
    try:
        conn = psycopg2.connect(**db_config)
        return conn
    except psycopg2.Error as e:
        raise psycopg2.Error(f"Error al conectar a PostgreSQL: {e}")


def get_db_config() -> dict:
    """
    Retorna el diccionario de configuración de PostgreSQL sin establecer conexión.
    
    Returns:
        dict: Diccionario con la configuración de conexión
    """
    return {
        'host': os.getenv('POSTGRES_HOST'),
        'port': os.getenv('POSTGRES_PORT'),
        'database': os.getenv('POSTGRES_DB'),
        'user': os.getenv('POSTGRES_USER'),
        'password': os.getenv('POSTGRES_PASSWORD')
    }
