"""
Neo4j CSV Data Ingestion & Similarity Module

This module provides functionality to connect to a Neo4j database, run health checks,
search products, and compute product similarities using graph structure (neighbors)
and optional text-embedding similarity.

Schema assumptions (aligned to your current DB):
- Product nodes connect to:
  - Brand via        :HAS_BRAND
  - Format via       :HAS_FORMAT
  - Product_type via :RELATED_TO_PRODUCT_TYPE
  - Store via        :RELATED_TO_STORE
- There is NO Category label in the graph.
- There is NO :SOLD_AT relationship; stores use :RELATED_TO_STORE.

Author: you
"""

import logging
import os
import sys
import pandas as pd
import time
import json
import pickle
import hashlib
from pathlib import Path
from typing import List, Dict, Any
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configure logging with colors and proper formatting
from config.logs import setup_logging
setup_logging(level=logging.INFO)

from config.settings import PROJECT_ROOT
from src.connectors.neo4j_connector import get_neo4j_driver

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# Azure OpenAI configuration
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = "text-embedding-3-large"

# Default weights (aligned to existing labels)
DEFAULT_WEIGHTS = {
    "Brand": 0.3,
    "Format": 0.15,
    "Product_type": 0.60,
    "Unit_measure": 0.10,
    "First_level_ingredient": 0.20,
    "Second_level_ingredient": 0.10,
    "Allergen": 0.05
}


