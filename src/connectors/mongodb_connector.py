import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ConfigurationError
import os
from config.settings import MONGO_CONFIG


def get_mongo_client(
    uri=MONGO_CONFIG["uri"],
    user=MONGO_CONFIG["user"],
    password=MONGO_CONFIG["password"],
    database=MONGO_CONFIG.get("database"),
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
