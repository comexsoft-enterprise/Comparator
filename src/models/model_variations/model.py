import logging
import json
import gc
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
import psycopg2
from psycopg2.extras import RealDictCursor
from src.models.category_analysis.category_analysis import CategoryAnalysis
from src.models.embedding_analysis.embedding_analysis import SimilarityAnalysis
from src.models.postgresql_analysis.euclidean_distance_calculator import process_product_euclidean
# Alternative: Use PostgreSQL-native directly for all batches
# from src.models.postgresql_analysis.euclidean_helpers import process_product_euclidean_native as process_product_euclidean
from src.models.model_variations.insert_match_posgres import insert_to_validate
from config.settings import POSTGRES_DB_CONFIG


class ModelCategoricalAnalysis(CategoryAnalysis):

    def __init__(self):
        self.category_analysis = CategoryAnalysis()
        self.DEFAULT_WEIGHTS = {
            "Brand": 0.3,
            "Format": 0.15,
            "Unit_measure": 0.10,
            "First_level_ingredient": 0.20,
            "Second_level_ingredient": 0.10,
            "Allergen": 0.05,
            "Internal_Subcategory": 0.5,
            "Country_of_Origin": 0.05,
            "Other_Ingredient": 0.05
        }

    def category_based_cross_store_similarity(
            self,
            products_a: List[Dict[str, Any]],
            store_a: str,
            store_b: str,
            min_matches: int,
            weights: Dict[str, float],
            category_filter: str = None,
            max_workers: int = 10,
            use_external_ids: bool = False,
            not_found_ids: List[str] = None,
            cat_score_threshold: float = 0.15,
            batch_size: int = 10
        ) -> Dict[str, List[Dict[str, Any]]]:
            """
            Perform cross-store similarity analysis using category-based filtering.
            
            Args:
                products_a: List of products from store A
                store_a: Name of the origin store
                store_b: Name of the destination store
                min_matches: Minimum number of shared neighbors required
                weights: Dictionary of weights for different node types (Brand, Format, Product_type, etc.)
                category_filter: Optional category prefix to filter products
                max_workers: Maximum number of parallel workers for processing products A
                use_external_ids: If True, read product IDs from external file instead of Neo4j
                not_found_ids: List of product IDs not found in Neo4j (if use_external_ids=True)
                cat_score_threshold: Maximum graph score difference from best match to include
                batch_size: Number of products to process in each batch (default: 10)
            """

            total = len(products_a)
            # OPTIMIZATION: Reduce max_workers aggressively to prevent memory overload
            effective_max_workers = min(max_workers, 5)  # Cap at 5 concurrent queries
            logging.info(f"\n🚀 Starting BATCHED processing of {total} products")
            logging.info(f"   Batch size: {batch_size} | Max workers: {effective_max_workers}")
            
            results = {}
            completed_count = 0
            
            # OPTIMIZATION: Process in batches to reduce memory pressure
            for batch_start in range(0, total, batch_size):
                batch_end = min(batch_start + batch_size, total)
                batch = products_a[batch_start:batch_end]
                batch_num = (batch_start // batch_size) + 1
                total_batches = (total + batch_size - 1) // batch_size
                
                logging.info(f"\n📦 Processing batch {batch_num}/{total_batches} ({len(batch)} products)")
                
                with ThreadPoolExecutor(max_workers=effective_max_workers) as executor:
                    # Submit tasks for current batch
                    future_to_product = {
                        executor.submit(
                            self.category_analysis.find_matches_by_category,
                            product_a,
                            store_a,
                            store_b,
                            min_matches,
                            weights,
                            batch_start + idx,
                            total,
                            cat_score_threshold
                        ): product_a
                        for idx, product_a in enumerate(batch, 1)
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
                
                # Wait between batches to allow Neo4j to release memory
                if batch_end < total:
                    import time
                    time.sleep(2)  # 2 second delay between batches

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
            
            logging.info("\n✅ Cross-store similarity analysis complete!")
            logging.info(f"   Processed {len(results)} products from {store_a}")
            logging.info(f"   Found matches for {sum(1 for r in results.values() if r['similar_products_b'] and not r.get('not_found', False))} products")
            if use_external_ids and not_found_ids:
                logging.info(f"   ⚠️ {len(not_found_ids)} product IDs were not found in Neo4j")


class ModelSemanticSimilarity(SimilarityAnalysis):

    def __init__(self):
        super().__init__()

    def embedding_based_cross_store_similarity(
        self,
        results: Dict[str, Dict[str, Any]],
        combined_weights: Dict[str, float],
        distance_metric: str,
        max_workers: int = 10,
        max_embedding_workers: int = 100
    ) -> Dict[str, Dict[str, Any]]:
        """
        Process embeddings for all products A and their similar products B.
        
        Args:
            results: Dictionary with product_a_id -> {product_a, similar_products_b}
            combined_weights: Weights for graph, name, and description scores
            distance_metric: Distance metric to use (cosine, euclidean, etc.)
            max_workers: Maximum parallel workers for processing products A
            max_embedding_workers: Maximum parallel workers for embedding generation
            
        Returns:
            Updated results dictionary with embedding scores
        """
        # Process embeddings in parallel for all products A
        total_products_with_matches = sum(1 for data in results.values() if data.get('similar_products_b'))
        logging.info(f"📊 Processing embeddings for {total_products_with_matches} products with matches...")
        
        embedding_completed = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all embedding tasks
            future_to_product_id = {
                executor.submit(
                    self.process_embeddings_for_product,
                    product_a_id,
                    data['product_a'],
                    data['similar_products_b'],
                    combined_weights,
                    distance_metric,
                    max_embedding_workers
                ): product_a_id
                for product_a_id, data in results.items()
                if data.get('similar_products_b')  # Only process products with matches
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_product_id):
                try:
                    sorted_similar_b = future.result()
                    product_a_id = future_to_product_id[future]
                    
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

        return results
    
     
     

class ModelCombinedSimilarity:
    """
    Combines category-based (graph) analysis with embedding-based similarity.
    
    This class is the main orchestrator that:
    1. Uses CategoryAnalysis for graph-based matching
    2. Uses SimilarityAnalysis for embedding-based similarity
    3. Combines both scores with configurable weights
    4. Returns sorted results by combined score
    """

    def __init__(self):
        self.category_analysis = CategoryAnalysis()
        self.similarity_analysis = SimilarityAnalysis()
        # falta la clase de los valores numéricos

    def _run_embedding_hf(
        self,
        products_a: List[Dict[str, Any]],
        store_a: str,
        store_b: str,
        top_k: int = 140,
        name_weight: float = 0.5,
        description_weight: float = 0.5,
        batch_size: int = 2048,
        max_workers: int = 20,
        widen_factor: int = 3,
        index_cache_dir: Optional[str] = None,
        rebuild_index: bool = False,
        faiss_threads: int = 10,
        approximate: bool = True,
        hnsw_m: int = 32,
        hnsw_ef_construction: int = 200,
        hnsw_ef_search: int = 64,
        store_b_fetch_workers: int = 10,
        store_b_fetch_batch_size: int = 1000,
        store_a_fetch_workers: int = 10,
        store_a_fetch_batch_size: int = 1000,
    ) -> Dict[str, List[Dict[str, float]]]:
        """Compute embedding similarity using FAISS (cosine via normalized dot product).

        Uses embeddings stored in Neo4j on Product nodes:
        - product_name_embedding_openai
        - description_embedding_openai

        Performance notes:
        - Builds two FAISS indexes for store B (name + description).
        - Uses batched FAISS search for all products A.
        - Uses a MapReduce-style fetch from Neo4j: parallel batch reads (map) + single-thread FAISS add (reduce).
        - Optional on-disk FAISS cache via `index_cache_dir` to avoid rebuilding indexes on repeated runs.
        - If `approximate=True`, uses an approximate HNSW index (when supported by your FAISS build).

        Returns:
            Dict mapping product_a_id -> list of top-k results (most similar first).

            Each entry includes ONLY the separated scores:
            - product_name_embedding_similarity
            - description_embedding_similarity

            Note: ranking/top_k selection is done by the mean of the two similarities.
        """

        # ------------------------------
        # 0) Basic guards + local imports
        # ------------------------------
        top_k = int(top_k)
        if top_k <= 0:
            return {p.get("id"): [] for p in products_a if p.get("id")}
        import numpy as np
        import os
        import pickle
        import hashlib
        import time

        try:
            import faiss
        except Exception as e:
            raise RuntimeError(
                "faiss is required for _run_embedding_hf. Install with `pip install faiss-cpu` (or faiss-gpu)."
            ) from e

        # Embedding property names in Neo4j
        name_prop = "product_name_embedding_openai"
        desc_prop = "description_embedding_openai"

        # Note: keep typing simple inside this function (Pylance disallows local aliases in annotations).

        def _normalize(vec: np.ndarray) -> Optional[np.ndarray]:
            """Normalize vector to unit length (so dot-product equals cosine similarity)."""
            if vec.size == 0:
                return None
            norm = np.linalg.norm(vec)
            if norm == 0:
                return None
            return (vec / norm).astype(np.float32, copy=False)

        # Kept for API compatibility; ranking is currently based on the mean of both similarities.
        # (Weights are not used for ranking to match the pipeline requirement.)
        _ = float(name_weight)
        _ = float(description_weight)
        _ = int(max_workers)

        driver = self.category_analysis.driver

        def _chunks(items: List[str], size: int) -> List[List[str]]:
            """Split a list into fixed-size chunks."""
            if not items:
                return []
            size = max(1, int(size))
            return [items[i : i + size] for i in range(0, len(items), size)]

        def _fetch_embeddings_for_ids(
            *,
            store_name: str,
            ids: List[str],
            fetch_batch_size: int,
            fetch_workers: int,
            store_param_name: str,
        ) -> Dict[str, Tuple[Optional[np.ndarray], Optional[np.ndarray]]]:
            """MapReduce fetch: map = parallel batch query, reduce = merge into a dict.

            Each worker uses its own Neo4j session.
            """
            out: Dict[str, Tuple[Optional[np.ndarray], Optional[np.ndarray]]] = {}
            if not ids:
                return out

            query = f"""
            MATCH (s:Store {{name: ${store_param_name}}})-[r:SELLS]->(p:Product)
            WHERE r.id IN $batch_ids
            RETURN r.id AS id,
                   p.`{name_prop}` AS name_emb,
                   p.`{desc_prop}` AS desc_emb
            """

            def _map_one(batch_ids: List[str]) -> Dict[str, Tuple[Optional[np.ndarray], Optional[np.ndarray]]]:
                local: Dict[str, Tuple[Optional[np.ndarray], Optional[np.ndarray]]] = {}
                with driver.session() as session:
                    result = session.run(query, {store_param_name: store_name, "batch_ids": batch_ids})
                    for record in result:
                        pid = record.get("id")
                        if not pid:
                            continue
                        name_emb = record.get("name_emb")
                        desc_emb = record.get("desc_emb")
                        name_vec = (
                            _normalize(np.asarray(name_emb, dtype=np.float32))
                            if isinstance(name_emb, list) and name_emb
                            else None
                        )
                        desc_vec = (
                            _normalize(np.asarray(desc_emb, dtype=np.float32))
                            if isinstance(desc_emb, list) and desc_emb
                            else None
                        )
                        local[pid] = (name_vec, desc_vec)
                return local

            batches = _chunks(ids, int(fetch_batch_size))
            workers = max(1, int(fetch_workers))
            if workers == 1 or len(batches) <= 1:
                for b in batches:
                    out.update(_map_one(b))
                return out

            with ThreadPoolExecutor(max_workers=min(workers, len(batches))) as ex:
                futures = [ex.submit(_map_one, b) for b in batches]
                for f in as_completed(futures):
                    out.update(f.result())
            return out

        # --------------------------------------------
        # 1) Build or load FAISS indexes for store B
        # --------------------------------------------
        t_build0 = time.perf_counter()

        # We do batched FAISS searches (no per-product ThreadPool), so allow a few FAISS threads.
        try:
            faiss.omp_set_num_threads(max(1, int(faiss_threads)))
        except Exception:
            pass

        def _new_faiss_index(d: int):
            """Create the FAISS index used for store B.

            Prefer approximate HNSW (inner product) when available; fall back to exact flat IP.
            """
            if approximate:
                try:
                    idx = faiss.IndexHNSWFlat(int(d), int(hnsw_m), faiss.METRIC_INNER_PRODUCT)
                    try:
                        idx.hnsw.efConstruction = int(hnsw_ef_construction)
                    except Exception:
                        pass
                    return idx
                except Exception:
                    # Some faiss builds don't support the 3-arg ctor or inner-product HNSW.
                    logging.warning("⚠️ HNSW (inner product) not available; falling back to IndexFlatIP")
            return faiss.IndexFlatIP(int(d))

        # We keep a stable global ID space across the two indexes.
        # The FAISS internal row index differs between name/desc indexes, so we store a mapping
        # from FAISS row -> global_id (and then global_id -> product_id).
        store_b_name_global: List[int] = []  # per-row global_id for name_index
        store_b_desc_global: List[int] = []  # per-row global_id for desc_index
        global_to_product_id: List[str] = []  # global_id -> product_b_id
        global_id_by_product_id: Dict[str, int] = {}  # product_b_id -> global_id
        name_dim: Optional[int] = None
        desc_dim: Optional[int] = None
        name_index: Optional[faiss.Index] = None
        desc_index: Optional[faiss.Index] = None

        store_b_name_global_arr: Optional[np.ndarray] = None
        store_b_desc_global_arr: Optional[np.ndarray] = None

        cache_key = None
        name_index_path = None
        desc_index_path = None
        meta_path = None
        if index_cache_dir:
            os.makedirs(index_cache_dir, exist_ok=True)
            cache_key = hashlib.md5(
                f"embedding_hf|{store_b}|{name_prop}|{desc_prop}".encode("utf-8")
            ).hexdigest()
            name_index_path = os.path.join(index_cache_dir, f"{cache_key}_name.index")
            desc_index_path = os.path.join(index_cache_dir, f"{cache_key}_desc.index")
            meta_path = os.path.join(index_cache_dir, f"{cache_key}_meta.pkl")

            if (
                not rebuild_index
                and os.path.exists(name_index_path)
                and os.path.exists(desc_index_path)
                and os.path.exists(meta_path)
            ):
                try:
                    logging.info(f"⚡ Loading FAISS indexes from cache: {index_cache_dir}")
                    name_index = faiss.read_index(name_index_path)
                    desc_index = faiss.read_index(desc_index_path)
                    with open(meta_path, "rb") as f:
                        meta = pickle.load(f)

                    global_to_product_id = list(meta["global_to_product_id"])
                    store_b_name_global_arr = np.asarray(meta["store_b_name_global"], dtype=np.int64)
                    store_b_desc_global_arr = np.asarray(meta["store_b_desc_global"], dtype=np.int64)

                    name_dim = int(name_index.d)
                    desc_dim = int(desc_index.d)
                except Exception as e:
                    logging.warning(f"⚠️ Failed to load FAISS cache, rebuilding: {e}")
                    name_index = None
                    desc_index = None
                    store_b_name_global_arr = None
                    store_b_desc_global_arr = None
                    global_to_product_id = []
                    global_id_by_product_id = {}

        needs_build = (
            name_index is None
            or desc_index is None
            or store_b_name_global_arr is None
            or store_b_desc_global_arr is None
        )
        if needs_build:
            logging.info(f"🏗️  Building FAISS indexes for store_b='{store_b}'...")

        build_name_batch_global: List[int] = []
        build_name_batch_vecs: List[np.ndarray] = []
        build_desc_batch_global: List[int] = []
        build_desc_batch_vecs: List[np.ndarray] = []

        def _flush_name_build_batch():
            nonlocal name_index
            if not build_name_batch_global:
                return
            mat = np.vstack(build_name_batch_vecs).astype(np.float32, copy=False)
            if name_index is None:
                name_index = _new_faiss_index(mat.shape[1])
            name_index.add(mat)
            store_b_name_global.extend(build_name_batch_global)
            build_name_batch_global.clear()
            build_name_batch_vecs.clear()

        def _flush_desc_build_batch():
            nonlocal desc_index
            if not build_desc_batch_global:
                return
            mat = np.vstack(build_desc_batch_vecs).astype(np.float32, copy=False)
            if desc_index is None:
                desc_index = _new_faiss_index(mat.shape[1])
            desc_index.add(mat)
            store_b_desc_global.extend(build_desc_batch_global)
            build_desc_batch_global.clear()
            build_desc_batch_vecs.clear()

        # MapReduce-style build: map = fetch embeddings in parallel batches, reduce = add to FAISS.
        # This keeps FAISS writes single-threaded while using multi-worker sessions to Neo4j.
        def _reduce_records(records: List[Tuple[str, Any, Any]]) -> None:
            nonlocal name_dim, desc_dim
            for product_b_id, name_emb, desc_emb in records:
                if not product_b_id:
                    continue

                global_id = global_id_by_product_id.get(product_b_id)
                if global_id is None:
                    global_id = len(global_to_product_id)
                    global_id_by_product_id[product_b_id] = global_id
                    global_to_product_id.append(product_b_id)

                name_vec = (
                    _normalize(np.asarray(name_emb, dtype=np.float32))
                    if isinstance(name_emb, list) and name_emb
                    else None
                )
                desc_vec = (
                    _normalize(np.asarray(desc_emb, dtype=np.float32))
                    if isinstance(desc_emb, list) and desc_emb
                    else None
                )

                if name_vec is not None:
                    if name_dim is None:
                        name_dim = int(name_vec.shape[0])
                    if int(name_vec.shape[0]) == int(name_dim):
                        build_name_batch_global.append(int(global_id))
                        build_name_batch_vecs.append(name_vec)
                        if len(build_name_batch_global) >= batch_size:
                            _flush_name_build_batch()
                    else:
                        logging.warning(
                            f"⚠️ Name embedding dim mismatch for product_b {product_b_id}: {int(name_vec.shape[0])} vs expected {name_dim}"
                        )

                if desc_vec is not None:
                    if desc_dim is None:
                        desc_dim = int(desc_vec.shape[0])
                    if int(desc_vec.shape[0]) == int(desc_dim):
                        build_desc_batch_global.append(int(global_id))
                        build_desc_batch_vecs.append(desc_vec)
                        if len(build_desc_batch_global) >= batch_size:
                            _flush_desc_build_batch()
                    else:
                        logging.warning(
                            f"⚠️ Description embedding dim mismatch for product_b {product_b_id}: {int(desc_vec.shape[0])} vs expected {desc_dim}"
                        )

        def _fetch_store_b_ids() -> List[str]:
            """Fetch all relationship IDs (rb.id) for store B once."""
            query_ids = """
            MATCH (sb:Store {name: $store_b})-[rb:SELLS]->(b:Product)
            RETURN rb.id AS id
            """
            ids: List[str] = []
            with driver.session() as session:
                result = session.run(query_ids, {"store_b": store_b})
                for record in result:
                    pid = record.get("id")
                    if pid:
                        ids.append(pid)
            return ids

        def _fetch_store_b_batch(batch_ids: List[str]) -> List[Tuple[str, Any, Any]]:
            """Fetch name/desc embeddings for a batch of store B product IDs."""
            query_batch = f"""
            MATCH (sb:Store {{name: $store_b}})-[rb:SELLS]->(b:Product)
            WHERE rb.id IN $batch_ids
            RETURN rb.id AS id,
                   b.`{name_prop}` AS name_emb,
                   b.`{desc_prop}` AS desc_emb
            """
            rows: List[Tuple[str, Any, Any]] = []
            with driver.session() as session:
                result = session.run(query_batch, {"store_b": store_b, "batch_ids": batch_ids})
                for record in result:
                    rows.append((record.get("id"), record.get("name_emb"), record.get("desc_emb")))
            return rows

        # If cache wasn't loaded, build from Neo4j.
        if needs_build:
            ids = _fetch_store_b_ids()
            if not ids:
                logging.warning(f"⚠️ No products found for store_b='{store_b}'.")
                return {p.get("id"): [] for p in products_a if p.get("id")}

            fbw = max(1, int(store_b_fetch_workers))
            fbs = max(100, int(store_b_fetch_batch_size))
            batches = _chunks(ids, fbs)
            logging.info(
                f"🧩 MapReduce build: {len(ids)} products in {len(batches)} batches (workers={min(fbw, len(batches))})"
            )

            # Map: fetch embeddings concurrently.
            # Reduce: normalize + add to FAISS in the main thread.
            if fbw == 1 or len(batches) <= 1:
                for b in batches:
                    rows = _fetch_store_b_batch(b)
                    if rows:
                        _reduce_records(rows)
            else:
                with ThreadPoolExecutor(max_workers=min(fbw, len(batches))) as ex:
                    futures = [ex.submit(_fetch_store_b_batch, b) for b in batches]
                    for future in as_completed(futures):
                        rows = future.result()
                        if rows:
                            _reduce_records(rows)

            _flush_name_build_batch()
            _flush_desc_build_batch()

        if name_index is None and desc_index is None:
            logging.warning(f"⚠️ No valid embeddings found for store_b='{store_b}'.")
            return {p.get("id"): [] for p in products_a if p.get("id")}

        if name_index is not None:
            logging.info(f"✅ FAISS name index built: {len(store_b_name_global)} vectors (dim={name_index.d})")
        else:
            logging.warning(f"⚠️ No name embeddings found for store_b='{store_b}'.")

        if desc_index is not None:
            logging.info(f"✅ FAISS description index built: {len(store_b_desc_global)} vectors (dim={desc_index.d})")
        else:
            logging.warning(f"⚠️ No description embeddings found for store_b='{store_b}'.")

        # If we don't have both indexes, we cannot compute the mean as required.
        if name_index is None or desc_index is None or name_dim is None or desc_dim is None:
            logging.warning(
                "⚠️ Both name and description embedding indexes are required for mean-based ranking. Returning no candidates."
            )
            return {p.get("id"): [] for p in products_a if p.get("id")}

        if store_b_name_global_arr is None:
            store_b_name_global_arr = np.asarray(store_b_name_global, dtype=np.int64)
        if store_b_desc_global_arr is None:
            store_b_desc_global_arr = np.asarray(store_b_desc_global, dtype=np.int64)

        # Save cache after building (so next run doesn't hit Neo4j at all).
        if needs_build and index_cache_dir and cache_key and name_index_path and desc_index_path and meta_path and not rebuild_index:
            try:
                faiss.write_index(name_index, name_index_path)
                faiss.write_index(desc_index, desc_index_path)
                with open(meta_path, "wb") as f:
                    pickle.dump(
                        {
                            "global_to_product_id": global_to_product_id,
                            "store_b_name_global": store_b_name_global_arr,
                            "store_b_desc_global": store_b_desc_global_arr,
                        },
                        f,
                        protocol=pickle.HIGHEST_PROTOCOL,
                    )
                logging.info(f"💾 FAISS cache saved to: {index_cache_dir}")
            except Exception as e:
                logging.warning(f"⚠️ Failed saving FAISS cache: {e}")

        t_build1 = time.perf_counter()
        logging.info(f"⏱️  FAISS index ready in {(t_build1 - t_build0):.2f}s")

        # --------------------------------------------
        # 2) Fetch embeddings for store A (batched)
        # --------------------------------------------
        product_a_ids = [p.get("id") for p in products_a if p.get("id")]
        a_embeddings = _fetch_embeddings_for_ids(
            store_name=store_a,
            ids=product_a_ids,
            fetch_batch_size=int(store_a_fetch_batch_size),
            fetch_workers=int(store_a_fetch_workers),
            store_param_name="store_a",
        )

        # --------------------------------------------
        # 3) Batched FAISS search (name + description)
        # --------------------------------------------
        a_ids: List[str] = []
        a_name_mat_rows: List[np.ndarray] = []
        a_desc_mat_rows: List[np.ndarray] = []
        for a_id in product_a_ids:
            a_name_vec, a_desc_vec = a_embeddings.get(a_id, (None, None))
            if a_name_vec is None or a_desc_vec is None:
                continue
            if int(a_name_vec.shape[0]) != int(name_dim) or int(a_desc_vec.shape[0]) != int(desc_dim):
                continue
            a_ids.append(a_id)
            a_name_mat_rows.append(a_name_vec)
            a_desc_mat_rows.append(a_desc_vec)

        if not a_ids:
            return {p.get("id"): [] for p in products_a if p.get("id")}

        a_name_mat = np.vstack(a_name_mat_rows).astype(np.float32, copy=False)
        a_desc_mat = np.vstack(a_desc_mat_rows).astype(np.float32, copy=False)

        # Search wider than top_k, then take the INTERSECTION of name+desc candidates.
        # Intersection guarantees both similarities are available for every exported candidate,
        # avoiding placeholder 0.0 values.
        wf = max(1, int(widen_factor))
        k_name = min(max(int(top_k) * wf, int(top_k)), int(store_b_name_global_arr.shape[0]))
        k_desc = min(max(int(top_k) * wf, int(top_k)), int(store_b_desc_global_arr.shape[0]))

        logging.info(
            f"🔎 FAISS batched querying: {len(a_ids)} products (k_name={k_name}, k_desc={k_desc}, top_k={top_k})"
        )

        # If using HNSW, set efSearch (higher = better recall, slower queries).
        try:
            if hasattr(name_index, "hnsw"):
                name_index.hnsw.efSearch = int(hnsw_ef_search)
            if hasattr(desc_index, "hnsw"):
                desc_index.hnsw.efSearch = int(hnsw_ef_search)
        except Exception:
            pass

        Dn, In = name_index.search(a_name_mat, k_name)
        Dd, Id = desc_index.search(a_desc_mat, k_desc)

        results: Dict[str, List[Dict[str, float]]] = {a_id: [] for a_id in product_a_ids}

        t_query0 = time.perf_counter()
        
        # --------------------------------------------
        # 4) Per-A intersection + mean ranking (Parallelized)
        # --------------------------------------------
        logging.info(f"⚡ Processing intersection and ranking for {len(a_ids)} products (Parallelized)...")
        
        def _process_batch_intersection(start_idx: int, end_idx: int) -> Dict[str, List[Dict[str, float]]]:
            batch_results = {}
            
            for row_idx in range(start_idx, end_idx):
                a_id = a_ids[row_idx]

                in_n = In[row_idx]
                dn = Dn[row_idx]
                # Filter invalid indices (-1)
                mask_n = in_n >= 0
                in_n = in_n[mask_n]
                dn = dn[mask_n]

                in_d = Id[row_idx]
                dd = Dd[row_idx]
                # Filter invalid indices (-1)
                mask_d = in_d >= 0
                in_d = in_d[mask_d]
                dd = dd[mask_d]

                if in_n.size == 0 or in_d.size == 0:
                    continue

                # Map FAISS indices to Global IDs
                ng = store_b_name_global_arr[in_n.astype(np.int64, copy=False)]
                dg = store_b_desc_global_arr[in_d.astype(np.int64, copy=False)]

                # Intersect: find candidates present in BOTH lists (Name AND Description)
                # This ensures we have both scores for the mean calculation
                common_g, idx_n, idx_d = np.intersect1d(ng, dg, return_indices=True)
                if common_g.size == 0:
                    continue

                n_sim = dn[idx_n].astype(np.float32, copy=False)
                d_sim = dd[idx_d].astype(np.float32, copy=False)
                
                # Filter out zero similarities if any (though unlikely with cosine unless orthogonal)
                valid = (n_sim != 0.0) & (d_sim != 0.0)
                if not np.any(valid):
                    continue

                common_g = common_g[valid]
                n_sim = n_sim[valid]
                d_sim = d_sim[valid]

                # Rank by mean of scores
                mean = (n_sim + d_sim) / 2.0
                
                # Top K selection
                limit = min(top_k, mean.size)
                if mean.size > limit:
                    # Partial sort for top K (faster than full sort)
                    sel = np.argpartition(-mean, limit - 1)[: limit]
                    # Sort the top K exactly
                    sel = sel[np.argsort(-mean[sel])]
                else:
                    sel = np.argsort(-mean)

                out_list: List[Dict[str, float]] = []
                for k in sel:
                    gid = int(common_g[k])
                    out_list.append(
                        {
                            "id": global_to_product_id[gid],
                            "product_name_embedding_similarity": float(n_sim[k]),
                            "description_embedding_similarity": float(d_sim[k]),
                        }
                    )
                batch_results[a_id] = out_list
            
            return batch_results

        # Execute in parallel chunks
        # Use a larger chunk size to reduce threading overhead
        chunk_size = max(100, len(a_ids) // (max_workers * 4))
        futures = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for i in range(0, len(a_ids), chunk_size):
                end = min(i + chunk_size, len(a_ids))
                futures.append(executor.submit(_process_batch_intersection, i, end))
                
            for future in as_completed(futures):
                try:
                    res = future.result()
                    results.update(res)
                except Exception as e:
                    logging.error(f"Error in intersection batch: {e}")

        t_query1 = time.perf_counter()
        logging.info(f"⏱️  FAISS query+rank time: {(t_query1 - t_query0):.2f}s")

        return results

        t_query1 = time.perf_counter()
        logging.info(f"⏱️  FAISS query+rank time: {(t_query1 - t_query0):.2f}s")

        return results
    
    def _get_ean_matches(
        self, 
        products_a: List[Dict[str, Any]], 
        store_a: str, 
        store_b: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Find exact matches based on EAN (European Article Number).
        
        Args:
            products_a: List of products from store A to check
            store_a: Name of origin store
            store_b: Name of destination store
            
        Returns:
            Dictionary mapping product_a_siid to match data with structure:
            {
                'product_a_siid': {
                    'product_a': {...},
                    'similar_products_b': [{...}],
                    'store_a': store_a,
                    'ean_match': True
                }
            }
        """
        ean_matches = {}
        
        logging.info(f"🔍 Checking EAN matches for {len(products_a)} products...")
        
        for product_a in products_a:
            product_a_id = product_a.get('id')
            product_a_siid = product_a.get('siid')
            
            # Query to get EAN from product A and find matching product in store B
            query = """
            MATCH (sa:Store {name: $store_a})-[ra:SELLS {id: $product_id}]->(pa:Product)
            WHERE pa.ean IS NOT NULL AND pa.ean <> ''
            WITH pa.ean AS ean, pa, ra
            MATCH (sb:Store {name: $store_b})-[rb:SELLS]->(pb:Product)
            WHERE pb.ean = ean
            RETURN 
                pa.name AS product_a_name,
                pa.description AS product_a_description,
                pa.url AS product_a_url,
                pa.ean AS ean,
                rb.id AS product_b_id,
                rb.siid AS product_b_siid,
                pb.name AS product_b_name,
                pb.description AS product_b_description,
                pb.url AS product_b_url
            LIMIT 1
            """
            
            try:
                result = self.category_analysis.execute_query(
                    query,
                    {
                        "store_a": store_a,
                        "store_b": store_b,
                        "product_id": product_a_id
                    }
                )
                
                if result and len(result) > 0:
                    match = result[0]
                    ean_value = match.get('ean')
                    
                    # Build product_a data
                    product_a_data = {
                        'id': product_a_id,
                        'siid': product_a_siid,
                        'product_name': match.get('product_a_name'),
                        'description': match.get('product_a_description'),
                        'url': match.get('product_a_url'),
                        'store': store_a,
                        'ean': ean_value
                    }
                    
                    # Build product_b match with perfect scores
                    product_b_match = {
                        'id': match.get('product_b_id'),
                        'siid': match.get('product_b_siid'),
                        'product_name': match.get('product_b_name'),
                        'description': match.get('product_b_description'),
                        'url': match.get('product_b_url'),
                        'store': store_b,
                        'ean': ean_value,
                        'weighted_score': 1.0,  # Perfect match
                        'name_similarity': 1.0,
                        'description_similarity': 1.0,
                        'euclidean_similarity': 1.0,
                        'combined_score': 1.0,
                        'overlap': 0,
                        'shared_nodes_details': [],
                        'match_type': 'ean',
                        'rank': 1
                    }
                    
                    ean_matches[product_a_siid] = {
                        'product_a': product_a_data,
                        'similar_products_b': [product_b_match],
                        'store_a': store_a,
                        'ean_match': True
                    }
                    
            except Exception as e:
                logging.warning(f"Error checking EAN for product {product_a_id}: {e}")
                continue
        
        if ean_matches:
            logging.info(f"✅ Found {len(ean_matches)} EAN matches")
        else:
            logging.info(f"ℹ️  No EAN matches found")
        
        return ean_matches
    
    def _get_existing_perfect_matches(self, store_a: str, store_b: str, product_siids: List[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Query PostgreSQL for existing perfect matches for the given store pair.
        
        Args:
            store_a: Name of the origin store
            store_b: Name of the destination store
            product_siids: Optional list of product SIIDs to check. If None, checks all products.
            
        Returns:
            Dictionary mapping product_a_siid to complete match data in the same format
            as similarity results, with structure:
            {
                'product_a_siid': {
                    'product_a': {...},
                    'similar_products_b': [{...}],
                    'store_a': store_a
                }
            }
        """
        perfect_matches = {}
        
        try:
            conn = psycopg2.connect(
                host=POSTGRES_DB_CONFIG['host'],
                port=POSTGRES_DB_CONFIG['port'],
                database=POSTGRES_DB_CONFIG['database'],
                user=POSTGRES_DB_CONFIG['user'],
                password=POSTGRES_DB_CONFIG['password']
            )
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Build query with optional SIID filtering
            if product_siids and len(product_siids) > 0:
                query = """
                    SELECT 
                        product_a_id, product_a_siid, product_a_name, product_a_description, product_a_url, store_a,
                        product_b_id, product_b_siid, product_b_name, product_b_description, product_b_url, store_b,
                        graph_score, name_similarity, description_similarity, 
                        euclidean_similarity, combined_score, overlap_count, shared_nodes
                    FROM product_match_validation
                    WHERE store_a = %s 
                      AND store_b = %s 
                      AND validation_result = 'perfect'
                      AND product_a_siid = ANY(%s)
                """
                cursor.execute(query, (store_a, store_b, product_siids))
            else:
                query = """
                    SELECT 
                        product_a_id, product_a_siid, product_a_name, product_a_description, product_a_url, store_a,
                        product_b_id, product_b_siid, product_b_name, product_b_description, product_b_url, store_b,
                        graph_score, name_similarity, description_similarity, 
                        euclidean_similarity, combined_score, overlap_count, shared_nodes
                    FROM product_match_validation
                    WHERE store_a = %s 
                      AND store_b = %s 
                      AND validation_result = 'perfect'
                """
                cursor.execute(query, (store_a, store_b))
            rows = cursor.fetchall()
            
            if rows:
                logging.info(f"✅ Found {len(rows)} existing perfect matches in PostgreSQL for {store_a} → {store_b}")
                
                for row in rows:
                    product_a_siid = row['product_a_siid']
                    
                    # Build product_a data
                    product_a = {
                        'id': row['product_a_id'],
                        'siid': row['product_a_siid'],
                        'product_name': row['product_a_name'],
                        'description': row['product_a_description'],
                        'url': row['product_a_url'],
                        'store': row['store_a']
                    }
                    
                    # Build similar_products_b data (single perfect match)
                    similar_product_b = {
                        'id': row['product_b_id'],
                        'siid': row['product_b_siid'],
                        'product_name': row['product_b_name'],
                        'description': row['product_b_description'],
                        'url': row['product_b_url'],
                        'store': row['store_b'],
                        'weighted_score': row['graph_score'],
                        'name_similarity': row['name_similarity'],
                        'description_similarity': row['description_similarity'],
                        'euclidean_similarity': row['euclidean_similarity'],
                        'combined_score': row['combined_score'],
                        'overlap': row['overlap_count'],
                        'shared_nodes_details': row['shared_nodes'] if row['shared_nodes'] else []
                    }
                    
                    # Store in the same format as model results
                    perfect_matches[product_a_siid] = {
                        'product_a': product_a,
                        'similar_products_b': [similar_product_b],
                        'store_a': row['store_a']
                    }
            else:
                logging.info(f"ℹ️  No existing perfect matches found in PostgreSQL for {store_a} → {store_b}")
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            logging.error(f"❌ Error querying PostgreSQL for perfect matches: {e}")
        
        return perfect_matches
        

    def find_cross_store_similarities(
            self,
            store_a: str,
            store_b: str,
            min_matches: int = 5,
            weights_food: dict = None,
            weights_non_food_super: dict = None,
            weights_non_food_elec: dict = None,
            limit_products_a: int = None,
            distance_metric: str = 'cosine',
            max_workers: int = 100,
            max_embedding_workers: int = 100,
            id_obtention_method: str = 'neo4j',
            external_ids_path: str = None,
            id_lists: list = None,
            cat_score_threshold: float = 0.3,
            combined_score_threshold: float = 0.5,
            top_n: int = 3,
            min_graph_score: float = 0.3,
            min_name_similarity: float = 0.4,
            min_description_similarity: float = 0.4,
            min_euclidean_similarity: float = 0.0,
            avoid_duplicate_product_b: bool = True
        ) -> Dict[str, Dict[str, Any]]:
            """
            Complete cross-store similarity analysis combining graph structure and embeddings.
            
            This is the main entry point that orchestrates:
            1. Product loading from Neo4j or external file
            2. Graph-based similarity via CategoryAnalysis
            3. Embedding-based similarity via SimilarityAnalysis
            4. Score combination and sorting
            
            Args:
                store_a: Name of the origin store
                store_b: Name of the destination store
                min_matches: Minimum number of shared neighbors required
                weights_food: Complete configuration for FOOD products including:
                    - Graph weights: Brand, Format, First_level_ingredient, etc.
                    - Combined weights: graph, name, description, euclidean
                    Default: Uses DEFAULT_WEIGHTS_FOOD
                weights_non_food_super: Complete configuration for NON-FOOD SUPERMARKET products including:
                    - Graph weights: Brand, Format, Internal_Subcategory, etc. (no ingredients)
                    - Combined weights: graph, name, description, euclidean
                    Default: Uses DEFAULT_WEIGHTS_NON_FOOD
                weights_non_food_elec: Complete configuration for NON-FOOD ELECTRONICS products including:
                    - Graph weights: Brand, Format, Internal_Subcategory, etc. (no ingredients)
                    - Combined weights: graph, name, description, euclidean
                    Default: Uses DEFAULT_WEIGHTS_NON_FOOD
                limit_products_a: Limit number of products from store A to process
                distance_metric: Distance metric for embeddings (cosine, euclidean, dot_product, manhattan)
                max_workers: Maximum number of parallel workers for processing products A
                max_embedding_workers: Maximum number of parallel workers for embedding generation
                id_obtention_method: Method to obtain product IDs. Options:
                    - 'neo4j' (default): Load all products from Neo4j store
                    - 'external_file': Read product IDs from external file
                    - 'id_list': Use provided list of IDs
                external_ids_path: Path to file containing product IDs (required if id_obtention_method='external_file')
                id_lists: List of product IDs to process (required if id_obtention_method='id_list')
                cat_score_threshold: Maximum category graph score difference from best match to include
                combined_score_threshold: Maximum graph score difference from best match to include
                top_n: Number of top similar products to return for each product (default: 3)
                min_graph_score: Minimum graph-based similarity score (default: 0.8)
                min_name_similarity: Minimum name embedding similarity score (default: 0.8)
                min_description_similarity: Minimum description embedding similarity score (default: 0.8)
                min_euclidean_similarity: Minimum euclidean distance similarity score (default: 0.7)
            
            Returns:
                Dictionary mapping product IDs to their similar products with metadata
            """
            
            # Extract graph weights and combined weights from consolidated dictionaries
            graph_keys = ["Brand", "Format", "Unit_measure", "First_level_ingredient", 
                         "Second_level_ingredient", "Allergen", "Internal_Subcategory", 
                         "Country_of_Origin", "Other_Ingredient", "Quantity"]
            
            combined_keys = ["graph", "name", "description", "euclidean"]
            
            graph_weights_food = {k: weights_food[k] for k in graph_keys if k in weights_food}
            graph_weights_non_food_super = {k: weights_non_food_super[k] for k in graph_keys if k in weights_non_food_super}
            graph_weights_non_food_elec = {k: weights_non_food_elec[k] for k in graph_keys if k in weights_non_food_elec}
            combined_weights_food = {k: weights_food[k] for k in combined_keys if k in weights_food}
            combined_weights_non_food_super = {k: weights_non_food_super[k] for k in combined_keys if k in weights_non_food_super}
            combined_weights_non_food_elec = {k: weights_non_food_elec[k] for k in combined_keys if k in weights_non_food_elec}
            
            # ============================================================
            # STEP 1: LOAD PRODUCTS FROM STORE A
            # ============================================================
            logging.info("\n" + "="*70)
            logging.info("  CROSS-STORE SIMILARITY ANALYSIS")
            logging.info("="*70)
            logging.info(f"Store A: {store_a}")
            logging.info(f"Store B: {store_b}")
            logging.info(f"Distance metric: {distance_metric}")
            logging.info("="*70)
            
            # ============================================================
            # STEP 1: LOAD PRODUCTS FROM SPECIFIED SOURCE
            # ============================================================
            if id_obtention_method == 'external_file':
                if not external_ids_path:
                    logging.error("❌ id_obtention_method='external_file' but no external_ids_path provided")
                    return {}
                
                products_a, not_found_ids = self.category_analysis.load_products_from_external_ids(
                    external_ids_path=external_ids_path,
                    store_a=store_a,
                    limit_products_a=limit_products_a
                )
            elif id_obtention_method == 'id_list':
                if not id_lists:
                    logging.error("❌ id_obtention_method='id_list' but no id_lists provided")
                    return {}
                
                products_a, not_found_ids = self.category_analysis.load_products_from_id_list(
                    id_list=id_lists,
                    store_a=store_a,
                    limit_products_a=limit_products_a
                )
            else:  # 'neo4j' or default
                products_a = self.category_analysis.load_products_from_store(
                    store_a=store_a,
                    limit_products_a=limit_products_a
                )
                not_found_ids = []
            
            if not products_a and not not_found_ids:
                logging.warning(f"No products found in store {store_a}")
                return {}
            
            # ============================================================
            # STEP 2: CHECK EAN MATCHES
            # ============================================================
            logging.info(f"\n🏷️  STEP 2: Checking for EAN matches")
            
            ean_matches = self._get_ean_matches(
                products_a=products_a,
                store_a=store_a,
                store_b=store_b
            )
            
            # ============================================================
            # STEP 3: CHECK FOR EXISTING PERFECT MATCHES (ONLY FOR LOADED PRODUCTS)
            # ============================================================
            logging.info("\n🔍 STEP 3: Checking PostgreSQL for existing perfect matches")
            
            # Get SIIDs of loaded products to check for perfect matches
            loaded_siids = [p.get('siid') for p in products_a if p.get('siid')]
            
            existing_perfect_matches = self._get_existing_perfect_matches(
                store_a=store_a, 
                store_b=store_b,
                product_siids=loaded_siids  # Only check for products we actually loaded
            )
            
            if existing_perfect_matches:
                logging.info(f"✅ Found {len(existing_perfect_matches)} existing perfect matches for loaded products")
                logging.info(f"   These products will be returned directly without model execution")
            else:
                logging.info(f"ℹ️  No existing perfect matches found for the {len(loaded_siids)} loaded products")
            
            
            # ============================================================
            # STEP 4: FILTER OUT PRODUCTS WITH EAN OR PERFECT MATCHES
            # ============================================================
            products_to_process = []
            ean_skipped = 0
            perfect_skipped = 0
            
            for product in products_a:
                product_siid = product.get('siid')
                if product_siid and product_siid in ean_matches:
                    ean_skipped += 1
                elif product_siid and product_siid in existing_perfect_matches:
                    perfect_skipped += 1
                else:
                    products_to_process.append(product)
            
            total_skipped = ean_skipped + perfect_skipped
            if total_skipped > 0:
                logging.info(f"\n⏭️  STEP 4: Skipping {total_skipped} products with existing matches:")
                if ean_skipped > 0:
                    logging.info(f"   - {ean_skipped} EAN matches")
                if perfect_skipped > 0:
                    logging.info(f"   - {perfect_skipped} perfect matches from PostgreSQL")
                logging.info(f"🔄 Processing {len(products_to_process)} remaining products through model")
            
            # If all products already have matches, return them directly
            if not products_to_process:
                logging.info(f"\n✅ All {len(products_a)} products already have matches! Skipping model execution.")
                # Merge EAN matches and perfect matches
                all_matches = {**ean_matches, **existing_perfect_matches}
                return all_matches
            
             # ============================================================
            # STEP 5: EMBEDDING-BASED SIMILARITY
            # ============================================================
            logging.info(f"\n🤖 STEP 5: Embedding-based similarity analysis")
            logging.info(f"   Calculating text similarities using {distance_metric} distance...")
            embedding_candidates = self._run_embedding_hf(
                products_a=products_to_process,
                store_a=store_a,
                store_b=store_b
                )


            # ============================================================
            # STEP 6: GRAPH-BASED SIMILARITY (CATEGORY ANALYSIS)
            # ============================================================
            logging.info(f"\n🔍 STEP 6: Graph-based similarity analysis for {len(products_to_process)} products")
            logging.info(f"   Finding similar products using graph structure...")

            results = self._run_category_analysis(
                products_a=products_to_process,  # Use filtered list
                store_a=store_a,
                store_b=store_b,
                candidates_by_product=embedding_candidates,
                min_matches=min_matches,
                graph_weights=graph_weights_food,
                graph_weights_non_food_super=graph_weights_non_food_super,
                graph_weights_non_food_elec=graph_weights_non_food_elec,
                max_workers=max_workers,
                not_found_ids=not_found_ids,
                cat_score_threshold=cat_score_threshold,
                min_graph_score=min_graph_score
            )


            # ============================================================
            # STEP 6: EMBEDDING-BASED SIMILARITY
            # ============================================================
            # logging.info(f"\n🤖 STEP 6: Embedding-based similarity analysis")
            # logging.info(f"   Calculating text similarities using {distance_metric} distance...")
            # results = self._run_embedding_analysis(
            #     results=results,
            #     combined_weights_food=combined_weights_food,
            #     combined_weights_non_food_super=combined_weights_non_food_super,
            #     combined_weights_non_food_elec=combined_weights_non_food_elec,
            #     distance_metric=distance_metric,
            #     max_workers=max_workers,
            #     max_embedding_workers=max_embedding_workers,
            #     store_a=store_a,
            #     min_name_similarity=min_name_similarity,
            #     min_description_similarity=min_description_similarity
            # )
            # ============================================================
            # STEP 7: EUCLIDEAN DISTANCE CALCULATION
            # ============================================================
            logging.info(f"\n📐 STEP 7: Euclidean distance calculation")
            logging.info(f"   Calculating numerical feature similarities...")
            
            results = self._run_euclidean_distance(
                results=results,
                max_workers=max_workers,
                min_euclidean_similarity=min_euclidean_similarity
            )

            # ============================================================
            # STEP 8: COMBINE ALL SCORES
            # ============================================================
            logging.info(f"\n🔢 STEP 8: Combining all similarity scores")
            logging.info(f"   Final score calculation with all 4 components...")
           
            results = self._combine_all_scores(
                results=results,
                combined_weights_food=combined_weights_food,
                combined_weights_non_food_super=combined_weights_non_food_super,
                combined_weights_non_food_elec=combined_weights_non_food_elec,
                store_a=store_a,
                combined_score_threshold=combined_score_threshold,
                top_n=top_n
            )
            
            # ============================================================
            # STEP 8.5: MERGE WITH EAN MATCHES AND EXISTING PERFECT MATCHES
            # ============================================================
            if ean_matches or existing_perfect_matches:
                logging.info(f"\n🔗 STEP 8.5: Merging results with existing matches")
                
                if ean_matches:
                    logging.info(f"   Adding {len(ean_matches)} EAN matches...")
                    for product_siid, match_data in ean_matches.items():
                        results[product_siid] = match_data
                
                if existing_perfect_matches:
                    logging.info(f"   Adding {len(existing_perfect_matches)} perfect matches from PostgreSQL...")
                    for product_siid, match_data in existing_perfect_matches.items():
                        results[product_siid] = match_data
                
                logging.info(f"✅ Total results after merge: {len(results) - 1} products (excluding metadata)")
            
            # ============================================================
            # STEP 9: CLEAN DUPLICATE PRODUCT_B MATCHES
            # ============================================================
            if avoid_duplicate_product_b:
                results = self._clean_duplicate_product_b(results, strict_mode=True)
            else:
                # Allow duplicates but assign at least one match per product
                results = self._clean_duplicate_product_b(results, strict_mode=False)
            
            # ============================================================
            # STEP 10: ADD METADATA
            # ============================================================
            results['_metadata'] = {
                'store_a': store_a,
                'store_b': store_b,
                'min_matches': min_matches,
                'weights_food': weights_food,
                'weights_non_food_super': weights_non_food_super,
                'weights_non_food_elec': weights_non_food_elec,
                'distance_metric': distance_metric,
                'cat_score_threshold': cat_score_threshold, 
                'combined_score_threshold': combined_score_threshold,
                'max_workers': max_workers,
                'max_embedding_workers': max_embedding_workers,
                'id_obtention_method': id_obtention_method,
                'external_ids_path': external_ids_path if id_obtention_method == 'external_file' else None,
                'id_lists_count': len(id_lists) if id_lists else 0,
                'top_n': top_n,
                'existing_perfect_matches_count': len(existing_perfect_matches),
                'ean_matches_count': len(ean_matches),
                'new_matches_from_model': len(results) - len(existing_perfect_matches) - len(ean_matches) - 1  # -1 for metadata
            }
            logging.info("\n" + "="*70)
            logging.info("✅ ANALYSIS COMPLETE!")
            logging.info(f"   Perfect matches from PostgreSQL: {len(existing_perfect_matches)}")
            logging.info(f"   EAN matches: {len(ean_matches)}")
            logging.info(f"   New matches from model: {len(results) - len(existing_perfect_matches) - len(ean_matches) - 1}")
            logging.info(f"   Total products: {len(results) - 1}")
            logging.info(f"   Products with matches: {sum(1 for k, r in results.items() if k != '_metadata' and r.get('similar_products_b'))}")
            logging.info("="*70 + "\n")
            
            # ============================================================
            # STEP 11: EXPORT AND PERSIST RESULTS (WITH CLEANED DATA)
            # ============================================================

            # Ensure similarity display fields exist in the output dict
            for _product_a_id, _data in results.items():
                if _product_a_id == '_metadata':
                    continue
                for prod_b in _data.get('similar_products_b', []) or []:
                    name_val = prod_b.get('name_similarity', None)
                    if name_val is None:
                        name_val = prod_b.get('product_name_embedding_similarity', None)

                    desc_val = prod_b.get('description_similarity', None)
                    if desc_val is None:
                        desc_val = prod_b.get('description_embedding_similarity', None)

                    prod_b['Name_Similarity'] = round(float(name_val), 3) if name_val is not None else ''
                    prod_b['Description_Similarity'] = round(float(desc_val), 3) if desc_val is not None else ''
            
            # Insert to PostgreSQL for validation (with cleaned data)
            insert_to_validate(results)
            
            return results
    
    def _run_category_analysis(
        self,
        products_a: List[Dict[str, Any]],
        store_a: str,
        store_b: str,
        candidates_by_product: Dict[str, List[Dict[str, float]]],
        min_matches: int,
        graph_weights: Dict[str, float],
        graph_weights_non_food_super: Dict[str, float],
        graph_weights_non_food_elec: Dict[str, float],
        max_workers: int,
        not_found_ids: List[str],
        cat_score_threshold: float,
        min_graph_score: float = 0.8
    ) -> Dict[str, Dict[str, Any]]:
        """
        Run category-based (graph) analysis to score a provided candidate set.
        
        For each product A, this scores ONLY the candidate product B IDs provided by
        `candidates_by_product` (e.g. top-200 from `_run_embedding_hf`). It does not
        apply additional filtering/limiting; it returns the graph score (`weighted_score`)
        for the provided candidates.
        """
        total = len(products_a)
        logging.info(f"🚀 Processing {total} products with {max_workers} workers...")
        
        results = {}
        completed_count = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks - select weights based on product type
            future_to_product = {}
            
            for idx, product_a in enumerate(products_a, 1):
                # Determine product type and category, then select appropriate weights
                product_type = self._get_product_internal_type(product_a, store_a)
                product_category = self._get_product_internal_category(product_a, store_a)

                if product_type and product_type.lower() == 'non-food':
                    # Distinguish between supermarket and electronics non-food
                    if product_category and product_category.lower() in ["Electronics", "Computing" , "Gaming", "Toys"]:
                        selected_weights = graph_weights_non_food_elec
                    else:
                        selected_weights = graph_weights_non_food_super
                else:
                    selected_weights = graph_weights

                product_a_id = product_a.get('id')
                candidate_entries = candidates_by_product.get(product_a_id, []) if product_a_id else []
                candidate_ids = [c.get('id') for c in candidate_entries if c.get('id')]
                name_embedding_sim_map = {
                    c.get('id'): float(c.get('product_name_embedding_similarity', 0.0))
                    for c in candidate_entries
                    if c.get('id')
                }

                desc_embedding_sim_map = {
                    c.get('id'): float(c.get('description_embedding_similarity', 0.0))
                    for c in candidate_entries
                    if c.get('id')
                }
                
                future = executor.submit(
                    self.category_analysis.score_candidates_by_category,
                    product_a,
                    store_a,
                    store_b,
                    candidate_ids,
                    selected_weights
                )
                future_to_product[future] = (product_a, name_embedding_sim_map, desc_embedding_sim_map)
            
            # Collect results as they complete
            for future in as_completed(future_to_product):
                try:
                    similar_products_b = future.result()
                    product_a, name_embedding_sim_map, desc_embedding_sim_map = future_to_product[future]
                    product_a_id = product_a.get('id')

                    # Attach embedding similarity from the prefilter stage (if available)
                    for prod in similar_products_b:
                        b_id = prod.get('id')
                        if b_id in name_embedding_sim_map:
                            prod['product_name_embedding_similarity'] = name_embedding_sim_map[b_id]
                        if b_id in desc_embedding_sim_map:
                            prod['description_embedding_similarity'] = desc_embedding_sim_map[b_id]

                    results[product_a_id] = {
                        'product_a': product_a,
                        'similar_products_b': similar_products_b,
                        'store_a': store_a
                    }
                    completed_count += 1
                    
                    # Progress update every 5 products or at completion
                    if completed_count % 5 == 0 or completed_count == total:
                        pct = (completed_count / total) * 100
                        logging.info(f"📊 Progress: {completed_count}/{total} ({pct:.1f}%) products processed")
                        
                except Exception as e:
                    product_a, _ = future_to_product[future]
                    logging.error(f"❌ Error processing product {product_a.get('id', 'N/A')}: {e}")

        # Add not-found products to results
        if not_found_ids:
            for not_found_id in not_found_ids:
                results[f"NOT_FOUND_{not_found_id}"] = {
                    'product_a': {
                        'id': not_found_id,
                        'product_name': 'NOT FOUND IN NEO4J',
                        'description': f'Product ID {not_found_id} does not exist in the database',
                        'url': '',
                        'store_a': store_a
                    },
                    'similar_products_b': [],
                    'not_found': True,
                    'store_a': store_a
                }
        
        logging.info(f"✅ Found graph-based matches for {sum(1 for r in results.values() if r.get('similar_products_b') and not r.get('not_found', False))} products")
        
        return results
    
    def _get_product_internal_type(
        self,
        product_a: Dict[str, Any],
        store_a: str
    ) -> str:
        """
        Get the Internal_Type for a product from Neo4j.
        
        Args:
            product_a: Product dictionary with 'id' field
            store_a: Store name
            
        Returns:
            Internal_Type name (e.g., 'Food', 'Non-Food') or None if not found
        """
        query = """
        MATCH (s:Store {name: $store_a})-[r:SELLS {id: $product_id}]->(a:Product)
        OPTIONAL MATCH (a)-[:COVERS]->(subcat:Internal_Subcategory)
        OPTIONAL MATCH (subcat)-[:HAS_INTERNAL_SUBCATEGORY]->(cat:Internal_Category)
        OPTIONAL MATCH (cat)-[:HAS_INTERNAL_CATEGORY]->(type:Internal_Type)
        RETURN type.name AS internal_type
        """
        
        try:
            result = self.category_analysis.execute_query(
                query,
                {"store_a": store_a, "product_id": product_a.get('id')}
            )
            
            if result and len(result) > 0:
                return result[0].get('internal_type')
        except Exception as e:
            logging.warning(f"Could not get Internal_Type for product {product_a.get('id')}: {e}")
        
        return None
    
    def _get_product_internal_category(
        self,
        product_a: Dict[str, Any],
        store_a: str
    ) -> str:
        """
        Get the Internal_Category for a product from Neo4j.
        
        Args:
            product_a: Product dictionary with 'id' field
            store_a: Store name
            
        Returns:
            Internal_Category name (e.g., 'Electronics', 'Home & Garden') or None if not found
        """
        query = """
        MATCH (s:Store {name: $store_a})-[r:SELLS {id: $product_id}]->(a:Product)
        OPTIONAL MATCH (a)-[:COVERS]->(subcat:Internal_Subcategory)
        OPTIONAL MATCH (subcat)-[:HAS_INTERNAL_SUBCATEGORY]->(cat:Internal_Category)
        RETURN cat.name AS internal_category
        """
        
        try:
            result = self.category_analysis.execute_query(
                query,
                {"store_a": store_a, "product_id": product_a.get('id')}
            )
            
            if result and len(result) > 0:
                return result[0].get('internal_category')
        except Exception as e:
            logging.warning(f"Could not get Internal_Category for product {product_a.get('id')}: {e}")
        
        return None
    
    def _run_embedding_analysis(
        self,
        results: Dict[str, Dict[str, Any]],
        combined_weights_food: Dict[str, float],
        combined_weights_non_food_super: Dict[str, float],
        combined_weights_non_food_elec: Dict[str, float],
        distance_metric: str,
        max_workers: int,
        max_embedding_workers: int,
        store_a: str,
        min_name_similarity: float = 0.4,
        min_description_similarity: float = 0.4,
        embedding_batch_size: int = 8000
    ) -> Dict[str, Dict[str, Any]]:
        """
        Run embedding-based similarity analysis and combine with graph scores.
        Processes embeddings in batches to avoid memory issues.
        
        Uses SimilarityAnalysis to:
        1. Divide products into batches
        2. For each batch: Load embeddings -> Process -> Clear memory
        3. Calculate semantic similarity in parallel
        4. Combine with graph scores using configured weights
        5. Sort results by combined score
        
        Args:
            min_name_similarity: Minimum name embedding similarity score (0-1)
            min_description_similarity: Minimum description embedding similarity score (0-1)
            embedding_batch_size: Number of products to process per batch (default: 8000)
        """
        import gc
        
        # Filter products with matches
        products_with_matches = {
            product_a_id: data 
            for product_a_id, data in results.items() 
            if data.get('similar_products_b') and product_a_id != '_metadata'
        }
        
        total_products_with_matches = len(products_with_matches)
        
        if total_products_with_matches == 0:
            logging.info("⚠️ No products with matches found, skipping embedding analysis")
            return results
        
        logging.info(f"🚀 Processing {total_products_with_matches} products in batches of {embedding_batch_size}")
        logging.info(f"   Using {max_embedding_workers} parallel workers per batch")
        
        # Divide products into batches
        product_ids = list(products_with_matches.keys())
        num_batches = (len(product_ids) + embedding_batch_size - 1) // embedding_batch_size
        
        embedding_completed = 0
        
        for batch_idx in range(num_batches):
            start_idx = batch_idx * embedding_batch_size
            end_idx = min((batch_idx + 1) * embedding_batch_size, len(product_ids))
            batch_product_ids = product_ids[start_idx:end_idx]
            
            batch_size = len(batch_product_ids)
            logging.info(f"\n📦 Processing batch {batch_idx + 1}/{num_batches} ({batch_size} products)")
            
            # Create subset of results for this batch
            batch_results = {
                product_id: products_with_matches[product_id] 
                for product_id in batch_product_ids
            }
            
            # Load embeddings only for this batch (memory-efficient)
            logging.info(f"   ⬇️  Loading embeddings for batch {batch_idx + 1}...")
            embedding_cache = self.similarity_analysis.preload_embeddings_for_batch(batch_results)
            logging.info(f"   ✅ Loaded {len(embedding_cache)} embeddings for batch {batch_idx + 1}")
            
            # Process this batch in parallel
            with ThreadPoolExecutor(max_workers=max_embedding_workers) as executor:
                future_to_product_id = {
                    executor.submit(
                        self.similarity_analysis.process_embeddings_for_product,
                        product_a_id,
                        batch_results[product_a_id]['product_a'],
                        batch_results[product_a_id]['similar_products_b'],
                        distance_metric,
                        embedding_cache,
                        min_name_similarity,
                        min_description_similarity
                    ): product_a_id
                    for product_a_id in batch_product_ids
                }
                
                # Collect results for this batch
                for future in as_completed(future_to_product_id):
                    try:
                        sorted_similar_b = future.result()
                        product_a_id = future_to_product_id[future]
                        
                        if sorted_similar_b is not None:
                            results[product_a_id]['similar_products_b'] = sorted_similar_b
                        
                        embedding_completed += 1
                        
                        # Progress update
                        if embedding_completed % 5 == 0 or embedding_completed == total_products_with_matches:
                            pct = (embedding_completed / total_products_with_matches) * 100
                            logging.info(f"   📊 Progress: {embedding_completed}/{total_products_with_matches} ({pct:.1f}%) embeddings processed")
                            
                    except Exception as e:
                        product_a_id = future_to_product_id[future]
                        logging.error(f"   ❌ Error calculating embeddings for product {product_a_id}: {e}")
            
            # Clear embedding cache to free memory
            logging.info(f"   🧹 Clearing embedding cache for batch {batch_idx + 1}...")
            del embedding_cache
            del batch_results
            self.similarity_analysis.clear_embeddings_cache()  # Clear in-memory cache
            gc.collect()  # Force garbage collection
            logging.info(f"   ✅ Memory cleared for batch {batch_idx + 1}")
        
        logging.info(f"\n✅ All batches processed! Total embeddings calculated: {embedding_completed}")
        logging.info(f"   Distance metric: {distance_metric}")
        logging.info(f"   FOOD weights: Graph={combined_weights_food['graph']}, Name={combined_weights_food['name']}, Description={combined_weights_food['description']}, Euclidean={combined_weights_food.get('euclidean', 0.0)}")
        logging.info(f"   NON-FOOD SUPER weights: Graph={combined_weights_non_food_super['graph']}, Name={combined_weights_non_food_super['name']}, Description={combined_weights_non_food_super['description']}, Euclidean={combined_weights_non_food_super.get('euclidean', 0.0)}")
        logging.info(f"   NON-FOOD ELEC weights: Graph={combined_weights_non_food_elec['graph']}, Name={combined_weights_non_food_elec['name']}, Description={combined_weights_non_food_elec['description']}, Euclidean={combined_weights_non_food_elec.get('euclidean', 0.0)}")
        
        # Save embeddings cache to disk (only once at the end)
        logging.info("💾 Saving embeddings cache to disk...")
        self.similarity_analysis.save_embeddings_cache()

        return results
    
    def _run_euclidean_distance(
        self,
        results: Dict[str, Any],
        max_workers: int = 10,
        min_euclidean_similarity: float = 0.0
    ) -> Dict[str, Any]:
        """
        Step 4: Calculate euclidean distances for numerical features.
        
        Args:
            results: Dictionary with product_a_id as keys and similarity data as values
            max_workers: Number of parallel workers
            min_euclidean_similarity: Minimum euclidean similarity score (0-1)
            
        Returns:
            Updated results dictionary with euclidean_similarity added
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        # Filter products with matches
        products_to_process = {
            product_a_id: data
            for product_a_id, data in results.items()
            if product_a_id != '_metadata' and data.get('similar_products_b')
        }
        
        total_products = len(products_to_process)
        
        if total_products == 0:
            logging.info("⚠️ No products with matches found, skipping euclidean distance calculation")
            return results
        
        logging.info(f"🚀 Calculating euclidean distances for {total_products} products with {max_workers} workers...")
        
        completed = 0
        start_time = time.time()
        
        # Process products in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_product_id = {
                executor.submit(
                    process_product_euclidean,
                    product_a_id,
                    data['product_a'],
                    data['similar_products_b'],
                    min_euclidean_similarity
                    # Auto-selects: NumPy for <100 products, PostgreSQL-native for >=100
                ): product_a_id
                for product_a_id, data in products_to_process.items()
            }
            
            # Collect results
            for future in as_completed(future_to_product_id):
                try:
                    updated_similar_b = future.result()
                    product_a_id = future_to_product_id[future]
                    
                    if updated_similar_b is not None:
                        results[product_a_id]['similar_products_b'] = updated_similar_b
                    
                    completed += 1
                    
                    # Progress update
                    if completed % 10 == 0 or completed == total_products:
                        elapsed = time.time() - start_time
                        pct = (completed / total_products) * 100
                        logging.info(f"📊 Progress: {completed}/{total_products} ({pct:.1f}%) - {elapsed:.1f}s elapsed")
                        
                except Exception as e:
                    product_a_id = future_to_product_id[future]
                    logging.error(f"❌ Error processing product {product_a_id}: {e}")
        
        elapsed = time.time() - start_time
        logging.info(f"✅ Euclidean distances calculated in {elapsed:.1f}s")
        
        return results
    
    def _combine_all_scores(
        self,
        results: Dict[str, Any],
        combined_weights_food: Dict[str, float],
        combined_weights_non_food_super: Dict[str, float],
        combined_weights_non_food_elec: Dict[str, float],
        store_a: str,
        combined_score_threshold: float = 0.5,
        top_n: int = 3
    ) -> Dict[str, Any]:
        """
        Step 5: Combine all similarity scores (graph, name, description, euclidean).
        Selects appropriate weights based on product type and category.
        
        Args:
            results: Dictionary with product_a_id as keys and similarity data as values
            combined_weights_food: Weight dictionary for FOOD products
            combined_weights_non_food_super: Weight dictionary for NON-FOOD SUPERMARKET products
            combined_weights_non_food_elec: Weight dictionary for NON-FOOD ELECTRONICS products
            store_a: Store name to query product type/category
            top_n: Number of top similar products to return for each product
            
        Returns:
            Updated results dictionary with combined_score added and sorted
        """
        import time
        
        logging.info(f"   FOOD weights: Graph={combined_weights_food.get('graph', 0.3)}, Name={combined_weights_food.get('name', 0.3)}, Description={combined_weights_food.get('description', 0.2)}, Euclidean={combined_weights_food.get('euclidean', 0.2)}")
        logging.info(f"   NON-FOOD SUPER weights: Graph={combined_weights_non_food_super.get('graph', 0.3)}, Name={combined_weights_non_food_super.get('name', 0.3)}, Description={combined_weights_non_food_super.get('description', 0.2)}, Euclidean={combined_weights_non_food_super.get('euclidean', 0.2)}")
        logging.info(f"   NON-FOOD ELEC weights: Graph={combined_weights_non_food_elec.get('graph', 0.3)}, Name={combined_weights_non_food_elec.get('name', 0.3)}, Description={combined_weights_non_food_elec.get('description', 0.2)}, Euclidean={combined_weights_non_food_elec.get('euclidean', 0.2)}")
        
        # Filter products with matches
        products_to_process = {
            product_a_id: data
            for product_a_id, data in results.items()
            if product_a_id != '_metadata' and data.get('similar_products_b')
        }
        
        total_products = len(products_to_process)
        
        if total_products == 0:
            logging.info("⚠️ No products with matches found, skipping score combination")
            return results
        
        logging.info(f"🔢 Combining scores for {total_products} products...")
        
        start_time = time.time()
        processed = 0
        
        # Process each product
        for product_a_id, data in products_to_process.items():
            # Determine product type and category to select appropriate weights
            product_a = data.get('product_a', {})
            product_type = self._get_product_internal_type(product_a, store_a)
            product_category = self._get_product_internal_category(product_a, store_a)
            
            # Select appropriate weights
            if product_type and product_type.lower() == 'non-food':
                if product_category and 'electr' in product_category.lower():
                    combined_weights = combined_weights_non_food_elec
                else:
                    combined_weights = combined_weights_non_food_super
            else:
                combined_weights = combined_weights_food
            
            # Extract weights for this product
            graph_weight = combined_weights.get('graph', 0.3)
            name_weight = combined_weights.get('name', 0.3)
            desc_weight = combined_weights.get('description', 0.2)
            euclidean_weight = combined_weights.get('euclidean', 0.2)
            
            similar_products_b = data.get('similar_products_b', [])
            
            for product_b in similar_products_b:
                graph_score = product_b.get('weighted_score', 0.0)
                # Prefer the separated embedding similarities produced by `_run_embedding_hf`.
                # Fall back to legacy fields if present.
                name_sim = product_b.get('product_name_embedding_similarity', None)
                if name_sim is None:
                    name_sim = product_b.get('name_similarity', 0.0)

                desc_sim = product_b.get('description_embedding_similarity', None)
                if desc_sim is None:
                    desc_sim = product_b.get('description_similarity', 0.0)

                # Normalize into the legacy keys as well so downstream/export stays consistent.
                try:
                    product_b['name_similarity'] = float(name_sim)
                except Exception:
                    product_b['name_similarity'] = 0.0
                try:
                    product_b['description_similarity'] = float(desc_sim)
                except Exception:
                    product_b['description_similarity'] = 0.0

                name_sim = product_b.get('name_similarity', 0.0)
                desc_sim = product_b.get('description_similarity', 0.0)
                euclidean_sim = product_b.get('euclidean_similarity', 0.0)
                
                # Check if euclidean is available (non-zero means it was calculated)
                has_euclidean = euclidean_sim > 0.0 or product_b.get('siid') is not None
                
                if has_euclidean:
                    # Calculate combined score with all 4 components
                    combined = (
                        graph_weight * graph_score +
                        name_weight * name_sim +
                        desc_weight * desc_sim +
                        euclidean_weight * euclidean_sim
                    )
                else:
                    # Combined without euclidean (redistribute weight proportionally)
                    total_weight = graph_weight + name_weight + desc_weight
                    combined = (
                        (graph_weight / total_weight) * graph_score +
                        (name_weight / total_weight) * name_sim +
                        (desc_weight / total_weight) * desc_sim
                    )
                
                product_b['combined_score'] = float(combined)
            
            # Sort by combined score (descending)
            similar_products_b.sort(key=lambda x: x.get('combined_score', 0.0), reverse=True)
            
            # Filter by minimum combined_score threshold (0.5) and keep only top N products
            filtered_products = [
                prod for prod in similar_products_b 
                if prod.get('combined_score', 0.0) > combined_score_threshold
            ]
            results[product_a_id]['similar_products_b'] = filtered_products[:top_n]
            
            processed += 1
        
        elapsed = time.time() - start_time
        logging.info(f"✅ All scores combined and sorted in {elapsed:.1f}s")

        return results
    
    def _clean_duplicate_product_b(self, results: Dict[str, Any], strict_mode: bool = True) -> Dict[str, Any]:
        """
        Clean duplicate Product_B matches across all Product_A results using rank-based logic.
        Iteratively ensures that rank 1 matches have unique Product_B IDs by:
        1. Finding duplicate Product_B in rank 1
        2. Keeping only the match with highest combined_score
        3. Removing rank 1 from other product_a's (promoting rank 2 to rank 1)
        4. Re-checking for duplicates after promotions (within same iteration)
        5. Repeating until no duplicates exist in rank 1
        
        Args:
            results: Dictionary with product_a_id as keys and similarity data as values
            strict_mode: If True, some products may end up with no matches. 
                        If False, products with no matches will get their first match back (even if duplicate).
            
        Returns:
            Cleaned results dictionary with unique Product_B in rank 1 and recalculated ranks
        """
        mode_text = "STRICT" if strict_mode else "FLEXIBLE (allow duplicates for unmatched)"
        logging.info(f"\n🧹 STEP 9: Cleaning duplicate Product_B matches (rank-based) - Mode: {mode_text}")
        
        iteration = 0
        total_removed = 0
        
        # Track original first matches for products that will lose all matches
        # Format: {product_a_id: original_first_match}
        original_first_matches = {}
        
        # Store original first matches before any removal
        for product_a_id, data in results.items():
            if product_a_id == '_metadata':
                continue
            
            similar_products_b = data.get('similar_products_b', [])
            if similar_products_b and len(similar_products_b) > 0:
                # Deep copy the first match to preserve it
                original_first_matches[product_a_id] = similar_products_b[0].copy()
        
        while True:
            iteration += 1
            logging.info(f"\n🔄 Iteration {iteration}: Checking rank 1 for duplicates...")
            
            # Step 1: Find all rank 1 matches (first match for each product_a)
            rank1_matches = {}  # product_b_id -> list of (product_a_id, match_data, combined_score)
            
            for product_a_id, data in results.items():
                if product_a_id == '_metadata':
                    continue
                
                similar_products_b = data.get('similar_products_b', [])
                
                # Check if there's a rank 1 match (first in the sorted list)
                if similar_products_b and len(similar_products_b) > 0:
                    rank1_match = similar_products_b[0]
                    product_b_id = rank1_match.get('id')
                    combined_score = rank1_match.get('combined_score', 0.0)
                    
                    if product_b_id:
                        if product_b_id not in rank1_matches:
                            rank1_matches[product_b_id] = []
                        
                        rank1_matches[product_b_id].append({
                            'product_a_id': product_a_id,
                            'match_data': rank1_match,
                            'combined_score': combined_score
                        })
            
            # Step 2: Find duplicates in rank 1
            duplicates = {
                b_id: matches 
                for b_id, matches in rank1_matches.items() 
                if len(matches) > 1
            }
            
            if not duplicates:
                logging.info(f"✅ No duplicates found in rank 1. Cleaning complete after {iteration} iterations.")
                logging.info(f"   Total matches removed: {total_removed}")
                break
            
            logging.info(f"   🔍 Found {len(duplicates)} Product_B with duplicates in rank 1")
            
            iteration_removed = 0
            
            # Step 3: For each duplicate, keep highest score, remove others
            for product_b_id, matches in duplicates.items():
                # Sort by combined_score descending
                matches.sort(key=lambda x: x['combined_score'], reverse=True)
                
                best_match = matches[0]
                removed_matches = matches[1:]
                
                logging.info(f"      Product_B {product_b_id}:")
                logging.info(f"         ✅ Keeping: Product_A {best_match['product_a_id']} (score: {best_match['combined_score']:.3f})")
                
                # Remove the rank 1 match from other product_a's (this promotes rank 2 to rank 1)
                for removed in removed_matches:
                    product_a_id = removed['product_a_id']
                    
                    if product_a_id in results:
                        similar_products_b = results[product_a_id].get('similar_products_b', [])
                        
                        # Verify that rank 1 is the product_b we want to remove
                        if similar_products_b and len(similar_products_b) > 0 and similar_products_b[0].get('id') == product_b_id:
                            # Check if this will leave Product_A with no matches
                            remaining_matches = len(similar_products_b) - 1
                            
                            if remaining_matches > 0:
                                logging.info(f"         ❌ Removing from: Product_A {product_a_id} (score: {removed['combined_score']:.3f}) - {remaining_matches} match(es) remaining")
                            else:
                                logging.info(f"         ⚠️  Removing from: Product_A {product_a_id} (score: {removed['combined_score']:.3f}) - NO MATCHES LEFT!")
                            
                            # Remove rank 1 (modifies list in-place)
                            del similar_products_b[0]
                            iteration_removed += 1
                            total_removed += 1
                        else:
                            logging.warning(f"         ⚠️  Cannot remove from Product_A {product_a_id}: rank 1 mismatch or empty list")
            
            logging.info(f"   ✅ Removed {iteration_removed} rank 1 matches in iteration {iteration}")
        
        # Count products with no matches after cleaning
        products_with_no_matches = sum(
            1 for product_a_id, data in results.items()
            if product_a_id != '_metadata' and not data.get('similar_products_b')
        )
        
        if products_with_no_matches > 0:
            logging.warning(f"⚠️  {products_with_no_matches} Product_A entries have NO MATCHES after duplicate removal!")
            logging.warning(f"   This happens when a product's only match(es) were duplicates with lower scores.")
            
            # If in flexible mode, restore original first match for products with no matches
            if not strict_mode:
                logging.info(f"\n🔄 FLEXIBLE MODE: Restoring first matches for {products_with_no_matches} unmatched products...")
                
                restored_count = 0
                for product_a_id, data in results.items():
                    if product_a_id == '_metadata':
                        continue
                    
                    similar_products_b = data.get('similar_products_b', [])
                    
                    # If no matches and we have the original first match
                    if not similar_products_b and product_a_id in original_first_matches:
                        original_match = original_first_matches[product_a_id]
                        product_b_id = original_match.get('id')
                        combined_score = original_match.get('combined_score', 0.0)
                        
                        # Restore the match
                        results[product_a_id]['similar_products_b'] = [original_match]
                        restored_count += 1
                        
                        logging.info(f"   ✅ Restored match for Product_A {product_a_id}: Product_B {product_b_id} (score: {combined_score:.3f})")
                
                logging.info(f"✅ Restored {restored_count} matches in flexible mode")
                logging.info(f"   All products now have at least one match assigned")
        
        # Final step: Recalculate ranks for all products
        logging.info("\n🔢 Recalculating final ranks for all products...")
        
        recalculated_count = 0
        for product_a_id, data in results.items():
            if product_a_id == '_metadata':
                continue
            
            similar_products_b = data.get('similar_products_b', [])
            
            if similar_products_b:
                for rank, match in enumerate(similar_products_b, start=1):
                    match['rank'] = rank
                recalculated_count += 1
        
        logging.info(f"✅ Recalculated ranks for {recalculated_count} products")
        logging.info("   All Product_B in rank 1 are now unique\n")
        
        return results