class Neo4jController:
    """
    Handle connections and queries to a Neo4j database.
    Includes:
    - Connection management
    - Basic health checks
    - Product search and detail retrieval
    - Similarity search (graph-based + optional embedding)
    - Cross-store similarity mapping
    """

    def __init__(self, processed_data_dir: str = None, openai_api_key: str = None, azure_endpoint: str = None):
        self.driver = None
        self.processed_data_dir = processed_data_dir or str(PROJECT_ROOT / "data" / "processed")
        self.openai_client = None
        self.azure_deployment = AZURE_OPENAI_DEPLOYMENT
        
        # Initialize embeddings cache
        self.embeddings_cache_path = PROJECT_ROOT / "data" / "processed" / "embeddings_cache.pkl"
        self.embeddings_cache = {}
        self.cache_lock = Lock()  # Thread-safe lock for cache access
        self.load_embeddings_cache()
        
        # Initialize OpenAI client (Azure OpenAI)
        endpoint = azure_endpoint or AZURE_OPENAI_ENDPOINT
        api_key = openai_api_key or AZURE_OPENAI_API_KEY
        
        if not api_key:
            raise ValueError("OpenAI API key is required. Set AZURE_OPENAI_API_KEY environment variable or pass openai_api_key parameter.")
        
        self.openai_client = OpenAI(
            base_url=endpoint,
            api_key=api_key
        )
        logging.info(f"✅ Azure OpenAI client initialized with endpoint: {endpoint}")
        logging.info(f"✅ Using deployment: {self.azure_deployment}")

    def connect_to_neo4j(self) -> bool:
        """Establish connection to Neo4j database."""
        try:
            self.driver = get_neo4j_driver(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)
            if self.driver:
                logging.info("Successfully connected to Neo4j database")
                return True
            logging.error("Failed to connect to Neo4j database")
            return False
        except Exception as e:
            logging.error(f"Error connecting to Neo4j: {e}")
            return False

    def close_connection(self):
        """Close the Neo4j driver connection."""
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
                'error': 'Driver not initialized. Call connect_to_neo4j() first.'
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
            logging.error("Driver not initialized. Call connect_to_neo4j() first.")
            return []
        try:
            with self.driver.session(database=NEO4J_DATABASE) as session:
                result = session.run(query, parameters or {})
                return [dict(record) for record in result]
        except Exception as e:
            logging.error(f"Error executing query: {e}")
            return []

    # -------------------------------
    # Embedding cache management
    # -------------------------------
    def _text_to_cache_key(self, text: str) -> str:
        """
        Generate a consistent cache key from text using SHA256 hash.
        This allows us to quickly look up if we've already computed an embedding for this text.
        """
        return hashlib.sha256(text.encode('utf-8')).hexdigest()
    
    def load_embeddings_cache(self):
        """
        Load embeddings cache from pickle file with retry logic.
        Cache format: {text_hash: [embedding_vector]}
        Pickle is ~10x faster than JSON for numerical data.
        """
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                if self.embeddings_cache_path.exists():
                    # Check file size before opening
                    file_size = self.embeddings_cache_path.stat().st_size
                    
                    if file_size == 0:
                        logging.warning(f"⚠️ Cache file is empty, starting fresh")
                        self.embeddings_cache = {}
                        return
                    
                    with open(self.embeddings_cache_path, 'rb') as f:
                        self.embeddings_cache = pickle.load(f)
                    
                    logging.info(f"✅ Loaded {len(self.embeddings_cache)} embeddings from cache: {self.embeddings_cache_path}")
                    return
                else:
                    logging.info(f"📝 No existing embeddings cache found. Will create new cache at: {self.embeddings_cache_path}")
                    self.embeddings_cache = {}
                    return
                    
            except (pickle.UnpicklingError, EOFError) as e:
                logging.error(f"❌ Cache file corrupted (pickle error): {e}")
                if attempt < max_retries - 1:
                    logging.info(f"   Retrying... (attempt {attempt + 2}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.warning(f"   Starting with empty cache")
                    self.embeddings_cache = {}
                    return
                    
            except Exception as e:
                logging.error(f"❌ Error loading embeddings cache: {e}")
                if attempt < max_retries - 1:
                    logging.info(f"   Retrying... (attempt {attempt + 2}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.warning(f"   Starting with empty cache")
                    self.embeddings_cache = {}
                    return
    
    def save_embeddings_cache(self):
        """
        Save embeddings cache to pickle file using atomic write.
        Uses temporary file and rename to prevent corruption during concurrent access.
        Thread-safe: creates a snapshot of the cache before saving.
        Pickle is ~10x faster than JSON for numerical data.
        """
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                # Ensure directory exists
                self.embeddings_cache_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Create a snapshot of the cache (thread-safe)
                with self.cache_lock:
                    cache_snapshot = dict(self.embeddings_cache)
                
                # Write to temporary file first (atomic operation)
                temp_path = self.embeddings_cache_path.with_suffix('.tmp')
                
                with open(temp_path, 'wb') as f:
                    pickle.dump(cache_snapshot, f, protocol=pickle.HIGHEST_PROTOCOL)
                    f.flush()  # Ensure data is written
                    os.fsync(f.fileno())  # Force write to disk
                
                # Create backup of existing cache if it exists
                if self.embeddings_cache_path.exists():
                    backup_path = self.embeddings_cache_path.with_suffix('.bak')
                    try:
                        import shutil
                        shutil.copy2(self.embeddings_cache_path, backup_path)
                    except Exception:
                        pass  # Backup is optional
                
                # Atomic rename (overwrites existing file)
                import shutil
                shutil.move(str(temp_path), str(self.embeddings_cache_path))
                
                logging.info(f"💾 Saved {len(cache_snapshot)} embeddings to cache: {self.embeddings_cache_path}")
                return
                
            except Exception as e:
                logging.error(f"❌ Error saving embeddings cache: {e}")
                if attempt < max_retries - 1:
                    logging.info(f"   Retrying... (attempt {attempt + 2}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.error(f"   Failed to save cache after {max_retries} attempts")
                    # Try to clean up temp file
                    try:
                        temp_path = self.embeddings_cache_path.with_suffix('.tmp')
                        if temp_path.exists():
                            temp_path.unlink()
                    except Exception:
                        pass

    # -------------------------------
    # Product utilities
    # -------------------------------
    def search_products_by_name(self, search_term: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for products by name/description (case-insensitive).
        Uses product_name (not name) to match your schema.
        """
        query = """
        MATCH (p:Product)
        WHERE toLower(coalesce(p.product_name, '')) CONTAINS toLower($search_term)
           OR toLower(coalesce(p.description, ''))   CONTAINS toLower($search_term)
        RETURN p.uuid AS uuid, 
               p.product_name AS name, 
               p.description AS description,
               p.price AS price,
               p.brand AS brand
        LIMIT $limit
        """
        return self.execute_query(query, {"search_term": search_term, "limit": limit})

    def get_product_details(self, product_id: int, print_details: bool = False) -> Dict[str, Any]:
        """
        Get detailed information about a product including its relationships.
        (No Category; uses Format, Brand, Product_type, Store)
        """
        query = """
        MATCH (p:Product {id: $product_id})
        OPTIONAL MATCH (p)-[:HAS_FORMAT]-(fmt:Format)
        OPTIONAL MATCH (p)-[:HAS_BRAND]-(b:Brand)
        OPTIONAL MATCH (p)-[:RELATED_TO_PRODUCT_TYPE]-(pt:Product_type)
        OPTIONAL MATCH (p)-[:RELATED_TO_STORE]-(s:Store)
        RETURN p.id AS id,
               p.uuid AS uuid,
               p.product_name AS product_name,
               p.description AS description,
               p.price AS price,
               b.name AS brand,
               fmt.name AS format,
               pt.name AS product_type,
               s.name AS store
        """
        results = self.execute_query(query, {"product_id": product_id})
        details = results[0] if results else {}

        if print_details:
            print(f"\n  📊 Target Product Details (ID: {product_id}):")
            if details:
                print(f"    UUID: {details.get('uuid', 'N/A')}")
                print(f"    Product Name: {details.get('product_name', 'N/A')}")
                print(f"    Description: {details.get('description', 'N/A')}")
                print(f"    Price: {details.get('price', 'N/A')}")
                print(f"    Brand: {details.get('brand', 'N/A')}")
                print(f"    Product Type: {details.get('product_type', 'N/A')}")
                print(f"    Format: {details.get('format', 'N/A')}")
                print(f"    Store: {details.get('store', 'N/A')}")
            else:
                print(f"    ❌ Product with ID {product_id} not found!")

        return details

    
    # -------------------------------
    # Embedding similarity with OpenAI
    # -------------------------------

    def generate_embeddings(self, texts: List[str], max_workers: int = 100) -> np.ndarray:
        """
        Generate embeddings for a list of texts using OpenAI API.
        Uses cache to avoid regenerating embeddings for the same text.
        Uses multithreading for parallel API calls.
        
        Args:
            texts: List of text strings to embed
            max_workers: Maximum number of parallel workers for API calls (default: 100)
            
        Returns:
            numpy array of embeddings, shape (len(texts), embedding_dim)
        """
        embeddings = []
        texts_to_generate = []
        indices_to_generate = []
        cache_hits = 0
        cache_misses = 0
        
        # Check cache for each text (thread-safe)
        with self.cache_lock:
            for i, text in enumerate(texts):
                cache_key = self._text_to_cache_key(text)
                
                if cache_key in self.embeddings_cache:
                    # Cache hit - use stored embedding
                    embeddings.append(self.embeddings_cache[cache_key])
                    cache_hits += 1
                else:
                    # Cache miss - need to generate
                    embeddings.append(None)  # Placeholder
                    texts_to_generate.append(text)
                    indices_to_generate.append(i)
                    cache_misses += 1
        
        # Log cache statistics
        if cache_hits > 0 or cache_misses > 0:
            logging.info(f"📊 Cache stats: {cache_hits} hits, {cache_misses} misses ({cache_hits/(cache_hits+cache_misses)*100:.1f}% hit rate)")
        
        # Generate embeddings for cache misses
        if texts_to_generate:
            logging.info(f"🤖 Generating {len(texts_to_generate)} new OpenAI embeddings with {max_workers} workers...")
            start_time = time.time()
            
            new_embeddings = [None] * len(texts_to_generate)
            
            def generate_single_embedding(idx_text_tuple):
                """Generate embedding for a single text"""
                idx, text = idx_text_tuple
                try:
                    response = self.openai_client.embeddings.create(
                        model=self.azure_deployment,
                        input=text
                    )
                    return idx, response.data[0].embedding, None
                except Exception as e:
                    return idx, None, str(e)
            
            # Use ThreadPoolExecutor for parallel API calls
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                futures = {
                    executor.submit(generate_single_embedding, (idx, text)): idx 
                    for idx, text in enumerate(texts_to_generate)
                }
                
                # Collect results as they complete
                completed = 0
                errors = []
                
                # Progress bar setup
                total = len(texts_to_generate)
                print(f"\n🔄 Generating embeddings: [", end="", flush=True)
                progress_width = 50
                last_progress = -1
                
                for future in as_completed(futures):
                    idx, embedding, error = future.result()
                    if error:
                        errors.append(f"Text {idx}: {error}")
                    else:
                        new_embeddings[idx] = embedding
                    completed += 1
                    
                    # Update progress bar
                    current_progress = int((completed / total) * progress_width)
                    if current_progress > last_progress:
                        print("=" * (current_progress - last_progress), end="", flush=True)
                        last_progress = current_progress
                    
                    # Progress log every 10 embeddings or at completion
                    if completed % 10 == 0 or completed == total:
                        pct = (completed / total) * 100
                        print(f"] {completed}/{total} ({pct:.1f}%)", end="\r", flush=True)
                
                print(f"] {total}/{total} (100.0%) ✅\n", flush=True)
            
            if errors:
                logging.warning(f"⚠️ {len(errors)} errors during embedding generation")
                for error in errors[:5]:  # Show first 5 errors
                    logging.warning(f"   {error}")
            
            # Filter out None values (failed embeddings)
            new_embeddings = [emb for emb in new_embeddings if emb is not None]
            
            elapsed_time = time.time() - start_time
            logging.info(f"✅ Generated {len(new_embeddings)} OpenAI embeddings in {elapsed_time:.2f}s ({len(new_embeddings)/elapsed_time:.1f} embeddings/s)")
            
            # Store new embeddings in cache and result list (thread-safe)
            with self.cache_lock:
                for i, text, embedding in zip(indices_to_generate, texts_to_generate, new_embeddings):
                    cache_key = self._text_to_cache_key(text)
                    self.embeddings_cache[cache_key] = embedding
                    embeddings[i] = embedding
            
            # Note: Cache will be saved at the end of the cross-store analysis
            # to avoid frequent disk writes
        
        # Convert to numpy array
        embeddings_array = np.array(embeddings)
        return embeddings_array

    def calculate_embedding_similarity(
        self,
        target_product_id: int,
        similar_products: List[Dict[str, Any]],
        distance_metric: str = 'cosine'
    ) -> List[Dict[str, Any]]:
        """
        Add embedding similarity between the target product name/description
        and each candidate; compute a combined score with separate weights.
        Uses OpenAI embeddings API.
        
        Weights:
        - Graph score: 0.4
        - Name similarity: 0.4
        - Description similarity: 0.2
        
        Args:
            target_product_id: ID of the target product
            similar_products: List of candidate products
            distance_metric: Distance metric to use
                - 'cosine': Cosine similarity (default, range 0-1)
                - 'euclidean': Euclidean distance (converted to similarity 0-1)
                - 'dot_product': Dot product similarity (with normalized embeddings)
                - 'manhattan': Manhattan distance (converted to similarity 0-1)
        
        Returns:
            List of products with similarity scores added
        """
        if not similar_products:
            logging.warning("No similar products provided for embedding calculation")
            return []

        # Get target product name and description
        target_product = self.get_product_details(target_product_id, print_details=False)
        target_name = target_product.get('product_name', '')
        target_description = target_product.get('description', '')

        if not target_name and not target_description:
            logging.warning(f"Target product {target_product_id} has no name or description")
            return similar_products

        # Generate separate embeddings for target name and description
        logging.info(f"Generating separate embeddings for target product name and description using {distance_metric} distance...")
        target_name_embedding = None
        target_desc_embedding = None
        
        if target_name:
            target_name_embedding = self.generate_embeddings([target_name])[0]
        if target_description:
            target_desc_embedding = self.generate_embeddings([target_description])[0]

        # Prepare candidate names and descriptions
        candidate_names = []
        candidate_descriptions = []
        valid_indices = []
        
        for i, product in enumerate(similar_products):
            name = product.get('product_name', '')
            desc = product.get('description', '')
            
            if name or desc:
                candidate_names.append(name if name else '')
                candidate_descriptions.append(desc if desc else '')
                valid_indices.append(i)

        if not valid_indices:
            logging.warning("No valid names or descriptions found in similar products")
            return similar_products

        logging.info(f"Generating embeddings for {len(valid_indices)} similar products (names and descriptions)...")
        
        # Generate embeddings for candidate names and descriptions
        name_embeddings = None
        desc_embeddings = None
        
        if target_name_embedding is not None and any(candidate_names):
            # Filter out empty names for embedding generation
            non_empty_names = [name if name else target_name for name in candidate_names]
            name_embeddings = self.generate_embeddings(non_empty_names)
        
        if target_desc_embedding is not None and any(candidate_descriptions):
            # Filter out empty descriptions for embedding generation
            non_empty_descs = [desc if desc else target_description for desc in candidate_descriptions]
            desc_embeddings = self.generate_embeddings(non_empty_descs)

        # Helper function to calculate similarity based on metric
        def calculate_similarity(target_emb, candidate_embs, metric):
            if metric == 'cosine':
                return cosine_similarity([target_emb], candidate_embs)[0]
            elif metric == 'euclidean':
                from sklearn.metrics.pairwise import euclidean_distances
                distances = euclidean_distances([target_emb], candidate_embs)[0]
                max_dist = distances.max() if distances.max() > 0 else 1
                return 1 - (distances / max_dist)
            elif metric == 'dot_product':
                target_norm = target_emb / np.linalg.norm(target_emb)
                candidates_norm = candidate_embs / np.linalg.norm(candidate_embs, axis=1, keepdims=True)
                return np.dot(candidates_norm, target_norm)
            elif metric == 'manhattan':
                from sklearn.metrics.pairwise import manhattan_distances
                distances = manhattan_distances([target_emb], candidate_embs)[0]
                max_dist = distances.max() if distances.max() > 0 else 1
                return 1 - (distances / max_dist)
            else:
                return cosine_similarity([target_emb], candidate_embs)[0]

        # Calculate name and description similarities
        name_similarities = None
        desc_similarities = None
        
        if name_embeddings is not None and target_name_embedding is not None:
            name_similarities = calculate_similarity(target_name_embedding, name_embeddings, distance_metric)
            logging.info(f"Using {distance_metric} similarity for product names")
        
        if desc_embeddings is not None and target_desc_embedding is not None:
            desc_similarities = calculate_similarity(target_desc_embedding, desc_embeddings, distance_metric)
            logging.info(f"Using {distance_metric} similarity for descriptions")

        # Add similarity scores to products
        graph_weight = 0.4   # weight for graph-based score
        name_weight = 0.4    # weight for name similarity
        desc_weight = 0.2    # weight for description similarity
        
        for idx_pos, idx in enumerate(valid_indices):
            graph_score = float(similar_products[idx].get('weighted_score', 0.0))
            
            # Get individual similarity scores
            name_sim = float(name_similarities[idx_pos]) if name_similarities is not None else 0.0
            desc_sim = float(desc_similarities[idx_pos]) if desc_similarities is not None else 0.0
            
            # Store individual scores
            similar_products[idx]['name_similarity'] = name_sim
            similar_products[idx]['description_similarity'] = desc_sim
            similar_products[idx]['distance_metric'] = distance_metric
            
            # Calculate weighted average of name and description for backward compatibility
            if name_sim > 0.0 and desc_sim > 0.0:
                # Both available: weighted average (name=0.67, desc=0.33)
                embedding_sim = (0.67 * name_sim) + (0.33 * desc_sim)
            elif name_sim > 0.0:
                embedding_sim = name_sim
            elif desc_sim > 0.0:
                embedding_sim = desc_sim
            else:
                embedding_sim = 0.0
            
            similar_products[idx]['embedding_similarity'] = embedding_sim
            
            # Combined score: graph=0.4, name=0.4, description=0.2
            combined = (graph_weight * graph_score) + (name_weight * name_sim) + (desc_weight * desc_sim)
            similar_products[idx]['combined_score'] = float(combined)

        # Sort by combined score desc
        similar_products.sort(key=lambda x: x.get('combined_score', 0.0), reverse=True)
        logging.info(f"✅ Separate embedding similarity calculated successfully using {distance_metric}")
        logging.info(f"   Weights: Graph={graph_weight}, Name={name_weight}, Description={desc_weight}")

        return similar_products

    # -------------------------------
    # Cross-store similarity
    # -------------------------------
    def _process_single_product_a(
        self,
        product_a: Dict[str, Any],
        store_b: str,
        min_matches: int,
        weights: dict,
        idx: int,
        total: int,
        category_filter: str = None,
        score_threshold: float = 0.15
    ) -> tuple:
        """
        Process a single product from store A to find similar products in store B.
        This method is designed to be called in parallel for multiple products A.
        
        Args:
            product_a: Product data from store A
            store_b: Name of destination store
            min_matches: Minimum number of shared neighbors
            weights: Weight configuration
            idx: Current product index (for logging)
            total: Total number of products (for logging)
            category_filter: Optional category filter
            score_threshold: Maximum score difference from best match (default: 0.15)
            
        Returns:
            Tuple of (product_a_id, product_a, similar_products_b)
        """
        product_a_id = product_a['id']
        product_a_name = product_a.get('product_name', 'N/A')
        logging.info(f"[{idx}/{total}] Finding similar products for: {product_a_name} (ID: {product_a_id})")

        # First, get the nodes that product A is connected to
        query_neighbors = """
        MATCH (a:Product {id: $product_id})-[r]-(n)
        RETURN labels(n) AS node_labels, n.name AS node_name, type(r) AS relationship_type
        """
        neighbors = self.execute_query(query_neighbors, {"product_id": product_a_id})
        
        # Extract Product_type to narrow the search domain
        product_type_name = None
        if neighbors:
            for neighbor in neighbors:
                if 'Product_Type' in neighbor['node_labels']:
                    product_type_name = neighbor['node_name']
                    logging.info(f"  🎯 Using Product_type: {product_type_name} to narrow search")
                    break

        query_similar = """
        // 1) Start product neighbors - capture nodes AND relationship types
        MATCH (a:Product {id: $product_id})
        MATCH (a)-[rel_a]-(x)
        WITH a, collect(DISTINCT {node: x, relType: type(rel_a)}) AS neighborsA

        // 2) Products in store_b sharing neighbors (narrowed by Product_type if available)
        MATCH (b:Product) <-[:SELLS]-(sb:Store {name: $store_b})
        """ + (""" 
        MATCH (b)<-[:COVERS]-(pt:Product_Type {name: $product_type_name})
        """ if product_type_name else "") + """
        MATCH (b)-[rel_b]-(x)
        WHERE b <> a AND {node: x, relType: type(rel_b)} IN neighborsA
        WITH a, b, sb, count(DISTINCT x) AS overlap, collect(DISTINCT {node: x, relType: type(rel_b)}) AS shared_nodes
        WHERE overlap >= $min_matches

        // 3) Weighted score - check relationship types for components
        WITH b, sb, overlap, shared_nodes,
             reduce(score = 0.0, item IN shared_nodes |
                 score +
                 CASE
                     WHEN 'Brand'        IN labels(item.node) THEN $weight_brand
                     WHEN 'Format'       IN labels(item.node) THEN $weight_format
                     WHEN 'Unit_measure' IN labels(item.node) THEN $weight_unit_measure
                     WHEN 'Product_Type' IN labels(item.node) THEN $weight_pt
                     WHEN 'Ingredient' IN labels(item.node) AND item.relType = 'FIRST_LEVEL_INGREDIENT' THEN $weight_component_first
                     WHEN 'Ingredient' IN labels(item.node) AND item.relType = 'SECOND_LEVEL_INGREDIENT' THEN $weight_component_second
                     WHEN 'Ingredient' IN labels(item.node) AND item.relType = 'ALLERGEN' THEN $weight_allergen
                     ELSE 0.01
                 END
             ) AS weighted_score

        // 4) Details
        OPTIONAL MATCH (b)-[:FROM_BRAND]-(brand:Brand)
        OPTIONAL MATCH (b)-[:IS_PACKED]-(fmt:Format)
        OPTIONAL MATCH (b)-[:COVERS]-(pt:Product_Type)

        RETURN b.id AS id,
               b.siid AS siid,
               b.product_name AS product_name,
               b.description AS description,
               b.url AS url,
               b.price AS price,
               sb.name AS store,
               brand.name AS brand,
               fmt.name AS format,
               pt.name AS product_type,
               overlap,
               weighted_score,
               [item IN shared_nodes |
                    CASE
                        WHEN 'Brand'        IN labels(item.node) THEN 'Brand: ' + item.node.name
                        WHEN 'Format'       IN labels(item.node) THEN 'Format: ' + item.node.name
                        WHEN 'Product_type' IN labels(item.node) THEN 'Product_type: ' + item.node.name
                        WHEN 'Unit_measure' IN labels(item.node) THEN 'Unit_measure: ' + item.node.name
                        WHEN 'Ingredient' IN labels(item.node) AND item.relType = 'FIRST_LEVEL_INGREDIENT' THEN 'First_level_ingredient: ' + item.node.name
                        WHEN 'Ingredient' IN labels(item.node) AND item.relType = 'SECOND_LEVEL_INGREDIENT' THEN 'Second_level_ingredient: ' + item.node.name
                        WHEN 'Ingredient' IN labels(item.node) AND item.relType = 'ALLERGEN' THEN 'Allergen: ' + item.node.name
                        ELSE 'Other: ' + coalesce(item.node.name, 'N/A')
                    END
               ] AS shared_nodes_details
        ORDER BY weighted_score DESC, overlap DESC, b.id ASC
        """

        parameters = {
            "product_id": product_a_id,
            "store_b": store_b,
            "min_matches": min_matches,
            "weight_brand":  float(weights.get("Brand", 0.25)),
            "weight_format": float(weights.get("Format", 0.15)),
            "weight_pt":     float(weights.get("Product_type", 0.60)),
            "weight_unit_measure": float(weights.get("Unit_measure", 0.10)),
            "weight_component_first":  float(weights.get("First_level_ingredient", 0.20)),
            "weight_component_second": float(weights.get("Second_level_ingredient", 0.10)),
            "weight_allergen": float(weights.get("Allergen", 0.05)),
        }

        # Add product_type_name to parameters if found
        if product_type_name:
            parameters["product_type_name"] = product_type_name

        # Print query with actual values (for debugging)
        logging.info(f"  📝 Executing query with parameters:")
        logging.info(f"     product_id: {parameters['product_id']}")
        logging.info(f"     store_b: {parameters['store_b']}")
        logging.info(f"     min_matches: {parameters['min_matches']}")
        if 'product_type_name' in parameters:
            logging.info(f"     product_type_name: {parameters['product_type_name']}")
        
        # Build actual query string for logging (replace parameters with actual values)
        actual_query = query_similar.replace("$product_id", str(parameters['product_id']))
        actual_query = actual_query.replace("$store_b", f"'{parameters['store_b']}'")
        actual_query = actual_query.replace("$min_matches", str(parameters['min_matches']))
        actual_query = actual_query.replace("$weight_brand", str(parameters['weight_brand']))
        actual_query = actual_query.replace("$weight_format", str(parameters['weight_format']))
        actual_query = actual_query.replace("$weight_pt", str(parameters['weight_pt']))
        actual_query = actual_query.replace("$weight_unit_measure", str(parameters['weight_unit_measure']))
        actual_query = actual_query.replace("$weight_component_first", str(parameters['weight_component_first']))
        actual_query = actual_query.replace("$weight_component_second", str(parameters['weight_component_second']))
        actual_query = actual_query.replace("$weight_allergen", str(parameters['weight_allergen']))
        if 'product_type_name' in parameters:
            actual_query = actual_query.replace("$product_type_name", f"'{parameters['product_type_name']}'")
        
        # logging.info(f"  📋 Query with actual values:\n{actual_query}")

        similar_products_b = self.execute_query(query_similar, parameters)
        
        # Filter products by graph score difference from the best match
        if similar_products_b:
            max_graph_score = similar_products_b[0].get('weighted_score', 0.0)
            filtered_products = [
                prod for prod in similar_products_b 
                if (max_graph_score - prod.get('weighted_score', 0.0)) < score_threshold
            ]
            similar_products_b = filtered_products
            
            logging.info(f"  ✅ Found {len(similar_products_b)} similar products in {store_b} (max score: {max_graph_score:.3f}, threshold: {score_threshold})")
        else:
            logging.info(f"  ⚠️ No similar products found in {store_b}")

        return (product_a_id, product_a, similar_products_b)

    def find_cross_store_similarities(
        self,
        store_a: str,
        store_b: str,
        min_matches: int = 5,
        weights: dict = None,
        combined_weights: dict = None,
        limit_products_a: int = None,
        distance_metric: str = 'cosine',
        category_filter: str = None,
        max_workers: int = 10,
        max_embedding_workers: int = 100,
        use_external_ids: bool = False,
        external_ids_path: str = None,
        score_threshold: float = 0.15
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        For each product in store A, find similar products in store B
        using shared neighbors and weighted scoring (Brand, Format, Product_type).
        Products are filtered by score threshold from best match.
        
        Uses multi-threading for parallel processing of:
        1. Multiple products A (max_workers threads)
        2. Embedding generation for each product A's similar products B (max_embedding_workers threads)
        
        Args:
            store_a: Name of the origin store
            store_b: Name of the destination store
            min_matches: Minimum number of shared neighbors required
            weights: Dictionary of weights for different node types (Brand, Format, Product_type, etc.)
                Default: {"Brand": 0.3, "Format": 0.15, "Product_type": 0.60, ...}
            combined_weights: Dictionary of weights for combining graph, name, and description scores
                Default: {"graph": 0.4, "name": 0.4, "description": 0.2}
            limit_products_a: Limit number of products from store A to process
            distance_metric: Distance metric for embeddings
                - 'cosine': Cosine similarity (default)
                - 'euclidean': Euclidean distance
                - 'dot_product': Dot product similarity
                - 'manhattan': Manhattan distance
            category_filter: Optional category prefix to filter products (e.g., "Alimentación general/Despensa/Pasta")
            max_workers: Maximum number of parallel workers for processing products A (default: 10)
            max_embedding_workers: Maximum number of parallel workers for embedding generation (default: 100)
            use_external_ids: If True, read product IDs from external file instead of Neo4j (default: False)
            external_ids_path: Path to file containing product IDs (required if use_external_ids=True)
                Format: one product ID per line, or JSON array of IDs
            score_threshold: Maximum graph score difference from best match to include (default: 0.15)
        
        Returns:
            Dictionary mapping product IDs to their similar products
        """
        if weights is None:
            weights = dict(DEFAULT_WEIGHTS)
        
        if combined_weights is None:
            combined_weights = {
                "graph": 0.4,
                "name": 0.4,
                "description": 0.2
            }


        if use_external_ids:
            # Read product IDs from external file
            if not external_ids_path:
                logging.error("❌ use_external_ids=True but no external_ids_path provided")
                return {}
            
            logging.info(f"📂 Reading product IDs from external file: {external_ids_path}")
            
            try:
                with open(external_ids_path, 'r', encoding='utf-8') as f:
                    file_content = f.read().strip()
                    
                    # Try to parse as JSON array first
                    try:
                        product_ids = json.loads(file_content)
                        if not isinstance(product_ids, list):
                            product_ids = [product_ids]
                    except json.JSONDecodeError:
                        # Fall back to reading line by line
                        product_ids = [line.strip() for line in file_content.split('\n') if line.strip()]
                
                # Convert to integers if possible
                try:
                    product_ids = [int(pid) for pid in product_ids]
                except (ValueError, TypeError):
                    pass
                
                logging.info(f"✅ Loaded {len(product_ids)} product IDs from file")
                
                # Apply limit if specified
                if limit_products_a:
                    product_ids = product_ids[:limit_products_a]
                    logging.info(f"📊 Limited to first {limit_products_a} products")
                
                # Query Neo4j to get product details for these IDs
                products_a = []
                not_found_ids = []
                for product_id in product_ids:
                    query = """
                    MATCH (a:Product {siid: 'eroski_01013-' + $product_id})
                    RETURN a.id AS id,
                           a.siid AS siid,
                           a.product_name AS product_name,
                           a.description AS description,
                           a.url AS url
                    """
                    result = self.execute_query(query, {"product_id": product_id})
                    if result:
                        products_a.append(result[0])
                    else:
                        logging.warning(f"⚠️ Product ID {product_id} not found in Neo4j")
                        not_found_ids.append(product_id)
                
                logging.info(f"✅ Retrieved details for {len(products_a)} products from Neo4j")
                if not_found_ids:
                    logging.warning(f"⚠️ {len(not_found_ids)} product IDs were not found in Neo4j")
                
            except FileNotFoundError:
                logging.error(f"❌ File not found: {external_ids_path}")
                return {}
            except Exception as e:
                logging.error(f"❌ Error reading external IDs file: {e}")
                return {}
        else:
            # Query Neo4j for products from store A
            logging.info(f"Getting products from store: {store_a}")
            query_store_a = """
            MATCH (a:Product)<-[r:SELLS]-(s:Store {name: $store_a})
            """
            
            # Add category filter if provided
            if category_filter:
                query_store_a += """
            WHERE r.category STARTS WITH $category_filter
            """
                logging.info(f"Filtering products by category: {category_filter}")
            
            query_store_a += """
            RETURN a.id AS id,
                   a.siid AS siid, 
                   a.product_name AS product_name,
                   a.description AS description,
                   a.url AS url
            """
            if limit_products_a:
                query_store_a += f" LIMIT {int(limit_products_a)}"

            query_params = {"store_a": store_a}
            if category_filter:
                query_params["category_filter"] = category_filter
            
            # Print query with actual values for debugging
            actual_query = query_store_a.replace("$store_a", f"'{store_a}'")
            if category_filter:
                actual_query = actual_query.replace("$category_filter", f"'{category_filter}'")
            logging.info(f"📋 Query for store A products:\n{actual_query}")
            
            products_a = self.execute_query(query_store_a, query_params)
            logging.info(f"Found {len(products_a)} products in {store_a} with category filter: {category_filter or 'None'}")
            not_found_ids = []  # No not-found IDs in normal query mode
        
        if not products_a and not not_found_ids:
            logging.warning(f"No products found in store {store_a}")
            return {}
        
        # ============================================================
        # PARALLEL PROCESSING OF PRODUCTS A
        # ============================================================
        total = len(products_a)
        logging.info(f"\n🚀 Starting parallel processing of {total} products with {max_workers} workers...")
        
        results = {}
        completed_count = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_product = {
                executor.submit(
                    self._process_single_product_a,
                    product_a,
                    store_b,
                    min_matches,
                    weights,
                    idx,
                    total,
                    category_filter,
                    score_threshold
                ): product_a
                for idx, product_a in enumerate(products_a, 1)
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_product):
                try:
                    product_a_id, product_a, similar_products_b = future.result()
                    results[product_a_id] = {
                        'product_a': product_a,
                        'similar_products_b': similar_products_b
                    }
                    completed_count += 1
                    
                    # Progress update every 5 products or at completion
                    if completed_count % 5 == 0 or completed_count == total:
                        pct = (completed_count / total) * 100
                        logging.info(f"📊 Progress: {completed_count}/{total} ({pct:.1f}%) products processed")
                        
                except Exception as e:
                    product_a = future_to_product[future]
                    logging.error(f"❌ Error processing product {product_a.get('id', 'N/A')}: {e}")

        # Add not-found products to results
        if use_external_ids and not_found_ids:
            for not_found_id in not_found_ids:
                results[f"NOT_FOUND_{not_found_id}"] = {
                    'product_a': {
                        'id': not_found_id,
                        'product_name': 'NOT FOUND IN NEO4J',
                        'description': f'Product ID {not_found_id} does not exist in the database',
                        'url': ''
                    },
                    'similar_products_b': [],
                    'not_found': True
                }
        
        logging.info(f"\n✅ Cross-store similarity analysis complete!")
        logging.info(f"   Processed {len(results)} products from {store_a}")
        logging.info(f"   Found matches for {sum(1 for r in results.values() if r['similar_products_b'] and not r.get('not_found', False))} products")
        if use_external_ids and not_found_ids:
            logging.info(f"   ⚠️ {len(not_found_ids)} product IDs were not found in Neo4j")
        
        # ============================================================
        # ADD EMBEDDING SIMILARITY TO RESULTS (PARALLEL)
        # ============================================================
        logging.info(f"\n🤖 Calculating embedding similarity using {distance_metric} distance...")
        logging.info(f"🚀 Using {max_embedding_workers} workers for parallel embedding computation...")
        
        
        def process_embeddings_for_product_a(product_a_id, data, combined_weights):
            """
            Process embeddings for a single product A and its similar products B.
            Generates separate embeddings for names and descriptions.
            This function will be executed in parallel for each product A.
            
            Args:
                product_a_id: ID of product A
                data: Data dictionary containing similar_products_b
                combined_weights: Dictionary with 'graph', 'name', 'description' weights
            """
            similar_b = data['similar_products_b']
            
            if not similar_b:
                return product_a_id, None
            
            # Get product A details to get name and description
            product_a_details = self.get_product_details(product_a_id, print_details=False)
            name_a = product_a_details.get('product_name', '')
            desc_a = product_a_details.get('description', '')
            
            if not name_a and not desc_a:
                logging.warning(f"Product {product_a_id} has no name or description, skipping embedding calculation")
                return product_a_id, None
            
            # Generate separate embeddings for product A name and description
            name_embedding_a = None
            desc_embedding_a = None
            
            if name_a:
                name_embedding_a = self.generate_embeddings([name_a], max_workers=max_embedding_workers)[0]
            if desc_a:
                desc_embedding_a = self.generate_embeddings([desc_a], max_workers=max_embedding_workers)[0]
            
            # Prepare candidate names and descriptions from store B
            names_b = []
            descriptions_b = []
            valid_indices = []
            
            for i, prod_b in enumerate(similar_b):
                name_b = prod_b.get('product_name', '')
                desc_b = prod_b.get('description', '')
                
                if name_b or desc_b:
                    names_b.append(name_b if name_b else '')
                    descriptions_b.append(desc_b if desc_b else '')
                    valid_indices.append(i)
            
            if not valid_indices:
                logging.warning(f"No valid names or descriptions for similar products of {product_a_id}")
                return product_a_id, None
            
            # Generate embeddings for products B (parallel API calls)
            name_embeddings_b = None
            desc_embeddings_b = None
            
            if name_embedding_a is not None and any(names_b):
                # Use product A name as fallback for empty names
                non_empty_names_b = [name if name else name_a for name in names_b]
                name_embeddings_b = self.generate_embeddings(non_empty_names_b, max_workers=max_embedding_workers)
            
            if desc_embedding_a is not None and any(descriptions_b):
                # Use product A description as fallback for empty descriptions
                non_empty_descs_b = [desc if desc else desc_a for desc in descriptions_b]
                desc_embeddings_b = self.generate_embeddings(non_empty_descs_b, max_workers=max_embedding_workers)
            
            # Helper function to calculate similarity
            def calculate_similarity(target_emb, candidate_embs, metric):
                if metric == 'cosine':
                    return cosine_similarity([target_emb], candidate_embs)[0]
                elif metric == 'euclidean':
                    from sklearn.metrics.pairwise import euclidean_distances
                    distances = euclidean_distances([target_emb], candidate_embs)[0]
                    max_dist = distances.max() if distances.max() > 0 else 1
                    return 1 - (distances / max_dist)
                elif metric == 'dot_product':
                    target_norm = target_emb / np.linalg.norm(target_emb)
                    candidates_norm = candidate_embs / np.linalg.norm(candidate_embs, axis=1, keepdims=True)
                    return np.dot(candidates_norm, target_norm)
                elif metric == 'manhattan':
                    from sklearn.metrics.pairwise import manhattan_distances
                    distances = manhattan_distances([target_emb], candidate_embs)[0]
                    max_dist = distances.max() if distances.max() > 0 else 1
                    return 1 - (distances / max_dist)
                else:
                    return cosine_similarity([target_emb], candidate_embs)[0]
            
            # Calculate name and description similarities
            name_similarities = None
            desc_similarities = None
            
            if name_embeddings_b is not None and name_embedding_a is not None:
                name_similarities = calculate_similarity(name_embedding_a, name_embeddings_b, distance_metric)
            
            if desc_embeddings_b is not None and desc_embedding_a is not None:
                desc_similarities = calculate_similarity(desc_embedding_a, desc_embeddings_b, distance_metric)
            
            # Add similarity scores to results
            graph_weight = combined_weights.get('graph', 0.4)
            name_weight = combined_weights.get('name', 0.4)
            desc_weight = combined_weights.get('description', 0.2)
            
            for idx_pos, idx in enumerate(valid_indices):
                graph_score = similar_b[idx].get('weighted_score', 0.0)
                
                # Get individual similarity scores
                name_sim = float(name_similarities[idx_pos]) if name_similarities is not None else 0.0
                desc_sim = float(desc_similarities[idx_pos]) if desc_similarities is not None else 0.0
                
                # Store individual scores
                similar_b[idx]['name_similarity'] = name_sim
                similar_b[idx]['description_similarity'] = desc_sim
                similar_b[idx]['distance_metric'] = distance_metric
                
                # Calculate weighted average of name and description for backward compatibility
                if name_sim > 0.0 and desc_sim > 0.0:
                    # Both available: weighted average (name=0.67, desc=0.33)
                    embedding_sim = (0.67 * name_sim) + (0.33 * desc_sim)
                elif name_sim > 0.0:
                    embedding_sim = name_sim
                elif desc_sim > 0.0:
                    embedding_sim = desc_sim
                else:
                    embedding_sim = 0.0
                
                similar_b[idx]['embedding_similarity'] = embedding_sim
                
                # Combined score: graph=0.4, name=0.4, description=0.2
                combined = (graph_weight * graph_score) + (name_weight * name_sim) + (desc_weight * desc_sim)
                similar_b[idx]['combined_score'] = float(combined)
            
            # Re-sort by combined score
            sorted_similar_b = sorted(similar_b, key=lambda x: x.get('combined_score', 0.0), reverse=True)
            
            return product_a_id, sorted_similar_b
        
        # Process embeddings in parallel for all products A
        total_products_with_matches = sum(1 for data in results.values() if data['similar_products_b'])
        logging.info(f"📊 Processing embeddings for {total_products_with_matches} products with matches...")
        
        embedding_completed = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all embedding tasks
            future_to_product_id = {
                executor.submit(process_embeddings_for_product_a, product_a_id, data, combined_weights): product_a_id
                for product_a_id, data in results.items()
                if data['similar_products_b']  # Only process products with matches
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_product_id):
                try:
                    product_a_id, sorted_similar_b = future.result()
                    
                    if sorted_similar_b is not None:
                        results[product_a_id]['similar_products_b'] = sorted_similar_b
                    
                    embedding_completed += 1
                    
                    # Progress update
                    if embedding_completed % 5 == 0 or embedding_completed == total_products_with_matches:
                        pct = (embedding_completed / total_products_with_matches) * 100
                        logging.info(f"📊 Embedding progress: {embedding_completed}/{total_products_with_matches} ({pct:.1f}%)")
                        
                except Exception as e:
                    product_a_id = future_to_product_id[future]
                    logging.error(f"❌ Error calculating embeddings for product {product_a_id}: {e}")
        
        logging.info(f"✅ Embedding similarity calculated using {distance_metric}!")
        logging.info(f"   Weights applied: Graph={combined_weights['graph']}, Name={combined_weights['name']}, Description={combined_weights['description']}")
        
        # Save embeddings cache to disk (only once at the end)
        logging.info(f"💾 Saving embeddings cache to disk...")
        self.save_embeddings_cache()
        
        # Add configuration metadata to results
        results['_metadata'] = {
            'store_a': store_a,
            'store_b': store_b,
            'min_matches': min_matches,
            'weights': weights,
            'combined_weights': combined_weights,
            'distance_metric': distance_metric,
            'score_threshold': score_threshold,
            'max_workers': max_workers,
            'max_embedding_workers': max_embedding_workers
        }

        return results


# ---------------
# Excel and CSV Export
# ---------------
def export_siid_pairs_csv(
    results: Dict[str, Dict],
    store_a: str,
    store_b: str,
    output_path: str = None,
    metadata: Dict = None
) -> str:
    """
    Export only SIID pairs for products with rank 1, 2, or 3 to CSV.
    
    Args:
        results: Results from find_cross_store_similarities()
        store_a: Name of origin store
        store_b: Name of destination store
        output_path: Path to output CSV file (optional)
        metadata: Optional metadata dict with configuration parameters
        
    Returns:
        str: Path to CSV file created
    """
    import csv
    from datetime import datetime
    
    # Generate filename if not provided
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = f"3_siid_pairs_{store_a}_to_{store_b}_{timestamp}.csv"
    
    # Collect SIID pairs for ranks 1-3
    siid_pairs = []
    
    for product_a_id, data in results.items():
        # Skip metadata
        if product_a_id == '_metadata':
            continue
        
        product_a = data.get('product_a')
        similar_b_list = data.get('similar_products_b', [])
        
        # Skip if no product_a or no matches or product not found
        if not product_a or not similar_b_list or data.get('not_found', False):
            continue
        
        # Get siid for product A
        siid_a = product_a.get('siid', '')
        
        # Add only rank 1, 2, and 3
        for rank, prod_b in enumerate(similar_b_list[:3], 1):  # Only first 3 products
            siid_b = prod_b.get('siid', '')
            
            if siid_a and siid_b:
                # Extract content after "-" in siid
                siid_a_cleaned = siid_a.split('-')[-1] if '-' in siid_a else siid_a
                siid_b_cleaned = siid_b.split('-')[-1] if '-' in siid_b else siid_b
                
                siid_pairs.append({
                    'siid_a': siid_a_cleaned,
                    'siid_b': siid_b_cleaned,
                    'rank': rank
                })
    
    # Write to CSV
    try:
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';')
            
            # Write header
            writer.writerow(['siid_a', 'siid_b', 'rank'])
            
            # Write data
            for pair in siid_pairs:
                writer.writerow([pair['siid_a'], pair['siid_b'], pair['rank']])
        
        logging.info(f"✅ SIID pairs CSV created: {output_path}")
        logging.info(f"   Total pairs exported: {len(siid_pairs)}")
        return output_path
        
    except Exception as e:
        logging.error(f"❌ Error creating SIID pairs CSV: {e}")
        return None


def export_cross_store_results_to_csv(
    results: Dict[str, Dict],
    store_a: str,
    store_b: str,
    output_path: str = None
) -> str:
    """
    Export cross-store analysis results to CSV file with only siid_a, siid_b, and rank.
    
    Args:
        results: Results from find_cross_store_similarities()
        store_a: Name of origin store
        store_b: Name of destination store
        output_path: Path to CSV file (optional)
        
    Returns:
        str: Path to created CSV file
    """
    import pandas as pd
    import csv
    from datetime import datetime
    
    # Generate filename if not provided
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = f"cross_store_similarity_{store_a}_to_{store_b}_{timestamp}.csv"
    
    # Prepare data for CSV
    rows = []
    
    for product_a_id, data in results.items():
        # Skip metadata
        if product_a_id == '_metadata':
            continue
        
        product_a = data.get('product_a', {})
        similar_b_list = data.get('similar_products_b', [])
        is_not_found = data.get('not_found', False)
        
        if not similar_b_list:
            # Add row even if no matches found or product not found
            status = 'NOT FOUND IN DATABASE' if is_not_found else 'No matches'
            rows.append({
                'Product_A_ID': product_a_id,
                'Product_A_SIID': product_a.get('siid', ''),
                'Product_A_Name': product_a.get('product_name', 'N/A'),
                'Product_A_Description': product_a.get('description', ''),
                'Product_A_URL': product_a.get('url', ''),
                'Store_A': store_a,
                'Rank': status,
                'Product_B_ID': '',
                'Product_B_SIID': '',
                'Product_B_Name': '',
                'Product_B_Description': '',
                'Product_B_URL': '',
                'Store_B': store_b,
                'Combined_Score': '',
                'Graph_Score': '',
                'Name_Similarity': '',
                'Description_Similarity': '',
                'Embedding_Score': '',
                'Overlap': '',
                'Brand_B': '',
                'Price_B': '',
                'Category_B': '',
                'Product_Type_B': '',
                'Format_B': '',
                'Shared_Attributes': ''
            })
        else:
            # Add one row per similar product
            for rank, prod_b in enumerate(similar_b_list, 1):
                # Get shared attributes
                shared_attrs = prod_b.get('shared_nodes_details', [])
                shared_attrs_str = ', '.join(shared_attrs) if shared_attrs else ''
                
                rows.append({
                    'Product_A_ID': product_a_id,
                    'Product_A_SIID': product_a.get('siid', ''),
                    'Store_A': store_a,
                    'Product_A_Name': product_a.get('product_name', 'N/A'),
                    'Product_A_Description': product_a.get('description', ''),
                    'Product_A_URL': product_a.get('url', ''),
                    'Rank': rank,
                    'Product_B_ID': prod_b.get('id', ''),
                    'Product_B_SIID': prod_b.get('siid', ''),
                    'Product_B_Name': prod_b.get('product_name', 'N/A'),
                    'Product_B_Description': prod_b.get('description', ''),
                    'Product_B_URL': prod_b.get('url', ''),
                    'Store_B': store_b,
                    'Combined_Score': round(prod_b.get('combined_score', 0.0), 3) if prod_b.get('combined_score') else '',
                    'Graph_Score': round(prod_b.get('weighted_score', 0.0), 3),
                    'Name_Similarity': round(prod_b.get('name_similarity', 0.0), 3) if prod_b.get('name_similarity') else '',
                    'Description_Similarity': round(prod_b.get('description_similarity', 0.0), 3) if prod_b.get('description_similarity') else '',
                    'Embedding_Score': round(prod_b.get('embedding_similarity', 0.0), 3) if prod_b.get('embedding_similarity') else '',
                    'Overlap': prod_b.get('overlap', ''),
                    'Brand_B': prod_b.get('brand', ''),
                    'Price_B': prod_b.get('price', ''),
                    'Category_B': prod_b.get('category', ''),
                    'Product_Type_B': prod_b.get('product_type', ''),
                    'Format_B': prod_b.get('format', ''),
                    'Shared_Attributes': shared_attrs_str
                })
    
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Replace NaN and infinite values with empty strings
    df.replace([np.inf, -np.inf], '', inplace=True)
    df.fillna('', inplace=True)
    
    # Export to CSV with semicolon delimiter
    df.to_csv(output_path, index=False, sep=';', encoding='utf-8')
    
    logging.info(f"✅ CSV file created: {output_path}")
    return output_path


def export_cross_store_results_to_excel(
    results: Dict[str, Dict],
    store_a: str,
    store_b: str,
    output_path: str = None
) -> str:
    """
    Exporta los resultados del análisis cross-store a un archivo Excel con formato.
    
    Args:
        results: Resultados de find_cross_store_similarities()
        store_a: Nombre del supermercado origen
        store_b: Nombre del supermercado destino
        output_path: Ruta del archivo Excel (opcional)
        
    Returns:
        str: Ruta del archivo Excel creado
    """
    import pandas as pd
    from datetime import datetime
    
    # Generate filename if not provided
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = f"cross_store_similarity_{store_a}_to_{store_b}_{timestamp}.xlsx"
    
    # Prepare data for Excel
    rows = []
    
    for product_a_id, data in results.items():
        # Skip metadata
        if product_a_id == '_metadata':
            continue
        
        product_a = data.get('product_a', {})
        similar_b_list = data.get('similar_products_b', [])
        is_not_found = data.get('not_found', False)
        
        if not similar_b_list:
            # Add row even if no matches found or product not found
            status = 'NOT FOUND IN DATABASE' if is_not_found else 'No matches'
            rows.append({
                'Product_A_ID': product_a_id,
                'Product_A_SIID': product_a.get('siid', ''),
                'Product_A_Name': product_a.get('product_name', 'N/A'),
                'Product_A_Description': product_a.get('description', ''),
                'Product_A_URL': product_a.get('url', ''),
                'Store_A': store_a,
                'Rank': status,
                'Product_B_ID': '',
                'Product_B_SIID': '',
                'Product_B_Name': '',
                'Product_B_Description': '',
                'Product_B_URL': '',
                'Store_B': store_b,
                'Combined_Score': '',
                'Graph_Score': '',
                'Embedding_Score': '',
                'Overlap': '',
                'Brand_B': '',
                'Price_B': '',
                'Category_B': '',
                'Product_Type_B': '',
                'Format_B': '',
                'Shared_Attributes': ''
            })
        else:
            # Add one row per similar product
            for rank, prod_b in enumerate(similar_b_list, 1):
                # Get shared attributes
                shared_attrs = prod_b.get('shared_nodes_details', [])
                shared_attrs_str = ', '.join(shared_attrs) if shared_attrs else ''
                
                rows.append({
                    'Product_A_ID': product_a_id,
                    'Product_A_SIID': product_a.get('siid', ''),
                    'Store_A': store_a,
                    'Product_A_Name': product_a.get('product_name', 'N/A'),
                    'Product_A_Description': product_a.get('description', ''),
                    'Product_A_URL': product_a.get('url', ''),
                    'Rank': rank,
                    'Product_B_ID': prod_b.get('id', ''),
                    'Product_B_SIID': prod_b.get('siid', ''),
                    'Product_B_Name': prod_b.get('product_name', 'N/A'),
                    'Product_B_Description': prod_b.get('description', ''),
                    'Product_B_URL': prod_b.get('url', ''),
                    'Store_B': store_b,
                    'Combined_Score': round(prod_b.get('combined_score', 0.0), 3) if prod_b.get('combined_score') else '',
                    'Graph_Score': round(prod_b.get('weighted_score', 0.0), 3),
                    'Name_Similarity': round(prod_b.get('name_similarity', 0.0), 3) if prod_b.get('name_similarity') else '',
                    'Description_Similarity': round(prod_b.get('description_similarity', 0.0), 3) if prod_b.get('description_similarity') else '',
                    'Embedding_Score': round(prod_b.get('embedding_similarity', 0.0), 3) if prod_b.get('embedding_similarity') else '',
                    'Overlap': prod_b.get('overlap', ''),
                    'Brand_B': prod_b.get('brand', ''),
                    'Price_B': prod_b.get('price', ''),
                    'Category_B': prod_b.get('category', ''),
                    'Product_Type_B': prod_b.get('product_type', ''),
                    'Format_B': prod_b.get('format', ''),
                    'Shared_Attributes': shared_attrs_str
                })
    
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Replace NaN and infinite values with empty strings to avoid Excel export errors
    df.replace([np.inf, -np.inf], '', inplace=True)
    df.fillna('', inplace=True)
    
    # Create Excel writer with xlsxwriter engine for formatting
    try:
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Cross-Store Similarity', index=False)
            
            # Get workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets['Cross-Store Similarity']
            
            # Define formats
            header_format = workbook.add_format({
                'bold': True,
                'bg_color': '#4472C4',
                'font_color': 'white',
                'border': 1,
                'align': 'center',
                'valign': 'vcenter'
            })
            
            rank1_format = workbook.add_format({
                'bg_color': '#C6EFCE',
                'border': 1
            })
            
            rank2_format = workbook.add_format({
                'bg_color': '#FFEB9C',
                'border': 1
            })
            
            rank3_format = workbook.add_format({
                'bg_color': '#FFC7CE',
                'border': 1
            })
            
            not_found_format = workbook.add_format({
                'bg_color': '#FF9999',
                'font_color': '#800000',
                'bold': True,
                'border': 1
            })
            
            number_format = workbook.add_format({
                'num_format': '0.000',
                'border': 1
            })
            
            # Set column widths
            worksheet.set_column('A:A', 15)  # Product_A_ID
            worksheet.set_column('B:B', 30)  # Product_A_SIID
            worksheet.set_column('C:C', 40)  # Product_A_Name
            worksheet.set_column('D:D', 60)  # Product_A_Description
            worksheet.set_column('E:E', 5)   # Product_A_URL
            worksheet.set_column('F:F', 15)  # Store_A
            worksheet.set_column('G:G', 8)   # Rank
            worksheet.set_column('H:H', 15)  # Product_B_ID
            worksheet.set_column('I:I', 30)  # Product_B_SIID
            worksheet.set_column('J:J', 40)  # Product_B_Name
            worksheet.set_column('K:K', 60)  # Product_B_Description
            worksheet.set_column('L:L', 5)   # Product_B_URL
            worksheet.set_column('M:M', 20)  # Store_B
            worksheet.set_column('N:N', 15)  # Combined_Score
            worksheet.set_column('O:O', 12)  # Graph_Score
            worksheet.set_column('P:P', 15)  # Name_Similarity
            worksheet.set_column('Q:Q', 18)  # Description_Similarity
            worksheet.set_column('R:R', 15)  # Embedding_Score
            worksheet.set_column('S:S', 10)  # Overlap
            worksheet.set_column('T:T', 20)  # Brand_B
            worksheet.set_column('U:U', 12)  # Price_B
            worksheet.set_column('V:V', 30)  # Category_B
            worksheet.set_column('W:W', 20)  # Product_Type_B
            worksheet.set_column('X:X', 15)  # Format_B
            worksheet.set_column('Y:Y', 60)  # Shared_Attributes
            
            # Apply header format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
            
            # Apply conditional formatting for ranks
            for row_num in range(1, len(df) + 1):
                rank_value = df.iloc[row_num - 1]['Rank']
                if rank_value == 'NOT FOUND IN DATABASE':
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], not_found_format)
                elif rank_value == 1:
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], rank1_format)
                elif rank_value == 2:
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], rank2_format)
                elif rank_value == 3:
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], rank3_format)
            
            # Freeze panes (freeze first row and first 6 columns: ID, SIID, Name, Description, URL, Store)
            worksheet.freeze_panes(2, 6)
            
        logging.info(f"✅ Excel file created: {output_path}")
        return output_path
        
    except Exception as e:
        logging.error(f"Error creating Excel file: {e}")
        # Fallback: save as simple CSV
        csv_path = output_path.replace('.xlsx', '.csv')
        df.to_csv(csv_path, index=False)
        logging.info(f"⚠️ Saved as CSV instead: {csv_path}")
        return csv_path


