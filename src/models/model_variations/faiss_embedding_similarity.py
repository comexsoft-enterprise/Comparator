"""
FAISS-based Product Similarity Finder (Embedding Only)

This script identifies similar products across different store databases.
It creates a FAISS index from 'Product_B' (target store) embeddings and queries it
using 'Product_A' (source store) embeddings. It supports both Name and Description embeddings.

Usage:
    python faiss_embedding_similarity.py

Requirements:
    - Neo4j database running locally (neo4j://localhost:7687)
    - FAISS library (faiss-cpu or faiss-gpu)
    - Environment variables configured in .env file

Architecture:
    This script is a simplified version of the main model, focusing only on embedding similarity using FAISS.

Author: Your Team
Date: 2024
Refactored: January 2026
"""

import logging
import sys
import argparse
import csv
import numpy as np
import faiss
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

# Add project root to path for imports
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.connectors.neo4j_connector import Neo4jConnector
from config.settings import NEO4J_CONFIG

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

NEO4J_URI = NEO4J_CONFIG["uri"]
NEO4J_USERNAME = NEO4J_CONFIG["user"]
NEO4J_PASSWORD = NEO4J_CONFIG["password"]
NEO4J_DATABASE = NEO4J_CONFIG.get("database", "neo4j")


# ============================================================
# Pydantic Models for Parameters
# ============================================================

class EmbeddingWeights(BaseModel):
    """Weights for combining different embedding similarities."""
    name: float = Field(default=0.5, ge=0, le=1, description="Weight for name embedding similarity")
    description: float = Field(default=0.5, ge=0, le=1, description="Weight for description embedding similarity")


class ModelParameters(BaseModel):
    """Parameters for the FAISS similarity model."""
    store_a: str = Field(..., description="Name of the source store")
    store_b: str = Field(..., description="Name of the target store")
    list_ids: List[str] = Field(default=[], description="List of product IDs (siids) to compare from store_a")
    top_n_results: int = Field(default=5, ge=1, description="Number of similar products to return per product")
    embedding_weights: Optional[EmbeddingWeights] = Field(default=EmbeddingWeights(), description="Weights for combining name and description similarities")
    strict_mode: bool = Field(default=True, description="If True, ensures target products are only matched to the best source candidate rank-1")
    remove_duplicates: bool = Field(default=True, description="If True, removes duplicate matches so one target product is assigned to only one source product (best match).")


class ProductData(BaseModel):
    """Product data structure."""
    id: str  # siid or product_hash
    product_name: str
    description: Optional[str] = None
    name_embedding: Optional[List[float]] = None
    description_embedding: Optional[List[float]] = None
    original_node: Optional[Dict] = None

    class Config:
        arbitrary_types_allowed = True


