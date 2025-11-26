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
}

# Configuración de logging
LOGGING_CONFIG = {
    "level": os.getenv("LOG_LEVEL", "INFO"),
    "file": os.getenv("LOG_FILE", "logs/migration.log"),
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
}

# Configuración de archivos
FILE_CONFIG = {
    "max_size_mb": int(os.getenv("MAX_FILE_SIZE_MB", "100")),
    "allowed_extensions": os.getenv("ALLOWED_EXTENSIONS", "csv,xlsx,json").split(","),
}

# Configuración de procesamiento
PROCESSING_CONFIG = {
    "batch_size": int(os.getenv("BATCH_SIZE", "1000")),
    "parallel_processing": os.getenv("PARALLEL_PROCESSING", "True").lower() == "true",
}

AZURE_TRANSLATOR_CONFIG = {
    "key": os.getenv("AZURE_TRANSLATOR_KEY"),
    "endpoint": os.getenv("AZURE_TRANSLATOR_ENDPOINT"),
    "region": os.getenv("AZURE_TRANSLATOR_REGION"),
}

AWS_TRANSLATE_CONFIG = {
    "access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
    "secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
    "region": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
}

OPENAI_API_CONFIG = {
    "key": os.getenv("OPENAI_API_KEY")
}


MONGO_CONFIG = {
    "uri": os.getenv("MONGO_URI", "localhost:27017"),
    "user": os.getenv("MONGO_USER1", "admin"),
    "password": os.getenv("MONGO_USER1_PASSWORD", "secretpass"),
    "database": os.getenv("MONGO_DATABASE", "test"),
}
