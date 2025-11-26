import logging
from neo4j import GraphDatabase
from config.settings import NEO4J_CONFIG

def get_neo4j_driver(uri=NEO4J_CONFIG["uri"], user=NEO4J_CONFIG["user"], password=NEO4J_CONFIG["password"]):
    """
    Establishes a connection to the Neo4j database.
    """
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        logging.info("Connection to Neo4j established successfully.")
        return driver
    except Exception as err:
        logging.error(f"Connection failed: {err}")
        logging.error("Please ensure your Neo4j database is running and the connection details are correct.")
        return None