class FaissSimilarityEngine:
    """Engine for FAISS-based similarity search."""

    def __init__(self, use_gpu: bool = False):
        self.use_gpu = use_gpu
        self.name_index = None
        self.desc_index = None
        self.target_products: List[ProductData] = []
        self.target_ids_map: Dict[int, str] = {} # Map internal FAISS ID to Product ID

    def _create_index(self, dimension: int):
        """Creates a FAISS index."""
        index = faiss.IndexFlatIP(dimension)  # Inner Product (cosine similarity for normalized vectors)
        if self.use_gpu:
            try:
                res = faiss.StandardGpuResources()
                index = faiss.index_cpu_to_gpu(res, 0, index)
            except Exception as e:
                logger.warning(f"Failed to use GPU for FAISS: {e}. Fallback to CPU.")
        return index

    def build_indices(self, products: List[ProductData]):
        """Builds FAISS indices for name and description embeddings."""
        self.target_products = products
        self.target_ids_map = {i: p.id for i, p in enumerate(products)}

        # Filter valid embeddings
        valid_name_embeddings = []
        valid_desc_embeddings = []
        valid_name_indices = []
        valid_desc_indices = []

        for i, p in enumerate(products):
            if p.name_embedding:
                valid_name_embeddings.append(p.name_embedding)
                valid_name_indices.append(i)
            if p.description_embedding:
                valid_desc_embeddings.append(p.description_embedding)
                valid_desc_indices.append(i)

        # Build Name Index
        if valid_name_embeddings:
            embeddings_matrix = np.array(valid_name_embeddings, dtype='float32')
            dimension = embeddings_matrix.shape[1]
            faiss.normalize_L2(embeddings_matrix)  # Normalize for Cosine Similarity
            
            self.name_index = self._create_index(dimension)
            # Store ID mapping specifically for this index if needed, 
            # but usually we map back using the original list if indices align.
            # Here we are adding only valid ones so indices shift.
            # Strategy: Use IndexIDMap if we want to keep original IDs, or just keep track.
            # Simplified: Let's assume we search and map back using a separate list for index-to-product mapping.
            
            # Re-approach: Use IndexIDMap to map internal FAISS ID (0..N) to index in self.target_products
            # But IndexFlatIP doesn't support add_with_ids directly unless wrapped.
            # For simplicity in this script, we'll keep it simple: 
            # We will use the subset lists for building, and we need to map the result index back to original product.
            
            self.name_products_map = {k: v for k, v in enumerate(valid_name_indices)}
            self.name_index.add(embeddings_matrix)
            logger.info(f"Built Name FAISS Index with {self.name_index.ntotal} vectors.")
        
        # Build Description Index
        if valid_desc_embeddings:
            embeddings_matrix = np.array(valid_desc_embeddings, dtype='float32')
            dimension = embeddings_matrix.shape[1]
            faiss.normalize_L2(embeddings_matrix)
            
            self.desc_index = self._create_index(dimension)
            self.desc_products_map = {k: v for k, v in enumerate(valid_desc_indices)}
            self.desc_index.add(embeddings_matrix)
            logger.info(f"Built Description FAISS Index with {self.desc_index.ntotal} vectors.")

    def search(self, query_product: ProductData, k: int = 5, weights: EmbeddingWeights = EmbeddingWeights()) -> List[Tuple[str, float]]:
        """
        Search for similar products using combined weighted scores.
        This is a bit complex with FAISS because we have two separate indices.
        
        Strategy:
        1. Search top K*factor in Name Index
        2. Search top K*factor in Description Index
        3. Combine results based on weights
        4. Return top K
        """
        
        scores: Dict[str, float] = {} # product_id -> weighted_score
        
        # Factor to search deeper to allow intersection
        search_k = k * 10 
        
        # --- Name Search ---
        if self.name_index and query_product.name_embedding:
            query_vec = np.array([query_product.name_embedding], dtype='float32')
            faiss.normalize_L2(query_vec)
            D, I = self.name_index.search(query_vec, search_k)
            
            for rank, idx in enumerate(I[0]):
                if idx == -1: continue
                # Map back to original product index
                original_idx = self.name_products_map[idx]
                target_p_id = self.target_products[original_idx].id
                score = float(D[0][rank]) # Cosine similarity
                
                # Initialize or add
                if target_p_id not in scores:
                    scores[target_p_id] = 0.0
                scores[target_p_id] += score * weights.name

        # --- Description Search ---
        if self.desc_index and query_product.description_embedding:
            query_vec = np.array([query_product.description_embedding], dtype='float32')
            faiss.normalize_L2(query_vec)
            D, I = self.desc_index.search(query_vec, search_k)
            
            for rank, idx in enumerate(I[0]):
                if idx == -1: continue
                original_idx = self.desc_products_map[idx]
                target_p_id = self.target_products[original_idx].id
                score = float(D[0][rank])
                
                if target_p_id not in scores:
                    scores[target_p_id] = 0.0
                scores[target_p_id] += score * weights.description

        # --- Normalize Scores ---
        # If a product was found in only one index (e.g. description missing), 
        # the score might be lower than it should be if we strictly Average.
        # But here we are summing weighted scores.
        # If weights sum to 1.0, and we have perfect match (1.0) in both, result is 1.0.
        # If we only matched Name (0.5 weight) with 1.0 sim, score is 0.5. This seems correct for penalizing missing data.
        
        # Sort and return top K
        sorted_products = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return sorted_products[:k]


