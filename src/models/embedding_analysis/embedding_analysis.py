from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import logging
import os
import pickle
import threading
import time
from typing import Any, Dict, List

import numpy as np

from sklearn.metrics.pairwise import manhattan_distances, cosine_similarity, euclidean_distances

from config.settings import PROJECT_ROOT
from src.connectors.neo4j_connector import Neo4jConnector
from src.models.embedding_analysis.embedding_generation import generate_single_embedding


class SimilarityAnalysis:


    def __init__(self):
        self.neo4j_connector = Neo4jConnector()
        self.driver = self.neo4j_connector.get_neo4j_driver()
        # Set driver on connector for execute_query to work
        self.neo4j_connector.driver = self.driver

        # Initialize embeddings cache
        self.embeddings_cache_path = PROJECT_ROOT / "data" / "processed" / "embeddings_cache.pkl"
        self.embeddings_cache = {}
        self.cache_lock = threading.Lock()  # Thread-safe lock for cache access
        self.load_embeddings_cache()

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
                        logging.warning("⚠️ Cache file is empty, starting fresh")
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
                    logging.warning("   Starting with empty cache")
                    self.embeddings_cache = {}
                    return
                    
            except Exception as e:
                logging.error(f"❌ Error loading embeddings cache: {e}")
                if attempt < max_retries - 1:
                    logging.info(f"   Retrying... (attempt {attempt + 2}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.warning("   Starting with empty cache")
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
                print("\n🔄 Generating embeddings: [", end="", flush=True)
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
            
            elapsed_time = time.time() - start_time
            
            # Count successful embeddings (excluding None values)
            successful_embeddings = sum(1 for emb in new_embeddings if emb is not None)
            logging.info(f"✅ Generated {successful_embeddings} OpenAI embeddings in {elapsed_time:.2f}s ({successful_embeddings/elapsed_time:.1f} embeddings/s)")
            
            # Store new embeddings in cache and result list (thread-safe)
            # Only store non-None embeddings
            with self.cache_lock:
                for i, text, embedding in zip(indices_to_generate, texts_to_generate, new_embeddings):
                    if embedding is not None:
                        cache_key = self._text_to_cache_key(text)
                        self.embeddings_cache[cache_key] = embedding
                        embeddings[i] = embedding
            
            # Note: Cache will be saved at the end of the cross-store analysis
            # to avoid frequent disk writes
        
        # Convert to numpy array
        embeddings_array = np.array(embeddings)
        return embeddings_array


    # Helper function to calculate similarity based on metric
    def calculate_similarity(self, target_emb, candidate_embs, metric):
        if metric == 'cosine':
            return cosine_similarity([target_emb], candidate_embs)[0]
        elif metric == 'euclidean':
            distances = euclidean_distances([target_emb], candidate_embs)[0]
            max_dist = distances.max() if distances.max() > 0 else 1
            return 1 - (distances / max_dist)
        elif metric == 'dot_product':
            target_norm = target_emb / np.linalg.norm(target_emb)
            candidates_norm = candidate_embs / np.linalg.norm(candidate_embs, axis=1, keepdims=True)
            return np.dot(candidates_norm, target_norm)
        elif metric == 'manhattan':
            distances = manhattan_distances([target_emb], candidate_embs)[0]
            max_dist = distances.max() if distances.max() > 0 else 1
            return 1 - (distances / max_dist)
        else:
            return cosine_similarity([target_emb], candidate_embs)[0]     
        

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
        results = self.neo4j_connector.execute_query(query, {"product_id": product_id})
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

    def preload_embeddings_for_results(
        self,
        results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, list]:
        """
        Preload ALL embeddings needed for the entire result set in a single query.
        This is the fastest approach - one query for everything.
        
        Args:
            results: Complete results dictionary with all products A and their similar products B
            
        Returns:
            Dictionary mapping siid -> {'name': embedding, 'description': embedding}
        """
        # Collect all unique (siid, store) pairs from results
        siid_store_pairs = set()
        
        for product_a_id, data in results.items():
            if product_a_id == '_metadata':
                continue
            
            # Add product A
            product_a = data.get('product_a', {})
            siid_a = product_a.get('siid')
            store_a = product_a.get('store_name')
            if siid_a and store_a:
                siid_store_pairs.add((siid_a, store_a))
            
            # Add all products B
            for prod_b in data.get('similar_products_b', []):
                siid_b = prod_b.get('siid')
                store_b = prod_b.get('store')
                if siid_b and store_b:
                    siid_store_pairs.add((siid_b, store_b))
        
        # Convert set to list
        siid_store_list = list(siid_store_pairs)
        
        logging.info(f"📥 Preloading {len(siid_store_list)} unique embeddings from Neo4j...")
        
        # Use the existing batch method
        embedding_map = self._fetch_embeddings_batch(siid_store_list)
        
        logging.info(f"✅ Preloaded {len(embedding_map)} embeddings successfully")
        
        return embedding_map
    
    def _fetch_embeddings_batch(self, siid_store_pairs: List[tuple]) -> Dict[str, list]:
        """
        Fetch multiple product embeddings from Neo4j in a single batch query.
        Much faster than individual queries.
        
        Args:
            siid_store_pairs: List of (siid, store_name) tuples
            
        Returns:
            Dictionary mapping siid to embedding vector
        """
        if not siid_store_pairs:
            return {}
        
        # Extract unique siids and stores
        siids = [pair[0] for pair in siid_store_pairs if pair[0]]
        stores = [pair[1] for pair in siid_store_pairs if pair[1]]
        
        if not siids:
            return {}
        
        # Batch query to get all embeddings at once (name and description separate)
        query = """
        UNWIND $siid_store_pairs AS pair
        MATCH (s:Store {name: pair.store})-[:SELLS]->(p:Product {siid: pair.siid})
        RETURN p.siid AS siid, 
               p.product_name_embedding_openai AS name_embedding,
               p.description_embedding_openai AS description_embedding
        """
        
        # Format parameters
        params = {
            "siid_store_pairs": [
                {"siid": siid, "store": store}
                for siid, store in siid_store_pairs
                if siid and store
            ]
        }
        
        try:
            results = self.neo4j_connector.execute_query(query, params)
            # Create dictionary mapping siid -> {name_embedding, description_embedding}
            embedding_map = {}
            for result in results:
                siid = result.get('siid')
                name_emb = result.get('name_embedding')
                desc_emb = result.get('description_embedding')
                if siid:
                    embedding_map[siid] = {
                        'name': name_emb,
                        'description': desc_emb
                    }
            return embedding_map
        except Exception as e:
            logging.error(f"Error fetching embeddings batch: {e}")
            return {}
    
    def _fetch_embedding_from_neo4j(self, siid: str, store_name: str = None) -> list:
        """
        Fetch product embedding from Neo4j using SIID and store name.
        
        Args:
            siid: Product SIID identifier
            store_name: Store name to filter products (recommended to avoid ambiguity)
            
        Returns:
            Embedding vector as list, or None if not found
        """
        if not siid:
            return None
        
        # Query to get embedding from Neo4j
        # If store_name is provided, filter by store to avoid ambiguity
        if store_name:
            query = """
            MATCH (s:Store {name: $store_name})-[:SELLS]->(p:Product {siid: $siid})
            RETURN p.product_embedding_openai AS embedding
            LIMIT 1
            """
            parameters = {"siid": siid, "store_name": store_name}
        else:
            query = """
            MATCH (p:Product {siid: $siid})
            RETURN p.product_embedding_openai AS embedding
            LIMIT 1
            """
            parameters = {"siid": siid}
        
        try:
            results = self.neo4j_connector.execute_query(query, parameters)
            if results and len(results) > 0:
                embedding = results[0].get('embedding')
                return embedding
            return None
        except Exception as e:
            logging.error(f"Error fetching embedding for SIID {siid} (store: {store_name}): {e}")
            return None
    
    def process_embeddings_for_product(
        self,
        product_a_id: str,
        product_a: Dict[str, Any],
        similar_products_b: List[Dict[str, Any]],
        distance_metric: str = 'cosine',
        embedding_cache: Dict[str, list] = None,
        min_name_similarity: float = 0.6,
        min_description_similarity: float = 0.6
    ) -> List[Dict[str, Any]]:
        """
        Process embeddings for a single product A and its similar products B.
        Uses pre-loaded embedding_cache if provided, otherwise fetches from Neo4j.
        
        This function is designed to be executed in parallel for each product A.
        
        Args:
            product_a_id: ID of product A
            product_a: Product A data dictionary
            similar_products_b: List of similar products from store B
            combined_weights: Dictionary with 'graph', 'name', 'description' weights
            distance_metric: Distance metric to use for similarity calculation
            max_workers: Maximum number of parallel workers (unused, kept for compatibility)
            embedding_cache: Pre-loaded dictionary mapping siid -> embedding (for performance)
            
        Returns:
            List of similar products B with embedding similarity scores
        """
        if not similar_products_b:
            return []
        
        # Use cache if provided, otherwise fetch from Neo4j
        if embedding_cache is not None:
            # Fast path: use pre-loaded cache
            embeddings_a = embedding_cache.get(product_a.get('siid'), {})
            name_embedding_a = embeddings_a.get('name')
            desc_embedding_a = embeddings_a.get('description')
        else:
            # Slow path: fetch batch from Neo4j
            siid_store_pairs = [(product_a.get('siid'), product_a.get('store_name'))]
            for prod_b in similar_products_b:
                siid_store_pairs.append((prod_b.get('siid'), prod_b.get('store')))
            
            embedding_map = self._fetch_embeddings_batch(siid_store_pairs)
            embeddings_a = embedding_map.get(product_a.get('siid'), {})
            name_embedding_a = embeddings_a.get('name')
            desc_embedding_a = embeddings_a.get('description')
        
        if name_embedding_a is None and desc_embedding_a is None:
            logging.warning(f"Product {product_a_id} has no embeddings in Neo4j, skipping embedding calculation")
            return similar_products_b
        
        # Convert to numpy arrays and validate dimensions
        name_embedding_a = np.array(name_embedding_a) if name_embedding_a else None
        desc_embedding_a = np.array(desc_embedding_a) if desc_embedding_a else None
        
        # Detect expected embedding dimensions (use 3072 as default for text-embedding-3-large)
        # If product A has embeddings, use those dimensions as reference
        # Otherwise, accept any valid dimension from products B
        expected_name_dim = len(name_embedding_a) if name_embedding_a is not None else 3072
        expected_desc_dim = len(desc_embedding_a) if desc_embedding_a is not None else 3072
        
        # Collect embeddings from products B
        name_embeddings_b = []
        desc_embeddings_b = []
        valid_indices = []
        
        if embedding_cache is not None:
            # Fast path: use pre-loaded cache
            for i, prod_b in enumerate(similar_products_b):
                embeddings_b = embedding_cache.get(prod_b.get('siid'), {})
                name_emb_b = embeddings_b.get('name')
                desc_emb_b = embeddings_b.get('description')
                
                # Validate embedding dimensions to avoid inhomogeneous array errors
                name_valid = (name_emb_b is None or 
                             (expected_name_dim is not None and len(name_emb_b) == expected_name_dim))
                desc_valid = (desc_emb_b is None or 
                             (expected_desc_dim is not None and len(desc_emb_b) == expected_desc_dim))
                
                if not name_valid or not desc_valid:
                    logging.warning(f"⚠️ Product {prod_b.get('siid')} has invalid embedding dimensions: "
                                   f"name={len(name_emb_b) if name_emb_b else None} (expected {expected_name_dim}), "
                                   f"desc={len(desc_emb_b) if desc_emb_b else None} (expected {expected_desc_dim}). Skipping.")
                    continue
                
                if name_emb_b or desc_emb_b:
                    name_embeddings_b.append(name_emb_b if name_emb_b else [0] * expected_name_dim if expected_name_dim else [])
                    desc_embeddings_b.append(desc_emb_b if desc_emb_b else [0] * expected_desc_dim if expected_desc_dim else [])
                    valid_indices.append(i)
        else:
            # Slow path: use fetched batch
            for i, prod_b in enumerate(similar_products_b):
                embeddings_b = embedding_map.get(prod_b.get('siid'), {})
                name_emb_b = embeddings_b.get('name')
                desc_emb_b = embeddings_b.get('description')
                
                # Validate embedding dimensions to avoid inhomogeneous array errors
                name_valid = (name_emb_b is None or 
                             (expected_name_dim is not None and len(name_emb_b) == expected_name_dim))
                desc_valid = (desc_emb_b is None or 
                             (expected_desc_dim is not None and len(desc_emb_b) == expected_desc_dim))
                
                if not name_valid or not desc_valid:
                    logging.warning(f"⚠️ Product {prod_b.get('siid')} has invalid embedding dimensions: "
                                   f"name={len(name_emb_b) if name_emb_b else None} (expected {expected_name_dim}), "
                                   f"desc={len(desc_emb_b) if desc_emb_b else None} (expected {expected_desc_dim}). Skipping.")
                    continue
                
                if name_emb_b or desc_emb_b:
                    name_embeddings_b.append(name_emb_b if name_emb_b else [0] * expected_name_dim if expected_name_dim else [])
                    desc_embeddings_b.append(desc_emb_b if desc_emb_b else [0] * expected_desc_dim if expected_desc_dim else [])
                    valid_indices.append(i)
        
        if not valid_indices:
            logging.warning(f"No valid embeddings for similar products of {product_a_id}")
            return similar_products_b
        
        # Convert to numpy arrays
        name_embeddings_b = np.array(name_embeddings_b)
        desc_embeddings_b = np.array(desc_embeddings_b)
        
        # Calculate similarities separately for name and description
        name_similarities = None
        desc_similarities = None
        
        if name_embedding_a is not None and len(name_embeddings_b) > 0:
            name_similarities = self.calculate_similarity(name_embedding_a, name_embeddings_b, distance_metric)
        
        if desc_embedding_a is not None and len(desc_embeddings_b) > 0:
            desc_similarities = self.calculate_similarity(desc_embedding_a, desc_embeddings_b, distance_metric)
        
        # Add similarity scores to results
        for idx_pos, idx in enumerate(valid_indices):
            name_sim = float(name_similarities[idx_pos]) if name_similarities is not None else 0.0
            desc_sim = float(desc_similarities[idx_pos]) if desc_similarities is not None else 0.0
            
            # Store individual similarities
            similar_products_b[idx]['name_similarity'] = name_sim
            similar_products_b[idx]['description_similarity'] = desc_sim
            similar_products_b[idx]['distance_metric'] = distance_metric
            
            # Calculate combined embedding similarity for backward compatibility
            if name_sim > 0.0 and desc_sim > 0.0:
                embedding_sim = (name_sim + desc_sim) / 2.0  # Average
            elif name_sim > 0.0:
                embedding_sim = name_sim
            elif desc_sim > 0.0:
                embedding_sim = desc_sim
            else:
                embedding_sim = 0.0
            
            similar_products_b[idx]['embedding_similarity'] = embedding_sim
        
        # Filter products: keep only those with name_similarity >= threshold AND description_similarity >= threshold
        similar_products_b = [
            prod for prod in similar_products_b
            if prod.get('name_similarity', 0.0) >= min_name_similarity
            and prod.get('description_similarity', 0.0) >= min_description_similarity
        ]
        
        # Return without sorting (sorting will happen after euclidean distances are added)
        return similar_products_b
        

        