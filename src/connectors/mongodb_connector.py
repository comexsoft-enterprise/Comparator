import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ConfigurationError
from urllib.parse import quote_plus
from config.settings import MONGO_CONFIG


def get_mongo_client(
    uri=MONGO_CONFIG["uri"],
    user=MONGO_CONFIG["user"],
    password=MONGO_CONFIG["password"],
    database=MONGO_CONFIG["database"],
    auth_source=MONGO_CONFIG.get("auth_source", "admin"),
):
    """
    Establishes a connection to the MongoDB database.
    """
    try:
        # Construye la URI completa si no está en config
        if "mongodb://" not in uri and "mongodb+srv://" not in uri:
            auth_part = f"{user}:{password}@" if user and password else ""
            uri = f"mongodb://{auth_part}{uri}"

        client = MongoClient(uri, serverSelectionTimeoutMS=5000)

        # Prueba la conexión
        client.admin.command("ping")

        if database:
            db = client[database]
            logging.info(f"Connection to MongoDB database '{database}' established successfully.")
            return db
        else:
            logging.info("Connection to MongoDB established successfully (no database specified).")
            return client

    except (ConnectionFailure, ConfigurationError) as err:
        logging.error(f"Connection to MongoDB failed: {err}")
        logging.error("Please ensure your MongoDB instance is running and connection details are correct.")
        return None
    except Exception as err:
        logging.error(f"Unexpected error while connecting to MongoDB: {err}")
        return None

def diagnostic_get_mongo_client(
    uri=MONGO_CONFIG["uri"],
    user=MONGO_CONFIG["user"],
    password=MONGO_CONFIG["password"],
    database=MONGO_CONFIG["database"],
    auth_source=MONGO_CONFIG.get("auth_source", "admin"),
):
    """
    Diagnostic wrapper: build/redact the URI, attempt to connect and ping MongoDB,
    and return a Database or Client. Raises on error so you get full trace.
    """
    try:
        # Build full URI if not provided as a full connection string
        if "mongodb://" not in uri and "mongodb+srv://" not in uri:
            auth_part = f"{quote_plus(user)}:{quote_plus(password)}@" if user and password else ""
            full_uri = f"mongodb://{auth_part}{uri}"
        else:
            full_uri = uri

        # Redact password for logging
        redacted_uri = full_uri.replace(password, "*****") if password else full_uri
        logging.info(f"[diagnostic] Attempting MongoDB connection to: {redacted_uri}")

        client = MongoClient(full_uri, serverSelectionTimeoutMS=5000)
        # Force a network check
        client.admin.command("ping")
        logging.info("[diagnostic] MongoDB ping successful")

        if database:
            logging.info(f"[diagnostic] Returning database: {database}")
            return client[database]
        logging.info("[diagnostic] Returning MongoClient (no database specified)")
        return client

    except Exception:
        logging.exception("[diagnostic] MongoDB connection/ping failed")
        # Re-raise so the caller / logs show the full error and stack trace
        raise