def fetch_products_from_neo4j(connector: Neo4jConnector, store_name: str, embedding_model: SentenceTransformer, product_ids: List[str] = None) -> List[ProductData]:
    """Fetches products from Neo4j and generates embeddings using SentenceTransformer."""
    
    # We need to adapt the query depending on whether we have a list of IDs or not
    where_clause = ""
    # Explicitly exclude products with empty names to avoid unnecessary processing
    params = {"store_name": store_name}
    
    if product_ids:
        # Accept product identifiers as either siid or product_hash in the list
        where_clause = "AND (p.siid IN $product_ids OR p.product_hash IN $product_ids)"
        params["product_ids"] = product_ids

    # Query to fetch name and description (TEXT ONLY)
    # Use a relationship-agnostic pattern so this works regardless of whether the repo used
    # `SELLS`, `SOLD_IN`, `SOLD_AT` or other relationship names between Product and Store.
    query = f"""
    MATCH (p:Product)-[r]-(s:Store)
    WHERE s.name = $store_name {where_clause}
    RETURN p.siid as id, 
        p.product_name as name, 
        p.description as description
    """
    
    logger.info(f"Fetching products for store '{store_name}'...")
    try:
        results = connector.execute_query(query, params)
        products = []
        
        # 1. Load basic data
        for record in results:
            products.append(ProductData(
                id=record["id"],
                product_name=record["name"] if record["name"] else "",
                description=record["description"] if record["description"] else "",
                name_embedding=None,
                description_embedding=None
            ))
        
        logger.info(f"Fetched {len(products)} products from {store_name}. Generating embeddings with all-MiniLM-L6-v2...")
        
        if not products:
            return []

        # 2. Prepare text lists
        names = [p.product_name for p in products]
        descriptions = [p.description for p in products]
        
        # 3. Generate Embeddings (Batch processing handled by sentence-transformers)
        # Check for CUDA availability for faster processing
        device = 'cuda' if faiss.get_num_gpus() > 0 else 'cpu'
        logger.info(f"Using device: {device} for embedding generation")
        
        # Encode Names
        name_embeddings = embedding_model.encode(names, batch_size=64, show_progress_bar=True, convert_to_numpy=True, device=device)
        
        # Encode Descriptions
        # Note: Some descriptions might be empty, the model handles empty strings (usually resulting in a vector)
        # but semantically we might want to ignore them in search if empty.
        # We will generate them regardless to keep indices aligned.
        desc_embeddings = embedding_model.encode(descriptions, batch_size=64, show_progress_bar=True, convert_to_numpy=True, device=device)

        # 4. Assign embeddings back to product objects
        for i, p in enumerate(products):
            p.name_embedding = name_embeddings[i].tolist()
            # Only assign description embedding if description exists, otherwise keep None or deal with it in search
            if p.description.strip():
                p.description_embedding = desc_embeddings[i].tolist()
            else:
                p.description_embedding = None

        logger.info(f"Embedding generation complete for {store_name}.")
        return products

    except Exception as e:
        logger.error(f"Error fetching products or generating embeddings: {e}")
        return []


def clean_duplicate_matches(results: Dict[str, Any], strict_mode: bool = True) -> Dict[str, Any]:
    """
    Clean duplicate Product_B matches across all Product_A results using rank-based logic.
    Ensure that rank 1 matches have unique Product_B IDs.
    
    Args:
        results: Dictionary with product_a_id as keys and results data as values
        strict_mode: If True, remove duplicates even if it leaves a product with no matches.
                        If False, restore original first match if product is left with no matches.
    """
    mode_text = "STRICT" if strict_mode else "FLEXIBLE"
    logger.info(f"Cleaning duplicate matches (rank-based) - Mode: {mode_text}")
    
    original_first_matches = {}
    
    # Store original first matches
    for src_id, data in results.items():
        matches = data.get('matches', [])
        if matches:
            original_first_matches[src_id] = matches[0].copy()

    iteration = 0
    total_removed = 0
    
    while True:
        iteration += 1
        # Step 1: Find all rank 1 matches
        rank1_matches = {} # target_id -> list of (src_id, match_data, score)

        for src_id, data in results.items():
            matches = data.get('matches', [])
            if matches:
                rank1_match = matches[0]
                # In faiss script, target id is 'siid'
                target_id = rank1_match.get('siid')
                score = rank1_match.get('score', 0.0)

                if target_id:
                    if target_id not in rank1_matches:
                        rank1_matches[target_id] = []
                    
                    rank1_matches[target_id].append({
                        'src_id': src_id,
                        'match_data': rank1_match,
                        'score': score
                    })

        # Step 2: Find duplicates
        duplicates = {t_id: m for t_id, m in rank1_matches.items() if len(m) > 1}

        if not duplicates:
            logger.info(f"No duplicates found in rank 1. Cleaning complete after {iteration} iterations.")
            break
        
        logger.info(f"Found {len(duplicates)} duplicates in rank 1.")
        iteration_removed = 0
        
        # Step 3: Resolve duplicates
        for target_id, matches_list in duplicates.items():
            # Sort by score descending
            matches_list.sort(key=lambda x: x['score'], reverse=True)

            # Keep best match, remove others
            removed_matches = matches_list[1:]

            # Remove rank 1 from others (promoting rank 2)
            for removed in removed_matches:
                src_id = removed['src_id']
                if src_id in results:
                    src_matches = results[src_id]['matches']
                    # Verify rank 1 is still the one we want to remove
                    if src_matches and src_matches[0].get('siid') == target_id:
                        del src_matches[0]
                        iteration_removed += 1
                        total_removed += 1
        
        if iteration_removed == 0:
             break # Avoid infinite loop if no changes

    # Check for empty matches
    products_with_no_matches = sum(1 for src_id, data in results.items() if not data.get('matches'))
    
    if products_with_no_matches > 0:
        logger.warning(f"{products_with_no_matches} products have NO MATCHES after duplicate cleaning.")
        
        if not strict_mode:
            logger.info("FLEXIBLE MODE: Restoring first matches...")
            restored_count = 0
            for src_id, data in results.items():
                # If no matches and we have the original first match
                if not data.get('matches') and src_id in original_first_matches:
                    data['matches'] = [original_first_matches[src_id]]
                    restored_count += 1
            logger.info(f"Restored {restored_count} matches.")

    return results


