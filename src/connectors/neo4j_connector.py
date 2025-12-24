from typing import Dict, Any, List
import logging
from neo4j import GraphDatabase
from config.settings import NEO4J_CONFIG

NEO4J_URI = NEO4J_CONFIG["uri"]
NEO4J_USERNAME = NEO4J_CONFIG["user"]
NEO4J_PASSWORD = NEO4J_CONFIG["password"]
NEO4J_DATABASE = NEO4J_CONFIG.get("database", "neo4j")
    

class Neo4jConnector:
    def __init__(self):
        self.driver = None

    @staticmethod
    def get_neo4j_driver(uri=NEO4J_CONFIG["uri"], user=NEO4J_CONFIG["user"], password=NEO4J_CONFIG["password"]):
        """
        Establishes a connection to the Neo4j database with optimized connection pooling.
        """
        # Use provided values or fall back to config defaults
        uri = uri or NEO4J_CONFIG["uri"]
        user = user or NEO4J_CONFIG["user"]
        password = password or NEO4J_CONFIG["password"]
        
        try:
            # OPTIMIZATION: Configure connection pool to prevent memory overload
            driver = GraphDatabase.driver(
                uri, 
                auth=(user, password),
                max_connection_pool_size=50,  # Limit concurrent connections
                connection_acquisition_timeout=60.0,  # Wait for available connection
                max_transaction_retry_time=30.0,  # Retry failed transactions
                encrypted=False  # Disable encryption for local dev (faster)
            )
            driver.verify_connectivity()
            logging.info("Connection to Neo4j established successfully with optimized pool settings.")
            return driver
        except Exception as err:
            logging.error(f"Connection failed: {err}")
            logging.error("Please ensure your Neo4j database is running and the connection details are correct.")
            return None

    def connect_to_neo4j(self) -> bool:
        """Establish connection to Neo4j database."""
        try:
            self.driver = self.get_neo4j_driver(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)
            if self.driver:
                logging.info("Successfully connected to Neo4j database")
                return True
            logging.error("Failed to connect to Neo4j database")
            return False
        except Exception as e:
            logging.error(f"Error connecting to Neo4j: {e}")
            return False

    def close_connection(self):
        """Close the Neo4j .driver connection."""
        if self.driver:
            self.driver.close()
            logging.info("Neo4j connection closed")

    def test_connection(self) -> Dict[str, Any]:
        """
        Test the Neo4j connection and retrieve database information.
        """
        if not self.driver:
            return {
                'success': False,
                'error': 'self.driver not initialized. Call connect_to_neo4j() first.'
            }

        try:
            with self.driver.session(database=NEO4J_DATABASE) as session:
                # Basic connectivity
                result = session.run("RETURN 1 AS test")
                test_value = result.single()["test"]
                if test_value != 1:
                    return {'success': False, 'error': 'Basic connectivity test failed'}

                # Version
                version_result = session.run("CALL dbms.components() YIELD versions RETURN versions[0] AS version")
                neo4j_version = version_result.single()["version"]

                # Counts
                node_count_result = session.run("MATCH (n) RETURN count(n) AS count")
                node_count = node_count_result.single()["count"]
                rel_count_result = session.run("MATCH ()-[r]-() RETURN count(r) AS count")
                rel_count = rel_count_result.single()["count"]

                # DB name
                db_result = session.run("CALL db.info() YIELD name RETURN name")
                db_name = db_result.single()["name"]

                # Labels / Rel types
                labels_result = session.run("CALL db.labels()")
                labels = [record["label"] for record in labels_result]
                rel_types_result = session.run("CALL db.relationshipTypes()")
                rel_types = [record["relationshipType"] for record in rel_types_result]

                return {
                    'success': True,
                    'database': db_name,
                    'neo4j_version': neo4j_version,
                    'node_count': node_count,
                    'relationship_count': rel_count,
                    'node_labels': labels,
                    'relationship_types': rel_types,
                    'uri': NEO4J_URI,
                    'username': NEO4J_USERNAME
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def execute_query(self, query: str, parameters: dict = None) -> List[Dict[str, Any]]:
            """
            Execute a Cypher query and return results as list of dicts.
            """
            if not self.driver:
                logging.error("driver not initialized. Call connect_to_neo4j() first.")
                return []
            try:
                with self.driver.session(database=NEO4J_DATABASE) as session:
                    result = session.run(query, parameters or {})
                    return [dict(record) for record in result]
            except Exception as e:
                logging.error(f"Error executing query: {e}")
                return []
