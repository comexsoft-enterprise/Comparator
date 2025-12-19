import logging
import json
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List
from collections import defaultdict
import psycopg2
from psycopg2.extras import RealDictCursor
from src.models.category_analysis.category_analysis import CategoryAnalysis
from src.models.embedding_analysis.embedding_analysis import SimilarityAnalysis
from src.models.postgresql_analysis.euclidean_distance_calculator import process_product_euclidean
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
            score_threshold: float = 0.15
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
                score_threshold: Maximum graph score difference from best match to include
            """

            total = len(products_a)
            logging.info(f"\n🚀 Starting parallel processing of {total} products with {max_workers} workers...")
            
            results = {}
            completed_count = 0
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_product = {
                    executor.submit(
                        self.category_analysis.find_matches_by_category,
                        product_a,
                        store_a,
                        store_b,
                        min_matches,
                        weights,
                        idx,
                        total,
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
            score_threshold: float = 0.4,
            top_n: int = 3,
            min_graph_score: float = 0.3,
            min_name_similarity: float = 0.4,
            min_description_similarity: float = 0.4,
            min_euclidean_similarity: float = 0.0
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
                score_threshold: Maximum graph score difference from best match to include
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
            # STEP 2: CHECK FOR EXISTING PERFECT MATCHES (ONLY FOR LOADED PRODUCTS)
            # ============================================================
            logging.info("\n🔍 STEP 2: Checking PostgreSQL for existing perfect matches")
            
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
            # STEP 3: FILTER OUT PRODUCTS WITH PERFECT MATCHES
            # ============================================================
            products_to_process = []
            skipped_count = 0
            
            for product in products_a:
                product_siid = product.get('siid')
                if product_siid and product_siid in existing_perfect_matches:
                    skipped_count += 1
                else:
                    products_to_process.append(product)
            
            if skipped_count > 0:
                logging.info(f"⏭️  Skipping {skipped_count} products with existing perfect matches")
                logging.info(f"🔄 Processing {len(products_to_process)} remaining products through model")
            
            # If all products already have perfect matches, return them directly
            if not products_to_process:
                logging.info(f"✅ All {len(products_a)} products already have perfect matches! Skipping model execution.")
                return existing_perfect_matches
            
            # ============================================================
            # STEP 4: GRAPH-BASED SIMILARITY (CATEGORY ANALYSIS)
            # ============================================================
            logging.info(f"\n🔍 STEP 4: Graph-based similarity analysis for {len(products_to_process)} products")
            logging.info(f"   Finding similar products using graph structure...")
            
            results = self._run_category_analysis(
                products_a=products_to_process,  # Use filtered list
                store_a=store_a,
                store_b=store_b,
                min_matches=min_matches,
                graph_weights=graph_weights_food,
                graph_weights_non_food_super=graph_weights_non_food_super,
                graph_weights_non_food_elec=graph_weights_non_food_elec,
                max_workers=max_workers,
                not_found_ids=not_found_ids,
                score_threshold=score_threshold,
                min_graph_score=min_graph_score
            )
            # ============================================================
            # STEP 3: EMBEDDING-BASED SIMILARITY
            # ============================================================
            logging.info(f"\n🤖 STEP 3: Embedding-based similarity analysis")
            logging.info(f"   Calculating text similarities using {distance_metric} distance...")
            results = self._run_embedding_analysis(
                results=results,
                combined_weights_food=combined_weights_food,
                combined_weights_non_food_super=combined_weights_non_food_super,
                combined_weights_non_food_elec=combined_weights_non_food_elec,
                distance_metric=distance_metric,
                max_workers=max_workers,
                max_embedding_workers=max_embedding_workers,
                store_a=store_a,
                min_name_similarity=min_name_similarity,
                min_description_similarity=min_description_similarity
            )
            # ============================================================
            # STEP 4: EUCLIDEAN DISTANCE CALCULATION
            # ============================================================
            logging.info(f"\n📐 STEP 4: Euclidean distance calculation")
            logging.info(f"   Calculating numerical feature similarities...")
            
            results = self._run_euclidean_distance(
                results=results,
                max_workers=max_workers,
                min_euclidean_similarity=min_euclidean_similarity
            )
            # ============================================================
            # STEP 5: COMBINE ALL SCORES
            # ============================================================
            logging.info(f"\n🔢 STEP 5: Combining all similarity scores")
            logging.info(f"   Final score calculation with all 4 components...")
           
            results = self._combine_all_scores(
                results=results,
                combined_weights_food=combined_weights_food,
                combined_weights_non_food_super=combined_weights_non_food_super,
                combined_weights_non_food_elec=combined_weights_non_food_elec,
                store_a=store_a,
                top_n=top_n
            )
            
            # ============================================================
            # STEP 5.5: MERGE WITH EXISTING PERFECT MATCHES
            # ============================================================
            if existing_perfect_matches:
                logging.info(f"\n🔗 STEP 5.5: Merging results with existing perfect matches")
                logging.info(f"   Adding {len(existing_perfect_matches)} perfect matches to results...")
                
                # Merge perfect matches into results
                for product_siid, match_data in existing_perfect_matches.items():
                    results[product_siid] = match_data
                
                logging.info(f"✅ Total results after merge: {len(results) - 1} products (excluding metadata)")
            
            # ============================================================
            # STEP 6: CLEAN DUPLICATE PRODUCT_B MATCHES
            # ============================================================
            results = self._clean_duplicate_product_b(results)
            
            # ============================================================
            # STEP 7: ADD METADATA
            # ============================================================
            results['_metadata'] = {
                'store_a': store_a,
                'store_b': store_b,
                'min_matches': min_matches,
                'weights_food': weights_food,
                'weights_non_food_super': weights_non_food_super,
                'weights_non_food_elec': weights_non_food_elec,
                'distance_metric': distance_metric,
                'score_threshold': score_threshold,
                'max_workers': max_workers,
                'max_embedding_workers': max_embedding_workers,
                'id_obtention_method': id_obtention_method,
                'external_ids_path': external_ids_path if id_obtention_method == 'external_file' else None,
                'id_lists_count': len(id_lists) if id_lists else 0,
                'top_n': top_n,
                'existing_perfect_matches_count': len(existing_perfect_matches),
                'new_matches_from_model': len(results) - len(existing_perfect_matches) - 1  # -1 for metadata
            }
            logging.info("\n" + "="*70)
            logging.info("✅ ANALYSIS COMPLETE!")
            logging.info(f"   Perfect matches from PostgreSQL: {len(existing_perfect_matches)}")
            logging.info(f"   New matches from model: {len(results) - len(existing_perfect_matches) - 1}")
            logging.info(f"   Total products: {len(results) - 1}")
            logging.info(f"   Products with matches: {sum(1 for k, r in results.items() if k != '_metadata' and r.get('similar_products_b'))}")
            logging.info("="*70 + "\n")
            
            # ============================================================
            # STEP 8: EXPORT AND PERSIST RESULTS (WITH CLEANED DATA)
            # ============================================================
            
            # Insert to PostgreSQL for validation (with cleaned data)
            insert_to_validate(results)
            
            return results
    
    def _run_category_analysis(
        self,
        products_a: List[Dict[str, Any]],
        store_a: str,
        store_b: str,
        min_matches: int,
        graph_weights: Dict[str, float],
        graph_weights_non_food_super: Dict[str, float],
        graph_weights_non_food_elec: Dict[str, float],
        max_workers: int,
        not_found_ids: List[str],
        score_threshold: float,
        min_graph_score: float = 0.8
    ) -> Dict[str, Dict[str, Any]]:
        """
        Run category-based (graph) analysis to find similar products.
        
        Uses CategoryAnalysis to find products in store B that share graph neighbors
        with products from store A. Automatically selects appropriate weights based on
        product's Internal_Type and Internal_Category (food vs non-food supermarket vs electronics).
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
                
                future = executor.submit(
                    self.category_analysis.find_matches_by_category,
                    product_a,
                    store_a,
                    store_b,
                    min_matches,
                    selected_weights,
                    idx,
                    total,
                    score_threshold,
                    min_graph_score
                )
                future_to_product[future] = product_a
            
            # Collect results as they complete
            for future in as_completed(future_to_product):
                try:
                    product_a_id, product_a, similar_products_b = future.result()
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
                    product_a = future_to_product[future]
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
        min_description_similarity: float = 0.4
    ) -> Dict[str, Dict[str, Any]]:
        """
        Run embedding-based similarity analysis and combine with graph scores.
        
        Uses SimilarityAnalysis to:
        1. Preload ALL embeddings in one query (ultra-fast)
        2. Calculate semantic similarity in parallel
        3. Combine with graph scores using configured weights
        4. Sort results by combined score
        
        Args:
            min_name_similarity: Minimum name embedding similarity score (0-1)
            min_description_similarity: Minimum description embedding similarity score (0-1)
        """
        # Process embeddings in parallel for all products A
        total_products_with_matches = sum(1 for data in results.values() if data.get('similar_products_b'))
        
        if total_products_with_matches == 0:
            logging.info("⚠️ No products with matches found, skipping embedding analysis")
            return results
        
        # OPTIMIZATION: Preload ALL embeddings in a single query
        embedding_cache = self.similarity_analysis.preload_embeddings_for_results(results)
        
        logging.info(f"🚀 Processing {total_products_with_matches} products with {max_embedding_workers} workers...")
        
        embedding_completed = 0
        
        with ThreadPoolExecutor(max_workers=max_embedding_workers) as executor:
            # Submit all embedding tasks with pre-loaded cache
            future_to_product_id = {
                executor.submit(
                    self.similarity_analysis.process_embeddings_for_product,
                    product_a_id,
                    data['product_a'],
                    data['similar_products_b'],
                    distance_metric,
                    embedding_cache,  # Pass pre-loaded cache
                    min_name_similarity,
                    min_description_similarity
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
                        logging.info(f"📊 Progress: {embedding_completed}/{total_products_with_matches} ({pct:.1f}%) embeddings processed")
                        
                except Exception as e:
                    product_a_id = future_to_product_id[future]
                    logging.error(f"❌ Error calculating embeddings for product {product_a_id}: {e}")
        
        logging.info(f"✅ Embedding similarity calculated using {distance_metric}")
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
            
            # Keep only top N products
            results[product_a_id]['similar_products_b'] = similar_products_b[:top_n]
            
            processed += 1
        
        elapsed = time.time() - start_time
        logging.info(f"✅ All scores combined and sorted in {elapsed:.1f}s")

        return results
    
    def _clean_duplicate_product_b(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean duplicate Product_B matches across all Product_A results.
        For each Product_B that appears multiple times, keep only the match with highest Combined_Score.
        Recalculate ranks after removing duplicates.
        
        Args:
            results: Dictionary with product_a_id as keys and similarity data as values
            
        Returns:
            Cleaned results dictionary with unique Product_B matches and recalculated ranks
        """
        logging.info("\n🧹 STEP 6: Cleaning duplicate Product_B matches")
        
        # Build mapping of Product_B_ID -> list of (product_a_id, match_data, index)
        product_b_to_matches = defaultdict(list)
        
        for product_a_id, data in results.items():
            if product_a_id == '_metadata':
                continue
            
            similar_products_b = data.get('similar_products_b', [])
            
            for idx, match in enumerate(similar_products_b):
                product_b_id = match.get('id')
                if product_b_id:
                    combined_score = match.get('combined_score', 0.0)
                    product_b_to_matches[product_b_id].append({
                        'product_a_id': product_a_id,
                        'match_data': match,
                        'index': idx,
                        'combined_score': combined_score
                    })
        
        # Find duplicates (Product_B appearing in multiple Product_A)
        duplicates_found = {
            b_id: matches 
            for b_id, matches in product_b_to_matches.items() 
            if len(matches) > 1
        }
        
        if not duplicates_found:
            logging.info("✅ No duplicate Product_B matches found")
            return results
        
        logging.info(f"🔍 Found {len(duplicates_found)} Product_B with duplicates")
        
        # Track which matches to remove
        matches_to_remove = []
        
        # For each duplicate Product_B, keep only the one with highest combined_score
        for product_b_id, matches in duplicates_found.items():
            # Sort by combined_score descending
            matches.sort(key=lambda x: x['combined_score'], reverse=True)
            
            # Keep the first (highest score), remove the rest
            best_match = matches[0]
            removed_matches = matches[1:]
            
            logging.info(f"   Product_B {product_b_id}:")
            logging.info(f"      ✅ Keeping: Product_A {best_match['product_a_id']} (score: {best_match['combined_score']:.3f})")
            
            for removed in removed_matches:
                logging.info(f"      ❌ Removing: Product_A {removed['product_a_id']} (score: {removed['combined_score']:.3f})")
                matches_to_remove.append({
                    'product_a_id': removed['product_a_id'],
                    'product_b_id': product_b_id
                })
        
        # Remove duplicate matches from results
        removed_count = 0
        for removal in matches_to_remove:
            product_a_id = removal['product_a_id']
            product_b_id = removal['product_b_id']
            
            if product_a_id in results:
                similar_products_b = results[product_a_id].get('similar_products_b', [])
                # Remove the match with this product_b_id
                original_len = len(similar_products_b)
                similar_products_b = [m for m in similar_products_b if m.get('id') != product_b_id]
                results[product_a_id]['similar_products_b'] = similar_products_b
                
                if len(similar_products_b) < original_len:
                    removed_count += 1
        
        logging.info(f"✅ Removed {removed_count} duplicate matches")
        
        # Recalculate ranks for each Product_A
        logging.info("🔢 Recalculating ranks for each Product_A...")
        
        recalculated_count = 0
        for product_a_id, data in results.items():
            if product_a_id == '_metadata':
                continue
            
            similar_products_b = data.get('similar_products_b', [])
            
            if similar_products_b:
                # Ranks are already sorted by combined_score (from previous step)
                # Just need to renumber them sequentially
                for rank, match in enumerate(similar_products_b, start=1):
                    match['rank'] = rank
                
                recalculated_count += 1
        
        logging.info(f"✅ Recalculated ranks for {recalculated_count} products")
        logging.info("   All Product_B are now unique across all matches\n")
        
        return results