def run_faiss_matching(params: ModelParameters):
    """Main function to run the FAISS matching process."""
    
    connector = Neo4jConnector()
    if not connector.connect_to_neo4j():
        logger.error("Could not connect to Neo4j. Exiting.")
        return {}
        
    try:
        # 0. Initialize Embedding Model
        logger.info("Loading SentenceTransformer model 'all-MiniLM-L6-v2'...")
        try:
            embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        except Exception as e:
            logger.error(f"Failed to load SentenceTransformer model: {e}")
            return {}

        # 1. Fetch Source Products (Store A)
        source_products = fetch_products_from_neo4j(connector, params.store_a, embedding_model, params.list_ids)
        if not source_products:
            logger.warning(f"No products found for Store A: {params.store_a}")
            return {}

        # 2. Fetch Target Products (Store B)
        # For target, we generally want ALL products to search against
        target_products = fetch_products_from_neo4j(connector, params.store_b, embedding_model)
        if not target_products:
            logger.warning(f"No products found for Store B: {params.store_b}")
            return {}

        # 3. Build FAISS Index for Store B
        faiss_engine = FaissSimilarityEngine(use_gpu=False)
        faiss_engine.build_indices(target_products)
        
        # Create lookup map for target products
        target_product_map = {p.id: p for p in target_products}

        # 4. Search for Similar Products
        results = {}
        logger.info("Running similarity search...")
        for src_prod in source_products:
            matches = faiss_engine.search(
                src_prod, 
                k=params.top_n_results, 
                weights=params.embedding_weights
            )
            
            # Format results
            match_list = []
            for pid, score in matches:
                target_p = target_product_map.get(pid)
                match_list.append({
                    "siid": pid,
                    "product_name": target_p.product_name if target_p else "Unknown",
                    "description": target_p.description if target_p else "",
                    "score": round(score, 4)
                })
            
            results[src_prod.id] = {
                "source_name": src_prod.product_name,
                "source_description": src_prod.description,
                "matches": match_list
            }
            
        logger.info(f"Completed FAISS matching for {len(results)} products.")
        
        # Apply duplicate cleaning
        if params.remove_duplicates:
            results = clean_duplicate_matches(results, strict_mode=params.strict_mode)
        
        return results

    finally:
        connector.close_connection()


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="FAISS-based Product Similarity Finder")
    parser.add_argument("--store_a", type=str, required=True, help="Name of the source store")
    parser.add_argument("--store_b", type=str, required=True, help="Name of the target store")
    parser.add_argument("--list_ids", nargs='+', default=[], help="List of product IDs (siids) to compare from store_a. If empty, all products from store_a will be used.")
    parser.add_argument("--ids_file", type=str, help="Path to a text file containing product IDs (one per line). Defaults to 'products_to_find_match.txt' if not specified and file exists.")
    parser.add_argument("--top_n", type=int, default=5, help="Number of similar products to return per product")
    parser.add_argument("--weight_name", type=float, default=0.5, help="Weight for name embedding similarity")
    parser.add_argument("--weight_desc", type=float, default=0.5, help="Weight for description embedding similarity")
    parser.add_argument("--output_csv", type=str, default=None, help="Path to output CSV file. If not provided, uses similarity_matches_{store_a}_{store_b}.csv")
    parser.add_argument("--flexible", action="store_true", help="If set, allows duplicates for products that would otherwise have no matches (disables strict mode)")
    parser.add_argument("--keep_duplicates", action="store_true", help="If set, skips the duplicate removal process entirely. Default is to remove duplicates.")

    args = parser.parse_args()

    # Combine IDs from file and command line
    target_ids = []
    
    # Check if list_ids provides a file path
    if args.list_ids and len(args.list_ids) == 1 and Path(args.list_ids[0]).is_file():
        logger.info(f"Reading IDs from file: {args.list_ids[0]}")
        try:
            with open(args.list_ids[0], 'r') as f:
                target_ids = [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.error(f"Failed to read IDs file: {e}")
            sys.exit(1)
    else:
        target_ids = list(args.list_ids)

    # Add explicitly provided file or default 'products_to_find_match.txt' if available
    file_to_read = args.ids_file
    
    # If no file specified, and no IDs in list, check for default file
    if not file_to_read and not target_ids:
        default_path = Path("products_to_find_match.txt")
        if default_path.exists():
            file_to_read = str(default_path)
            logger.info(f"No IDs provided explicitly. Using default file: {file_to_read}")

    if file_to_read:
        try:
            with open(file_to_read, 'r') as f:
                file_ids = [line.strip() for line in f if line.strip()]
                target_ids.extend(file_ids)
            logger.info(f"Loaded {len(file_ids)} IDs from {file_to_read}")
        except Exception as e:
            logger.error(f"Failed to read IDs file: {e}")
            sys.exit(1)

    # Remove duplicates if any
    if target_ids:
        target_ids = list(dict.fromkeys(target_ids))
        logger.info(f"Total unique IDs to process: {len(target_ids)}")

    # Create parameters object
    params = ModelParameters(
        store_a=args.store_a,
        store_b=args.store_b,
        list_ids=target_ids,
        top_n_results=args.top_n,
        embedding_weights=EmbeddingWeights(name=args.weight_name, description=args.weight_desc),
        strict_mode=not args.flexible,
        remove_duplicates=not args.keep_duplicates
    )
    
    matches = run_faiss_matching(params)
    
    # Save to CSV
    if matches:
        # Determine output path: use provided one or build from store names
        out_path = args.output_csv
        if not out_path:
            # sanitize store names to safe filename parts
            def _clean_name(s: str) -> str:
                return "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in s).replace(' ', '_')

            store_a_safe = _clean_name(args.store_a)
            store_b_safe = _clean_name(args.store_b)
            out_path = f"similarity_matches_{store_a_safe}_{store_b_safe}.csv"

        logger.info(f"Saving results to {out_path}...")
        try:
            with open(out_path, mode='w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['source_siid', 'source_name', 'source_description', 
                              'target_siid', 'target_name', 'target_description', 'score', 'rank']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=';', quoting=csv.QUOTE_MINIMAL)
                writer.writeheader()
                
                # Sort by source_siid to satisfy "organized by siid"
                sorted_siids = sorted(matches.keys())
                
                for source_siid in sorted_siids:
                    res = matches[source_siid]
                    matches_list = res.get('matches', [])

                    if not matches_list:
                        writer.writerow({
                            'source_siid': source_siid,
                            'source_name': res.get('source_name', ''),
                            'source_description': res.get('source_description', ''),
                            'target_siid': 'NO_MATCH',
                            'target_name': 'NO_MATCH',
                            'target_description': 'NO_MATCH',
                            'score': 0.0,
                            'rank': 0
                        })
                        continue

                    # Matches are already sorted by score (descending) from the search engine
                    for i, m in enumerate(matches_list):
                        writer.writerow({
                            'source_siid': source_siid,
                            'source_name': res.get('source_name', ''),
                            'source_description': res.get('source_description', ''),
                            'target_siid': m['siid'],
                            'target_name': m['product_name'],
                            'target_description': m['description'],
                            'score': m['score'],
                            'rank': i + 1
                        })
            logger.info("CSV save complete.")
        except Exception as e:
            logger.error(f"Failed to save CSV: {e}")
