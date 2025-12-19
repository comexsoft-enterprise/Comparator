"""
Product Verification Module

This module verifies products against MongoDB to determine:
- New products (not in MongoDB) → Insert in MongoDB and keep in output CSV
- Existing identical products (all uptable columns match) → Exclude from output CSV
- Modified products (exists but uptable columns differ) → Replace in MongoDB, delete from Neo4j, and keep in output CSV

The comparison is done using only 'uptable' columns defined in column_registry.json

Flow:
1. Read validated CSV products
2. For each product, check if exists in MongoDB by product_hash
3. If new → insert in MongoDB, keep in output CSV (will be ingested to Neo4j)
4. If identical → exclude from output CSV (already in both MongoDB and Neo4j)
5. If modified → replace in MongoDB, track for Neo4j deletion, keep in output CSV (will be re-ingested to Neo4j)
6. After processing all products, delete modified products from Neo4j in batch

Result: MongoDB contains ALL products (pre-translation state), Neo4j will be synced via re-ingestion
"""

import logging
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import json
import re

from config.settings import PROJECT_ROOT, MONGO_CONFIG
from src.connectors.mongodb_connector import get_mongo_client
from src.connectors.neo4j_connector import Neo4jConnector, NEO4J_DATABASE


class ProductVerifier:
    """
    Verifies products against MongoDB database and manages updates.
    """
    
    def __init__(self):
        """Initialize the Product Verifier."""
        self.mongo_db = None
        self.mongo_collection = None
        self.neo4j_connector = None
        self.neo4j_driver = None
        self.uptable_columns = self._load_uptable_columns()
        
        # Track modified products for Neo4j deletion
        self.modified_product_hashes = []
        
        # Statistics
        self.stats = {
            'total_products': 0,
            'new_products': 0,
            'modified_products': 0,
            'identical_products': 0,
            'errors': 0,
            'neo4j_deleted': 0
        }
    
    def _load_uptable_columns(self) -> List[str]:
        """
        Load the list of columns marked as 'uptable' from column_registry.json
        
        Returns:
            List[str]: Column names that are marked as uptable
        """
        try:
            registry_path = PROJECT_ROOT / "data" / "schemas" / "column_registry.json"
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry = json.load(f)
            
            uptable_cols = [
                col_name for col_name, col_def in registry.items()
                if col_def.get('uptable') == 'uptable'
            ]
            
            logging.info(f"Loaded {len(uptable_cols)} uptable columns for comparison")
            logging.debug(f"Uptable columns: {uptable_cols}")
            
            return uptable_cols
            
        except Exception as e:
            logging.error(f"Error loading uptable columns from column_registry: {e}")
            return []

    def _collection_name_from_filename(self, filepath: str) -> str:
        """
        Derive a collection name from an input filename with format: supermarket_postcode_XX
        where XX is 01, 02, 03, etc. based on existing collections.
        """
        try:
            p = Path(filepath)
            name = p.stem
            # Normalize
            name = name.strip().lower()

            # Remove common processing tags with optional trailing timestamps
            # e.g. _validated_20251202_0849, _verified_20251202, _enriched, _fixed_20251202
            name = re.sub(r'(_(validated|verified|translated|enriched|fixed))(_\d{8}(_\d{4,6})?)?$', '', name)

            # Remove trailing pure date patterns like _20251202 or _20251202_0849
            name = re.sub(r'[_-]\d{8}(_\d{4,6})?$', '', name)

            # Remove any leftover processing tags anywhere in the name
            name = re.sub(r'_(validated|verified|translated|enriched|fixed)', '', name)

            # Lowercase, replace spaces and non-alphanumeric with hyphen
            name = re.sub(r"[^0-9a-zA-Z-]+", "-", name)
            # Collapse multiple hyphens and strip
            name = re.sub(r"-+", "-", name).strip('-')

            # Expected format: supermarket_postcode (e.g., eroski-01013)
            base_name = name or 'products'
            
            # Find the next available version number
            return self._get_versioned_collection_name(base_name)
            
        except Exception:
            return 'products-01'
    
    def _get_versioned_collection_name(self, base_name: str) -> str:
        """
        Get versioned collection name in format: supermarket_postcode_XX
        Checks existing collections and returns next available version.
        
        Args:
            base_name: Base collection name (e.g., 'eroski-01013')
            
        Returns:
            Versioned collection name (e.g., 'eroski-01013-01', 'eroski-01013-02')
        """
        try:
            if self.mongo_db is None:
                # If DB not connected, return default -01
                return f"{base_name}-01"
            
            # Get all existing collection names
            try:
                existing_collections = self.mongo_db.list_collection_names()
            except Exception:
                try:
                    db = getattr(self.mongo_db, 'get_database')()
                    existing_collections = db.list_collection_names()
                except Exception:
                    # If we can't get collections, default to -01
                    return f"{base_name}-01"
            
            # Find all collections matching the base name pattern
            pattern = re.compile(rf'^{re.escape(base_name)}-(\d{{2}})$')
            existing_versions = []
            
            for coll_name in existing_collections:
                match = pattern.match(coll_name)
                if match:
                    version_num = int(match.group(1))
                    existing_versions.append(version_num)
            
            # If no existing versions, start with -01
            if not existing_versions:
                versioned_name = f"{base_name}-01"
                logging.info(f"Creating new collection: {versioned_name}")
                return versioned_name
            
            # Find the next available version number
            next_version = max(existing_versions) + 1
            # Use hyphen separator for consistency with initial '-01' format
            versioned_name = f"{base_name}-{next_version:02d}"
            logging.info(f"Found {len(existing_versions)} existing version(s). Creating new collection: {versioned_name}")
            return versioned_name
            
        except Exception as e:
            logging.warning(f"Error getting versioned collection name: {e}. Using default -01")
            return f"{base_name}-01"
    
    def connect_to_mongodb(self) -> bool:
        """
        Establish connection to MongoDB database.
        
        Returns:
            bool: True if connection successful, False otherwise.
        """
        try:
            database = MONGO_CONFIG.get('database', 'test')
            
            db = get_mongo_client()
            if db is None:
                logging.error("❌ Failed to get MongoDB client from connector")
                return False
            
            # Ensure we always have a Database reference
            if hasattr(db, 'get_collection'):
                # db is already a Database instance
                self.mongo_db = db
            else:
                # db is a MongoClient instance
                self.mongo_db = db.get_database(database)

            # Do not set a default collection here; collection selection will be
            # decided per-input file or per-product (store/brand) when inserting
            self.mongo_collection = None
            
            logging.info("✅ Successfully connected to MongoDB database")
            return True
            
        except Exception as e:
            logging.error(f"❌ Error connecting to MongoDB: {e}")
            return False
    
    def connect_to_neo4j(self) -> bool:
        """
        Establish connection to Neo4j database.
        
        Returns:
            bool: True if connection successful, False otherwise.
        """
        try:
            self.neo4j_connector = Neo4jConnector()
            if self.neo4j_connector.connect_to_neo4j():
                self.neo4j_driver = self.neo4j_connector.driver
                return True
            else:
                logging.warning("⚠️  Failed to connect to Neo4j database (will skip deletion)")
                return False
        except Exception as e:
            logging.warning(f"⚠️  Error connecting to Neo4j: {e} (will skip deletion)")
            return False
    
    def load_all_products_from_mongo(self, product_hashes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Load ALL products from MongoDB in a single batch query (optimized).

        Args:
            product_hashes (List[str]): List of all product hashes to load

        Returns:
            Dict[str, Dict[str, Any]]: Dictionary mapping product_hash to product document
        """
        mongo_products = {}
        
        try:
            if self.mongo_db is None:
                logging.error('MongoDB database not initialized')
                return mongo_products
            
            if not product_hashes:
                return mongo_products
            
            logging.info(f"🔄 Loading {len(product_hashes)} products from MongoDB in batch...")
            
            # Get all collection names
            coll_names = []
            try:
                coll_names = self.mongo_db.list_collection_names()
            except Exception:
                try:
                    db = getattr(self.mongo_db, 'get_database')()
                    coll_names = db.list_collection_names()
                except Exception:
                    coll_names = []
            
            # Query all collections with $in operator
            for coll_name in coll_names:
                try:
                    coll = self.mongo_db.get_collection(coll_name)
                    # Batch query using $in - retrieves all matching documents at once
                    cursor = coll.find({'product_hash': {'$in': product_hashes}})
                    
                    for doc in cursor:
                        doc.pop('_id', None)
                        product_hash = doc.get('product_hash')
                        if product_hash:
                            mongo_products[product_hash] = doc
                    
                    logging.debug(f"Loaded {len(mongo_products)} products from collection '{coll_name}'")
                    
                except Exception as e:
                    logging.warning(f"Error querying collection '{coll_name}': {e}")
                    continue
            
            logging.info(f"✅ Loaded {len(mongo_products)}/{len(product_hashes)} products from MongoDB")
            return mongo_products
            
        except Exception as e:
            logging.error(f"Error loading products from MongoDB in batch: {e}")
            return mongo_products
    
    def get_product_from_mongo(self, product_hash: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a product document from MongoDB by its product_hash.
        
        NOTE: This method is kept for backward compatibility but should not be used
        in loops. Use load_all_products_from_mongo() for batch operations.

        Args:
            product_hash (str): The product hash to search for

        Returns:
            Optional[Dict[str, Any]]: Document fields or None if not found
        """
        try:
            if self.mongo_db is None:
                logging.error('MongoDB database not initialized')
                return None

            # Iterate over all collections and return the first matching document
            try:
                coll_names = []
                try:
                    coll_names = self.mongo_db.list_collection_names()
                except Exception:
                    # Fallback: if mongo_db is a client, try to get default database collections
                    try:
                        db = getattr(self.mongo_db, 'get_database')()
                        coll_names = db.list_collection_names()
                    except Exception:
                        coll_names = []

                for coll_name in coll_names:
                    try:
                        coll = self.mongo_db.get_collection(coll_name)
                        doc = coll.find_one({'product_hash': product_hash})
                        if doc:
                            doc.pop('_id', None)
                            logging.debug(f"Found product in collection '{coll_name}': {product_hash}")
                            if logging.getLogger().isEnabledFor(logging.DEBUG):
                                sample_fields = [col for col in self.uptable_columns if col in doc][:3]
                                sample_values = {k: f"{type(doc[k]).__name__}:{doc[k]}" for k in sample_fields}
                                logging.debug(f"  Sample MongoDB values: {sample_values}")
                            return doc
                    except Exception:
                        # ignore collection-level errors and continue with next
                        continue

                logging.debug(f"Product not found in any MongoDB collection: {product_hash}")
                return None
            except Exception as e:
                logging.error(f"Error retrieving product {product_hash} from MongoDB: {e}")
                return None

        except Exception as e:
            logging.error(f"Error retrieving product {product_hash} from MongoDB: {e}")
            return None
    
    def compare_products(self, csv_product: Dict[str, Any], mongo_product: Dict[str, Any]) -> bool:
        """
        Compare uptable columns between CSV product and MongoDB product.
        
        Args:
            csv_product (Dict): Product data from CSV
            mongo_product (Dict): Product data from MongoDB
            
        Returns:
            bool: True if products are identical (in uptable columns), False if different
        """
        try:
            differences = []
            
            # Columns to exclude from comparison (always generated fresh)
            exclude_columns = {'uuid', 'modified_at'}
            
            # Only compare uptable columns that exist in the CSV product and are not excluded
            uptable_columns_in_csv = [
                col for col in self.uptable_columns 
                if col in csv_product and col not in exclude_columns
            ]
            
            logging.debug(f"Comparing product - CSV columns to check: {uptable_columns_in_csv}")
            
            for col in uptable_columns_in_csv:
                csv_value = csv_product.get(col)
                mongo_value = mongo_product.get(col)
                
                # Normalize None, empty string, NaN to None for comparison
                csv_value_normalized = self._normalize_value(csv_value)
                mongo_value_normalized = self._normalize_value(mongo_value)
                
                if csv_value_normalized != mongo_value_normalized:
                    differences.append({
                        'column': col,
                        'csv_value': csv_value_normalized,
                        'mongo_value': mongo_value_normalized
                    })
                    # Format None values as 'NULL' for better readability in logs
                    csv_display = 'NULL' if csv_value_normalized is None else f"'{csv_value_normalized}'"
                    mongo_display = 'NULL' if mongo_value_normalized is None else f"'{mongo_value_normalized}'"
                    logging.info(f"  Difference in '{col}': CSV={csv_display} vs MongoDB={mongo_display}")
            
            if differences:
                logging.info(f"Product {csv_product.get('product_hash', 'unknown')[:16]}... has {len(differences)} differences")
                return False
            else:
                return True
                
        except Exception as e:
            logging.error(f"Error comparing products: {e}")
            return False
    
    def _normalize_value(self, value):
        """Normalize value for comparison (handle None, NaN, empty strings, and float NaN).
        
        All these representations are considered equivalent to None:
        - pandas NaN (pd.isna returns True)
        - Empty string ''
        - String 'None'
        - String 'nan' or 'NaN' (case insensitive)
        - Float NaN values
        - None itself
        """
        # Check for pandas NaN, None, or float NaN
        if value is None or pd.isna(value):
            return None
        
        # Check for string representations of null/empty
        if isinstance(value, str):
            stripped = value.strip()
            # Empty string or string representations of null
            if stripped == '' or stripped.lower() in ('none', 'nan', 'null'):
                return None
            return stripped
        
        # Check for numeric NaN (shouldn't happen after pd.isna, but just in case)
        if isinstance(value, float):
            import math
            if math.isnan(value):
                return None
        
        return value
    
    def insert_product_in_mongo(self, product_hash: str, csv_product: Dict[str, Any]) -> bool:
        """
        Insert a new product into MongoDB.

        Args:
            product_hash (str): The product hash
            csv_product (Dict): Complete product data from CSV
            
        Returns:
            bool: True if insertion successful, False otherwise
        """
        try:
            if self.mongo_db is None:
                logging.error('MongoDB database not initialized')
                return False

            # Use the collection name determined at the start of verification
            # This ensures all products from the same file go to the same versioned collection
            collection_name = getattr(self, 'current_input_collection', None)
            if not collection_name:
                # Fallback: should not happen if verify_products() sets it correctly
                logging.warning("current_input_collection not set, using default 'products-01'")
                collection_name = 'products-01'

            try:
                collection = self.mongo_db.get_collection(collection_name)
            except Exception as e:
                logging.error(f"Error getting collection {collection_name}: {e}")
                return False

            # Prepare document to insert (normalize all values)
            new_document = {}
            for key, value in csv_product.items():
                normalized = self._normalize_value(value)
                new_document[key] = normalized

            # Add timestamp
            new_document['created_at'] = pd.Timestamp.utcnow().isoformat()

            # Insert new document
            result = collection.insert_one(new_document)

            return result.inserted_id is not None

        except Exception as e:
            logging.error(f"Error inserting product {product_hash} in MongoDB: {e}")
            return False
    
    def replace_product_in_mongo(self, product_hash: str, csv_product: Dict[str, Any]) -> bool:
        """
        Replace the entire MongoDB document for a product (delete old, insert new).
        This ensures the product in MongoDB exactly matches the CSV.

        Args:
            product_hash (str): The product hash to replace
            csv_product (Dict): Complete product data from CSV
            
        Returns:
            bool: True if replacement successful, False otherwise
        """
        try:
            if self.mongo_db is None:
                logging.error('MongoDB database not initialized')
                return False

            # Use the collection name determined at the start of verification
            # This ensures all products from the same file go to the same versioned collection
            collection_name = getattr(self, 'current_input_collection', None)
            if not collection_name:
                # Fallback: should not happen if verify_products() sets it correctly
                logging.warning("current_input_collection not set, using default 'products-01'")
                collection_name = 'products-01'

            try:
                collection = self.mongo_db.get_collection(collection_name)
            except Exception as e:
                logging.error(f"Error getting collection {collection_name}: {e}")
                return False

            # Prepare document to insert (normalize all values)
            new_document = {}
            for key, value in csv_product.items():
                normalized = self._normalize_value(value)
                # Store None as None in MongoDB (not string 'NaN')
                new_document[key] = normalized

            # Add timestamp
            new_document['modified_at'] = pd.Timestamp.utcnow().isoformat()

            # Use replace_one for atomic operation (upsert=False to ensure product exists)
            result = collection.replace_one(
                {'product_hash': product_hash},
                new_document,
                upsert=False
            )

            if result.matched_count > 0:
                logging.info(f"🔄 Replaced product in MongoDB: {product_hash}")
                return True
            else:
                # If product doesn't exist, insert it (this shouldn't happen in normal flow)
                logging.warning(f"⚠️  Product not found for replacement, inserting new: {product_hash}")
                insert_result = collection.insert_one(new_document)
                return insert_result.inserted_id is not None

        except Exception as e:
            logging.error(f"Error replacing product {product_hash} in MongoDB: {e}")
            return False
    
    def verify_product_with_cache(self, product_row: pd.Series, mongo_products_cache: Dict[str, Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Verify a single product using pre-loaded MongoDB cache (OPTIMIZED - no DB queries).
        
        Args:
            product_row (pd.Series): A row from the CSV containing product data
            mongo_products_cache (Dict): Pre-loaded products from MongoDB
            
        Returns:
            Tuple[bool, str]: (keep_in_output, reason)
                - keep_in_output: True if product should be in output CSV
                - reason: 'new', 'modified', 'identical', or 'error'
        """
        try:
            product_hash = product_row.get('product_hash')
            
            if pd.isna(product_hash) or product_hash == '':
                logging.warning(f"⚠️  Product without hash (SIID: {product_row.get('siid')}), keeping in output")
                return True, 'new'
            
            # Check if product exists in cache (no DB query!)
            mongo_product = mongo_products_cache.get(str(product_hash))
            
            if mongo_product is None:
                # Product doesn't exist in MongoDB → NEW → Insert and keep in output
                logging.debug(f"🆕 New product (not in MongoDB): {product_hash}")
                
                # Insert new product into MongoDB
                csv_product = product_row.to_dict()
                inserted = self.insert_product_in_mongo(product_hash, csv_product)
                if inserted:
                    logging.info(f"✅ Inserted new product in MongoDB: {product_hash}")
                else:
                    logging.warning(f"⚠️  Failed to insert new product in MongoDB: {product_hash}")
                
                self.stats['new_products'] += 1
                return True, 'new'

            # Product exists in MongoDB → Compare uptable columns
            csv_product = product_row.to_dict()
            is_identical = self.compare_products(csv_product, mongo_product)
            
            if is_identical:
                # Products are identical → EXCLUDE from output (no need to re-ingest)
                logging.debug(f"✓ Identical product: {product_hash}")
                self.stats['identical_products'] += 1
                return False, 'identical'
            else:
                # Products differ → MODIFIED → Replace in MongoDB, track for Neo4j deletion, keep in output
                logging.info(f"🔄 Modified product: {product_hash}")
                
                # Replace product in MongoDB with new CSV data
                replaced = self.replace_product_in_mongo(product_hash, csv_product)
                if replaced:
                    logging.info(f"✅ Replaced modified product in MongoDB: {product_hash}")
                else:
                    logging.warning(f"⚠️  Failed to replace modified product in MongoDB: {product_hash}")
                
                # Track for Neo4j deletion (will be deleted in batch later)
                self.modified_product_hashes.append(product_hash)
                
                self.stats['modified_products'] += 1
                return True, 'modified'
                
        except Exception as e:
            logging.error(f"❌ Error verifying product: {e}")
            self.stats['errors'] += 1
            return True, 'error'
    
    def verify_product(self, product_row: pd.Series) -> Tuple[bool, str]:
        """
        Verify a single product against MongoDB database.
        
        NOTE: This method is kept for backward compatibility but is SLOW.
        Use verify_product_with_cache() with load_all_products_from_mongo() for better performance.
        
        Args:
            product_row (pd.Series): A row from the CSV containing product data
            
        Returns:
            Tuple[bool, str]: (keep_in_output, reason)
                - keep_in_output: True if product should be in output CSV
                - reason: 'new', 'modified', 'identical', or 'error'
        """
        try:
            product_hash = product_row.get('product_hash')
            
            if pd.isna(product_hash) or product_hash == '':
                logging.warning(f"⚠️  Product without hash (SIID: {product_row.get('siid')}), keeping in output")
                return True, 'new'
            
            # Check if product exists in MongoDB
            mongo_product = self.get_product_from_mongo(product_hash)

            if mongo_product is None:
                # Product doesn't exist in MongoDB → NEW → Insert and keep in output
                logging.debug(f"🆕 New product (not in MongoDB): {product_hash}")
                
                # Insert new product into MongoDB
                csv_product = product_row.to_dict()
                inserted = self.insert_product_in_mongo(product_hash, csv_product)
                if inserted:
                    logging.info(f"✅ Inserted new product in MongoDB: {product_hash}")
                else:
                    logging.warning(f"⚠️  Failed to insert new product in MongoDB: {product_hash}")
                
                self.stats['new_products'] += 1
                return True, 'new'

            # Product exists in MongoDB → Compare uptable columns
            csv_product = product_row.to_dict()
            is_identical = self.compare_products(csv_product, mongo_product)
            
            if is_identical:
                # Products are identical → EXCLUDE from output (no need to re-ingest)
                logging.debug(f"✓ Identical product: {product_hash}")
                self.stats['identical_products'] += 1
                return False, 'identical'
            else:
                # Products are different → REPLACE in MongoDB and KEEP in output
                logging.info(f"🔄 Modified product (detected vs MongoDB): {product_hash}")

                replaced = self.replace_product_in_mongo(product_hash, csv_product)
                if replaced:
                    # Track this product for Neo4j deletion later
                    self.modified_product_hashes.append(product_hash)
                    self.stats['modified_products'] += 1
                    return True, 'modified'
                else:
                    logging.error(f"❌ Failed to replace modified product in MongoDB: {product_hash}")
                    self.stats['errors'] += 1
                    return True, 'error'
                    
        except Exception as e:
            logging.error(f"❌ Error verifying product: {e}")
            self.stats['errors'] += 1
            return True, 'error'
    
    def verify_csv(self, input_csv_path: str, output_csv_path: str) -> bool:
        """
        Verify all products in a CSV file against MongoDB (OPTIMIZED with batch loading).
        
        Args:
            input_csv_path (str): Path to input CSV (validated)
            output_csv_path (str): Path to output CSV (verified)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logging.info(f"📂 Reading CSV: {input_csv_path}")
            df = pd.read_csv(input_csv_path, sep=";")
            self.stats['total_products'] = len(df)
            logging.info(f"📊 Total products to verify: {len(df)}")
            
            # OPTIMIZATION: Load ALL products from MongoDB in ONE batch query
            all_hashes = df['product_hash'].dropna().astype(str).tolist()
            mongo_products_cache = self.load_all_products_from_mongo(all_hashes)
            logging.info(f"✅ Cached {len(mongo_products_cache)} products from MongoDB")
            
            # Verify each product (now using in-memory cache, no DB queries)
            products_to_keep = []
            
            for idx, row in df.iterrows():
                keep_in_output, reason = self.verify_product_with_cache(row, mongo_products_cache)
                
                if keep_in_output:
                    products_to_keep.append(row)
                
                # Log progress every 100 products
                if (idx + 1) % 100 == 0:
                    logging.info(f"Progress: {idx + 1}/{len(df)} products verified")
            
            # Create output DataFrame
            if products_to_keep:
                output_df = pd.DataFrame(products_to_keep)
                
                # Ensure output directory exists
                output_path = Path(output_csv_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Save to CSV
                output_df.to_csv(output_csv_path, sep=";", index=False)
                logging.info(f"✅ Verified CSV saved to: {output_csv_path}")
                logging.info(f"📊 Products in output CSV: {len(output_df)}")
            else:
                logging.info("ℹ️  No products to keep in output CSV (all were identical)")
                # Create empty CSV with same structure
                output_path = Path(output_csv_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                df.head(0).to_csv(output_csv_path, sep=";", index=False)
            
            # Delete modified products from Neo4j (they will be re-ingested)
            if self.modified_product_hashes:
                self.delete_modified_products_from_neo4j()
            
            return True
            
        except Exception as e:
            logging.error(f"❌ Error verifying CSV: {e}")
            return False
    
    def delete_modified_products_from_neo4j(self):
        """Delete all modified products from Neo4j in batch."""
        if not self.modified_product_hashes:
            return
        
        logging.info(f"\n🗑️  Deleting {len(self.modified_product_hashes)} modified products from Neo4j...")
        
        # Try to connect to Neo4j if not already connected
        if not self.neo4j_driver:
            if not self.connect_to_neo4j():
                logging.warning("⚠️  Cannot delete from Neo4j (connection failed), products will be re-ingested as duplicates")
                return
        
        try:
            # Use a single UNWIND query to delete all matching Product nodes and their relationships
            with self.neo4j_driver.session(database=NEO4J_DATABASE) as session:
                query = """
                UNWIND $hashes AS h
                MATCH (p:Product {product_hash: h})
                WITH collect(distinct p) AS nodes, [x IN collect(distinct p) | x.product_hash] AS hashes_found
                FOREACH (n IN nodes | DETACH DELETE n)
                RETURN size(nodes) AS deleted, hashes_found AS deleted_hashes
                """
                try:
                    result = session.run(query, hashes=self.modified_product_hashes)
                    record = result.single()
                    if record is None:
                        deleted_count = 0
                        deleted_hashes = []
                    else:
                        deleted_count = int(record.get('deleted', 0) or 0)
                        deleted_hashes = record.get('deleted_hashes', []) or []

                    # Log details
                    logging.info(f"✅ Deleted {deleted_count}/{len(self.modified_product_hashes)} modified products from Neo4j")
                    if deleted_hashes:
                        for h in deleted_hashes:
                            logging.debug(f"  Deleted node with product_hash: {h}")

                    # Determine which hashes were not found/deleted
                    not_deleted = [h for h in self.modified_product_hashes if h not in deleted_hashes]
                    if not_deleted:
                        logging.warning(f"⚠️  The following product_hashes were not found in Neo4j and could not be deleted: {not_deleted}")

                    self.stats['neo4j_deleted'] = deleted_count

                except Exception as e:
                    logging.error(f"❌ Error executing Neo4j deletion query: {e}")
                    # Fall back to per-hash deletion attempts
                    deleted_count = 0
                    for product_hash in self.modified_product_hashes:
                        try:
                            query = """
                            MATCH (p:Product {product_hash: $product_hash})
                            DETACH DELETE p
                            """
                            result = session.run(query, product_hash=product_hash)
                            summary = result.consume()
                            if summary.counters.nodes_deleted > 0:
                                deleted_count += 1
                                logging.debug(f"  Deleted {product_hash} from Neo4j (fallback)")
                        except Exception as e2:
                            logging.warning(f"  Failed to delete {product_hash} from Neo4j (fallback): {e2}")

                    self.stats['neo4j_deleted'] = deleted_count
                    logging.info(f"✅ Deleted {deleted_count}/{len(self.modified_product_hashes)} modified products from Neo4j (fallback)")
            
        except Exception as e:
            logging.error(f"❌ Error during Neo4j batch deletion: {e}")
    
    def print_summary(self):
        """Print verification summary statistics."""
        logging.info("\n" + "="*60)
        logging.info("📊 PRODUCT VERIFICATION SUMMARY")
        logging.info("="*60)
        logging.info(f"Total products processed: {self.stats['total_products']}")
        logging.info(f"🆕 New products (inserted in MongoDB & kept in CSV):     {self.stats['new_products']}")
        logging.info(f"🔄 Modified products (updated in MongoDB & kept in CSV): {self.stats['modified_products']}")
        logging.info(f"✓  Identical products (excluded from CSV):               {self.stats['identical_products']}")
        logging.info(f"🗑️  Modified products deleted from Neo4j:                 {self.stats['neo4j_deleted']}")
        logging.info(f"❌ Errors: {self.stats['errors']}")
        logging.info(f"📤 Total products in output CSV: {self.stats['new_products'] + self.stats['modified_products']}")
        logging.info("="*60 + "\n")
    
    def close(self):
        """Close database connections."""
        if self.neo4j_connector:
            self.neo4j_connector.close_connection()
            logging.info("🔌 Neo4j connection closed")
        logging.info("🔌 MongoDB connection will be closed by connector")


def verify_products(input_csv_path: str, output_csv_path: str) -> bool:
    """
    Main function to verify products from CSV against MongoDB.
    
    Args:
        input_csv_path (str): Path to input CSV (validated)
        output_csv_path (str): Path to output CSV (verified)
        
    Returns:
        bool: True if successful, False otherwise
    """
    verifier = ProductVerifier()
    
    try:
        # Connect to MongoDB
        if not verifier.connect_to_mongodb():
            logging.error("❌ Cannot proceed without MongoDB connection")
            return False
        # Derive collection name from input filename and store it on verifier
        verifier.current_input_collection = verifier._collection_name_from_filename(input_csv_path)

        # Verify products
        success = verifier.verify_csv(input_csv_path, output_csv_path)
        
        # Print summary
        verifier.print_summary()
        
        return success
        
    except Exception as e:
        logging.error(f"❌ Error in product verification: {e}")
        return False
        
    finally:
        verifier.close()