# ---------------
# Pretty printing
# ---------------
def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(title: str):
    print(f"\n📋 {title}")
    print("-" * 70)


def main():
    """
    Main function to test Neo4j connection and execute sample queries.
    """
    from dotenv import load_dotenv
    load_dotenv()

    print_header("NEO4J CONNECTION & QUERY TEST")
    print_section("Initializing Neo4j Controller")
    
    # ============================================================
    # CONFIGURATION: Choose embedding method
    # ============================================================
    controller = Neo4jController(
        openai_api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT
    )

    # Test connection
    print("🔌 Attempting to connect to Neo4j...")
    if not controller.connect_to_neo4j():
        print("\n❌ Failed to connect to Neo4j database")
        print("\n💡 Troubleshooting steps:")
        print("  1. Verify Neo4j is running (check Docker/Desktop Neo4j)")
        print("  2. Check credentials in .env file")
        print("  3. Verify URI format (neo4j://localhost:7687)")
        print("  4. Check firewall settings")
        return

    print("✅ Successfully connected to Neo4j!")

    # Run connection test
    print_section("Database Information")
    test_results = controller.test_connection()

    if not test_results['success']:
        print(f"❌ Connection test failed: {test_results['error']}")
        controller.close_connection()
        return

    print(f"  Database Name: {test_results['database']}")
    print(f"  Neo4j Version: {test_results['neo4j_version']}")
    print(f"  Total Nodes: {test_results['node_count']:,}")
    print(f"  Total Relationships: {test_results['relationship_count']:,}")

    # ============================================================
    # CROSS-STORE SIMILARITY ANALYSIS (DEMO)
    # ============================================================
    print_section("Cross-Store Similarity Analysis")

    store_a = "eroski"  # Origin store (as per your dataset naming)
    store_b = "makro"   # Destination store
    
    # ============================================================
    # CHOOSE DISTANCE METRIC
    # ============================================================
    # Available options: 'cosine', 'euclidean', 'dot_product', 'manhattan'
    distance_metric = 'cosine'  # Change this to test different metrics
    
    print(f"  🔍 Finding similar products:")
    print(f"     From: {store_a} → To: {store_b}")
    print(f"     Distance metric: {distance_metric.upper()}")

    # Find cross-store similarities
    results = controller.find_cross_store_similarities(
        store_a=store_a,
        store_b=store_b,
        min_matches=4,
        weights={
            "Brand": 0.20,
            "Format": 0.10,
            "Product_type": 0.70,
            "Unit_measure": 0.10,
            "First_level_ingredient": 0.20,
            "Second_level_ingredient": 0.10,
            "Allergen": 0.05
        },
        combined_weights={
            "graph": 0.60,
            "name": 0.30,
            "description": 0.10
        },
        limit_products_a=1000,
        distance_metric=distance_metric,
        category_filter=None,
        max_workers=20,
        max_embedding_workers=100,
        use_external_ids=True,
        external_ids_path="2_eroski_id.txt",
        score_threshold=0.10
    )

    # Display results
    print_section("Cross-Store Similarity Results")

    for idx, (product_a_id, data) in enumerate(results.items(), 1):
        product_a = data['product_a']
        similar_b = data['similar_products_b']

        print(f"\n{'='*70}")
        print(f"[{idx}] PRODUCT FROM {store_a}:")
        print(f"    ID: {product_a_id}")
        controller.get_product_details(product_a_id, print_details=True)
        print(f"    Name: {product_a.get('product_name', 'N/A')}")
        print(f"\n    FOUND {len(similar_b)} SIMILAR PRODUCTS IN {store_b}:")

        if similar_b:
            for i, prod_b in enumerate(similar_b, 1):
                # Get all scores
                graph_score = prod_b.get('weighted_score', 0.0)
                name_sim = prod_b.get('name_similarity', None)
                desc_sim = prod_b.get('description_similarity', None)
                combined_score = prod_b.get('combined_score', None)
                metric_used = prod_b.get('distance_metric', 'N/A')
                
                # Display header with scores
                if combined_score is not None and name_sim is not None and desc_sim is not None:
                    print(f"\n    #{i} | Combined: {combined_score:.3f} | Graph: {graph_score:.3f} | Name: {name_sim:.3f} | Desc: {desc_sim:.3f} ({metric_used})")
                elif combined_score is not None:
                    print(f"\n    #{i} | Combined: {combined_score:.3f} | Graph: {graph_score:.3f} ({metric_used})")
                else:
                    print(f"\n    #{i} | Graph Score: {graph_score:.3f} | Overlap: {prod_b['overlap']} neighbors")
                
                product_name = prod_b.get('product_name', 'N/A') or 'N/A'
                print(f"        Product Name: {product_name[:60]}")
                print(f"        ID: {prod_b.get('id', 'N/A')} | Store: {prod_b.get('store', 'N/A')}")
                print(f"        Brand: {prod_b.get('brand', 'N/A')} | Price: {prod_b.get('price', 'N/A')}")
                print(f"        Product Type: {prod_b.get('product_type', 'N/A')} | Format: {prod_b.get('format', 'N/A')}")
                # Show shared attributes
                shared_nodes = prod_b.get('shared_nodes_details', [])
                if shared_nodes:
                    print(f"        Shared attributes: {', '.join(shared_nodes[:3])}")
        else:
            print(f"    ❌ No similar products found in {store_b}")

    print(f"\n{'='*70}")
    print(f"\n✅ Analysis complete!")
    print(f"   Total products from {store_a}: {len(results)}")
    print(f"   Products with matches: {sum(1 for r in results.values() if r['similar_products_b'])}")
    print(f"{'='*70}\n")

    # ============================================================
    # EXPORT TO EXCEL AND CSV
    # ============================================================
    print_section("Exporting Results")
    
    # Export full Excel file
    excel_file = export_cross_store_results_to_excel(
        results=results,
        store_a=store_a,
        store_b=store_b
    )
    
    print(f"\n✅ Results exported to Excel: {excel_file}")
    
    # Export SIID pairs CSV (ranks 1-3 only)
    metadata = results.get('_metadata', {})
    csv_file = export_siid_pairs_csv(
        results=results,
        store_a=store_a,
        store_b=store_b,
        metadata=metadata
    )
    
    if csv_file:
        print(f"\n✅ SIID pairs CSV created: {csv_file}")
        print(f"   Contains only SIID pairs with ranks 1, 2, and 3")
        if metadata:
            print(f"   Score threshold: {metadata.get('score_threshold', 'N/A')}")
    print()
    
    # Close connection
    controller.close_connection()
    print("🔌 Neo4j connection closed\n")


if __name__ == "__main__":
    main()