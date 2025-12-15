"""
Configuración central del proyecto
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Rutas del proyecto
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
SCHEMAS_DIR = DATA_DIR / "schemas"
LOGS_DIR = PROJECT_ROOT / "logs"

# Configuración de Neo4j
NEO4J_CONFIG = {
    "uri": os.getenv("NEO4J_URI", "54.237.55.135:7687"),
    "user": os.getenv("NEO4J_USER", "neo4j"),
    "password": os.getenv("NEO4J_PASSWORD", ""),
    "database": os.getenv("NEO4J_DATABASE", "neo4j"),
}

# Configuración de logging
LOGGING_CONFIG = {
    "level": os.getenv("LOG_LEVEL", "INFO"),
    "file": os.getenv("LOG_FILE", "logs/migration.log"),
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
}

AZURE_OPENAI_CONFIG = {
    "api_type": os.getenv("AZURE_OPENAI_API_TYPE"),
    "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
    "api_version": os.getenv("AZURE_OPENAI_API_VERSION"),
    "api_base": os.getenv("AZURE_OPENAI_API_BASE"),
    "deployment_name": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
    "api_endpoint": os.getenv("AZURE_OPENAI_API_ENDPOINT")
}

MONGO_CONFIG = {
    "uri": os.getenv("MONGO_URI", "localhost:27017"),
    "user": os.getenv("MONGO_USER1", "admin"),
    "password": os.getenv("MONGO_USER1_PASSWORD", ""),
    "database": os.getenv("MONGO_DATABASE", "test"),
}

POSTGRES_DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": os.getenv("POSTGRES_DB", "myappdb"),
    "user": os.getenv("POSTGRES_USER", "admin"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}