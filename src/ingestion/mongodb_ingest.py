"""
MongoDB CSV Data Ingestion Module

This module provides functionality to ingest CSV files from the processed data directory
into a MongoDB database. It creates documents for consumer goods data.
"""

import logging
import os
import pandas as pd
import time
from pathlib import Path
from typing import List

from src.connectors.mongodb_connector import get_mongo_client
from config.settings import PROJECT_ROOT, MONGO_CONFIG


class MongoDBCSVIngestor:
    """
    A class to handle CSV data ingestion into MongoDB database.
    
    This class provides methods to:
    - Connect to MongoDB database
    - Load CSV files from the processed directory
    - Clean and transform data
    - Create documents in MongoDB collections
    """
    
    def __init__(self, processed_data_dir: str = None, database_name: str = None):
        """
        Initialize the MongoDB CSV Ingestor.
        
        Args:
            processed_data_dir (str): Path to the directory containing processed CSV files.
                                    Defaults to data/processed relative to project root.
            database_name (str): Name of the MongoDB database to use.
        """
        self.db = None
        self.processed_data_dir = processed_data_dir or str(PROJECT_ROOT / "data" / "processed")
        self.database_name = database_name or MONGO_CONFIG.get("database")
        
    def connect_to_mongodb(self) -> bool:
        """
        Establish connection to MongoDB database.
        
        Returns:
            bool: True if connection successful, False otherwise.
        """
        try:
            self.db = get_mongo_client(
                uri=MONGO_CONFIG["uri"],
                user=MONGO_CONFIG["user"],
                password=MONGO_CONFIG["password"],
                database=self.database_name
            )
            # ✅ FIX: Comparar explícitamente con None
            if self.db is not None:
                logging.info(f"Successfully connected to MongoDB database: {self.database_name}")
                return True
            else:
                logging.error("Failed to connect to MongoDB database")
                return False
        except Exception as e:
            logging.error(f"Error connecting to MongoDB: {e}")
            return False
    
    def close_connection(self):
        """Close the MongoDB connection."""
        # ✅ FIX: Comparar explícitamente con None
        if self.db is not None:
            self.db.client.close()
            logging.info("MongoDB connection closed")

    def get_csv_files(self, folder: str) -> List[str]:
        """
        Get list of CSV files in the processed data directory.
        
        Args:
            folder (str): Subfolder within processed data directory.
        
        Returns:
            List[str]: List of CSV file paths.
        """
        csv_files = []
        try:
            data_dir = Path(self.processed_data_dir) / folder
            if data_dir.exists():
                csv_files = [str(f) for f in data_dir.glob("*.csv")]
                logging.info(f"Found {len(csv_files)} CSV files in {data_dir}")
            else:
                logging.warning(f"Directory {data_dir} does not exist")
        except Exception as e:
            logging.error(f"Error accessing directory {self.processed_data_dir}/{folder}: {e}")
        
        return csv_files
    
    def check_value_exists(self, collection_name: str, field: str, value) -> bool:
        """
        Check if a specific value exists in a collection field.
        
        Args:
            collection_name (str): Name of the MongoDB collection.
            field (str): Name of the field to check.
            value: Value to search for.
            
        Returns:
            bool: True if the value exists, False otherwise.
        """
        try:
            if self.db is None:
                logging.error("No database connection available")
                return False
            
            collection = self.db[collection_name]
            existing_doc = collection.find_one({field: value})
            
            if existing_doc:
                logging.debug(f"Value '{value}' exists in collection '{collection_name}' field '{field}'")
                return True
            else:
                logging.debug(f"Value '{value}' does not exist in collection '{collection_name}' field '{field}'")
                return False
                
        except Exception as e:
            logging.error(f"Error checking if value exists in {collection_name}.{field}: {e}")
            return False
    
    def clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and prepare DataFrame for MongoDB ingestion.
        
        Args:
            df (pd.DataFrame): Raw DataFrame from CSV.
            
        Returns:
            pd.DataFrame: Cleaned DataFrame.
        """
        # Create a copy to avoid modification warnings
        df_cleaned = df.copy()
        
        # Remove unnamed columns
        df_cleaned = df_cleaned.loc[:, ~df_cleaned.columns.str.contains('^Unnamed')]
        
        # Fill NaN values with None
        df_cleaned = df_cleaned.where(pd.notnull(df_cleaned), None)
        
        # Clean string columns - remove extra whitespace
        string_columns = df_cleaned.select_dtypes(include=['object']).columns
        for col in string_columns:
            if df_cleaned[col].dtype == 'object':
                df_cleaned.loc[:, col] = df_cleaned[col].astype(str).str.strip()
                df_cleaned.loc[:, col] = df_cleaned[col].replace(['nan', 'None', ''], None)
        
        return df_cleaned
    
    def process_csv_file(self, csv_file_path: str, collection_name: str = None, batch_size: int = 1000, 
                        id_field: str = 'uuid', watch_fields: List[str] = None):
        """
        Process a single CSV file and ingest data into MongoDB.
        Checks for existing documents by ID and only updates if specified fields have changed.
        
        Args:
            csv_file_path (str): Path to the CSV file.
            collection_name (str): Name of the MongoDB collection. Defaults to filename without extension.
            batch_size (int): Number of rows to process in each batch.
            id_field (str): Name of the field to use as unique identifier. Defaults to 'uuid'.
            watch_fields (List[str]): List of fields to watch for changes. If any of these fields change,
                                     the entire document will be updated. 
                                     If None or empty list, ALL fields will be checked and updated if different.
        """
        logging.info(f"Starting to process file: {csv_file_path}")
        logging.info(f"   ID field: {id_field}")
        if watch_fields:
            logging.info(f"   Watch fields: {watch_fields}")
        else:
            logging.info(f"   Watch fields: ALL (will check all columns for changes)")
        start_time = time.time()
        
        try:
            # Determine collection name from filename if not provided
            if not collection_name:
                collection_name = Path(csv_file_path).stem
            
            collection = self.db[collection_name]
            
            # Read CSV file
            df = pd.read_csv(csv_file_path, sep=";")
            logging.info(f"Loaded {len(df)} rows from {csv_file_path}")
            
            # Clean data
            df = self.clean_data(df)
            
            # Process data in batches
            total_batches = (len(df) + batch_size - 1) // batch_size
            inserted_count = 0
            updated_count = 0
            skipped_count = 0
            unchanged_count = 0
            
            for batch_num in range(total_batches):
                batch_start_time = time.time()
                start_idx = batch_num * batch_size
                end_idx = min((batch_num + 1) * batch_size, len(df))
                batch_df = df.iloc[start_idx:end_idx]
                
                logging.info(f"Processing batch {batch_num + 1}/{total_batches} "
                               f"(rows {start_idx + 1}-{end_idx})")
                
                # Convert batch to list of dictionaries
                documents = batch_df.to_dict('records')
                
                # Process each document with conditional update
                if documents:
                    batch_inserted = 0
                    batch_updated = 0
                    batch_skipped = 0
                    batch_unchanged = 0
                    
                    for doc in documents:
                        # Check if document has the ID field
                        if id_field not in doc or doc[id_field] is None:
                            logging.warning(f"Document missing '{id_field}' field, skipping: {doc}")
                            batch_skipped += 1
                            continue
                        
                        doc_id = doc[id_field]
                        
                        # Check if siid exists in the document and if it already exists in collection
                        if 'siid' in doc and doc['siid'] is not None:
                            if self.check_value_exists(collection_name, 'siid', doc['siid']):
                                logging.debug(f"   ⏭️  Skipped document with siid={doc['siid']} (already exists)")
                                batch_skipped += 1
                                continue
                        
                        # Check if ean exists in the document and if it already exists in collection
                        if 'ean' in doc and doc['ean'] is not None:
                            if self.check_value_exists(collection_name, 'ean', doc['ean']):
                                logging.debug(f"   ⏭️  Skipped document with ean={doc['ean']} (already exists)")
                                batch_skipped += 1
                                continue
                        
                        # Check if gtin exists in the document and if it already exists in collection
                        if 'gtin' in doc and doc['gtin'] is not None:
                            if self.check_value_exists(collection_name, 'gtin', doc['gtin']):
                                logging.debug(f"   ⏭️  Skipped document with gtin={doc['gtin']} (already exists)")
                                batch_skipped += 1
                                continue
                        
                        # Find existing document by ID field
                        existing_doc = collection.find_one({id_field: doc_id})
                        
                        if existing_doc is None:
                            # Document doesn't exist, insert it
                            collection.insert_one(doc)
                            batch_inserted += 1
                            logging.debug(f"   ➕ Inserted new document with {id_field}={doc_id}")
                        else:
                            # Document exists, check for changes
                            has_changes = False
                            changed_fields = []
                            
                            # If watch_fields is empty or None, check all fields
                            if not watch_fields:
                                fields_to_check = [field for field in doc.keys() if field != '_id']
                                logging.debug(f"   🔍 No watch fields specified, checking all {len(fields_to_check)} fields")
                            else:
                                fields_to_check = watch_fields
                            
                            for field in fields_to_check:
                                # Skip MongoDB internal fields
                                if field == '_id':
                                    continue
                                    
                                # Get values from both documents
                                new_value = doc.get(field)
                                old_value = existing_doc.get(field)
                                
                                # Compare values (handle None and type differences)
                                if new_value != old_value:
                                    # Additional check for string representation (handle "None" vs None)
                                    if str(new_value) != str(old_value):
                                        has_changes = True
                                        changed_fields.append(field)
                                        logging.debug(f"   🔄 Field '{field}' changed: '{old_value}' → '{new_value}'")
                            
                            if has_changes:
                                # Update the entire document with the newest data
                                collection.replace_one(
                                    {id_field: doc_id},
                                    doc
                                )
                                batch_updated += 1
                                logging.debug(f"   🔄 Updated document with {id_field}={doc_id}, changed fields: {changed_fields}")
                            else:
                                # No changes detected, skip update
                                batch_unchanged += 1
                                logging.debug(f"   ⏭️  Skipped document with {id_field}={doc_id} (no changes)")
                    
                    inserted_count += batch_inserted
                    updated_count += batch_updated
                    skipped_count += batch_skipped
                    unchanged_count += batch_unchanged
                    
                    logging.info(f"Batch {batch_num + 1}: "
                               f"Inserted {batch_inserted}, "
                               f"Updated {batch_updated}, "
                               f"Unchanged {batch_unchanged}, "
                               f"Skipped {batch_skipped} documents")
                
                batch_end_time = time.time()
                batch_processing_time = batch_end_time - batch_start_time
                logging.info(f"Completed batch {batch_num + 1}/{total_batches} in {batch_processing_time:.2f} seconds")
            
            end_time = time.time()
            total_processing_time = end_time - start_time
            logging.info(f"📊 Summary for {csv_file_path}:")
            logging.info(f"   ✅ Inserted: {inserted_count} documents")
            logging.info(f"   🔄 Updated: {updated_count} documents")
            logging.info(f"   ⏭️  Unchanged: {unchanged_count} documents")
            logging.info(f"   ⚠️  Skipped: {skipped_count} documents")
            logging.info(f"   ⏱️  Total time: {total_processing_time:.2f} seconds")
        
        except Exception as e:
            logging.error(f"Error processing file {csv_file_path}: {e}")
            raise

    def create_indexes(self, collection_name: str, index_fields: List[str]):
        """
        Create indexes on specified fields for a collection.
        
        Args:
            collection_name (str): Name of the collection.
            index_fields (List[str]): List of field names to index.
        """
        try:
            collection = self.db[collection_name]
            for field in index_fields:
                collection.create_index(field)
                logging.info(f"Created index on field '{field}' in collection '{collection_name}'")
        except Exception as e:
            logging.error(f"Error creating indexes for collection {collection_name}: {e}")

    def ingest_all_csv_files(self, folder: str, batch_size: int = 1000, index_fields: List[str] = None, 
                            id_field: str = 'uuid', watch_fields: List[str] = None):
        """
        Ingest all CSV files from the processed data directory.
        Checks for existing documents by ID and only updates if specified fields have changed.
        
        Args:
            folder (str): Subfolder within processed data directory.
            batch_size (int): Number of rows to process in each batch.
            index_fields (List[str]): List of field names to create indexes on.
            id_field (str): Name of the field to use as unique identifier. Defaults to 'uuid'.
            watch_fields (List[str]): List of fields to watch for changes. 
                                     If None or empty, ALL fields will be checked for changes.
                                     Examples: ['price'], ['price', 'all_components'], or None for all fields.
        """
        if not self.connect_to_mongodb():
            logging.error("Cannot proceed without MongoDB connection")
            return False
        
        try:
            # Get CSV files
            csv_files = self.get_csv_files(folder=folder)
            if not csv_files:
                logging.warning("No CSV files found to process")
                return False
            
            # Process each CSV file
            for csv_file in csv_files:
                collection_name = Path(csv_file).stem
                self.process_csv_file(csv_file, collection_name, batch_size, id_field, watch_fields)
                
                # Create indexes if specified
                if index_fields:
                    self.create_indexes(collection_name, index_fields)
                
                logging.info(f"Completed processing: {csv_file}")
            
            logging.info("Successfully completed ingestion of all CSV files")
            return True
            
        except Exception as e:
            logging.error(f"Error during CSV ingestion: {e}")
            return False
        finally:
            self.close_connection()