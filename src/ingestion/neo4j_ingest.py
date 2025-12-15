"""
Neo4j CSV Data Ingestion Module

This module provides functionality to ingest CSV files from the processed data directory
into a Neo4j database. It creates nodes and relationships for consumer goods data.
"""

import logging
import os
import pandas as pd
import time
from pathlib import Path
from typing import List

import sys


# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


from src.connectors.neo4j_connector import Neo4jConnector
from src.ingestion.nodes_relationships import Neo4jNodesRelationshipsManager
from config.settings import PROJECT_ROOT


NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


class Neo4jCSVIngestor:
    """
    A class to handle CSV data ingestion into Neo4j database.
    
    This class provides methods to:
    - Connect to Neo4j database
    - Load CSV files from the processed directory
    - Clean and transform data
    - Create nodes and relationships in Neo4j
    """
    
    def __init__(self, processed_data_dir: str = None):
        """
        Initialize the Neo4j CSV Ingestor.
        
        Args:
            processed_data_dir (str): Path to the directory containing processed CSV files.
                                    Defaults to data/processed relative to project root.
        """
        self.driver = None
        self.processed_data_dir = processed_data_dir or str(PROJECT_ROOT / "data" / "processed")
        self.nodes_relationships_manager = Neo4jNodesRelationshipsManager()
        self.neo4j_connector = Neo4jConnector()
        
        
    def connect_to_neo4j(self) -> bool:
        """
        Establish connection to Neo4j database.
        
        Returns:
            bool: True if connection successful, False otherwise.
        """
        try:
            self.driver = self.neo4j_connector.get_neo4j_driver(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)
            if self.driver:
                logging.info("Successfully connected to Neo4j database")
                return True
            else:
                logging.error("Failed to connect to Neo4j database")
                return False
        except Exception as e:
            logging.error(f"Error connecting to Neo4j: {e}")
            return False

    def get_csv_files(self, folder: str) -> List[str]:
        """
        Get list of CSV files in the processed data directory.
        
        Returns:
            List[str]: List of CSV file paths.
        """
        csv_files = []
        try:
            data_dir = Path(self.processed_data_dir + f"/{folder}")
            if data_dir.exists():
                csv_files = [str(f) for f in data_dir.glob("*.csv")]
                logging.info(f"Found {len(csv_files)} CSV files in {data_dir}")
            else:
                logging.warning(f"Directory {data_dir} does not exist")
        except Exception as e:
            logging.error(f"Error accessing directory {self.processed_data_dir}: {e}")
        
        return csv_files
    
    def check_value_exists(self, label: str, product_hash: str, value) -> bool:
        """
        Check if a specific value exists in a Neo4j node property.
        
        Args:
            label (str): Node label to check (e.g., 'Product').
            product_hash (str): Name of the property to check.
            value: Value to search for.
            
        Returns:
            bool: True if the value exists, False otherwise.
        """
        try:
            if not self.driver:
                logging.error("No database connection available")
                return False
            
            with self.driver.session(database=NEO4J_DATABASE) as session:
                query = f"MATCH (n:{label} {{{product_hash}: $value}}) RETURN n LIMIT 1"
                result = session.run(query, value=value)
                existing_node = result.single()
                
                if existing_node:
                    logging.debug(f"Value '{value}' exists in label '{label}' property '{product_hash}'")
                    return True
                else:
                    logging.debug(f"Value '{value}' does not exist in label '{label}' property '{product_hash}'")
                    return False
                    
        except Exception as e:
            logging.error(f"Error checking if value exists in {label}.{product_hash}: {e}")
            return False
    
    def clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and prepare DataFrame for Neo4j ingestion.
        
        Args:
            df (pd.DataFrame): Raw DataFrame from CSV.
            
        Returns:
            pd.DataFrame: Cleaned DataFrame.
        """
        # Create a copy to avoid the "object cannot be re-sized" error
        df_cleaned = df.copy()
        
        # Remove unnamed columns
        df_cleaned = df_cleaned.loc[:, ~df_cleaned.columns.str.contains('^Unnamed')]
        
        # Fill NaN values with None (which becomes null in Neo4j)
        df_cleaned = df_cleaned.where(pd.notnull(df_cleaned), None)
        
        # Clean string columns - remove extra whitespace
        string_columns = df_cleaned.select_dtypes(include=['object']).columns
        for col in string_columns:
            if df_cleaned[col].dtype == 'object':
                # Convert to string and clean, handling None values properly
                df_cleaned.loc[:, col] = df_cleaned[col].astype(str).str.strip()
                # Replace 'nan' strings with None
                df_cleaned.loc[:, col] = df_cleaned[col].replace(['nan', 'None', ''], None)
        
        return df_cleaned
    
    def ingest_csv_file(self, csv_file_path: str, batch_size: int = 1000):
        """
        Process a single CSV file and ingest data into Neo4j.
        
        Args:
            csv_file_path (str): Path to the CSV file.
            batch_size (int): Number of rows to process in each batch.
        """
        logging.info(f"Starting to process file: {csv_file_path}")
        start_time = time.time()
        
        try:
            # Read CSV file
            df = pd.read_csv(csv_file_path, sep=";")
            logging.info(f"Loaded {len(df)} rows from {csv_file_path}")
            
            # Clean data
            df = self.clean_data(df)
            
            if len(df) == 0:
                logging.warning("⚠️ No valid rows to process after filtering. Skipping file.")
                return
            
            # Process data in batches
            total_batches = (len(df) + batch_size - 1) // batch_size
            
            for batch_num in range(total_batches):
                batch_start_time = time.time()
                start_idx = batch_num * batch_size
                end_idx = min((batch_num + 1) * batch_size, len(df))
                batch_df = df.iloc[start_idx:end_idx]
                
                logging.info(f"Processing batch {batch_num + 1}/{total_batches} "
                            f"(rows {start_idx + 1}-{end_idx})")
                
                # Convert batch to list of dictionaries
                batch_data = batch_df.to_dict('records')

                # Process entire batch at once using optimized batch method
                with self.driver.session(database=NEO4J_DATABASE) as session:
                    self.nodes_relationships_manager.process_products_batch(session, batch_data)
                
                batch_end_time = time.time()
                batch_processing_time = batch_end_time - batch_start_time
                logging.info(f"Completed batch {batch_num + 1}/{total_batches} in {batch_processing_time:.2f} seconds")
            
            end_time = time.time()
            total_processing_time = end_time - start_time
            logging.info(f"Completed processing file {csv_file_path} in {total_processing_time:.2f} seconds total")
        
        except Exception as e:
            logging.error(f"Error processing file {csv_file_path}: {e}")
            raise


    def ingest_all_csv_files(self, folder: str, batch_size: int = 1000):
        """
        Ingest all CSV files from the processed data directory.
        
        Args:
            batch_size (int): Number of rows to process in each batch.
        """
        if not self.connect_to_neo4j():
            logging.error("Cannot proceed without Neo4j connection")
            return False
        
        try:
            # Create constraints and indexes BEFORE ingestion for better performance
            logging.info("Creating constraints and indexes before ingestion...")
            self.nodes_relationships_manager.create_constraints_and_indexes()
            
            # Get CSV files
            csv_files = self.get_csv_files(folder=folder)
            if not csv_files:
                logging.warning("No CSV files found to process")
                return False
            
            # Process each CSV file
            for csv_file in csv_files:
                self.ingest_csv_file(csv_file, batch_size)
                logging.info(f"Completed processing: {csv_file}")
            
            logging.info("Successfully completed ingestion of all CSV files")
            return True
            
        except Exception as e:
            logging.error(f"Error during CSV ingestion: {e}")
            return False
        finally:
            self.neo4j_connector.close_connection()