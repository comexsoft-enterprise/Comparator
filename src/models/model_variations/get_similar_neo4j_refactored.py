"""
Cross-Store Product Similarity Finder (Neo4j + OpenAI Embeddings) - REFACTORED VERSION

This script identifies similar products across different store databases stored in Neo4j.
It combines:
- Graph-based similarity (shared attributes/neighbors in Neo4j)
- Semantic similarity (OpenAI embeddings for product names and descriptions)

Usage:
    python 3_get_similar_neo4j_refactored.py

Requirements:
    - Neo4j database running locally (neo4j://localhost:7687)
    - OpenAI API key for embeddings
    - Environment variables configured in .env file

Architecture:
    This script uses the refactored architecture:
    - CategoryAnalysis: Handles Neo4j product loading and graph-based similarity
    - SimilarityAnalysis: Handles embedding generation and semantic similarity
    - ModelCombinedSimilarity: Orchestrates both analyses
    - export_utils: Handles CSV/Excel exports

Author: Your Team
Date: 2024
Refactored: December 2024
"""

import logging
import sys
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field

# Add project root to path for imports (ai_consumer_goods directory)
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))


# Import refactored components
from src.connectors.neo4j_connector import Neo4jConnector
from src.models.category_analysis.category_analysis import CategoryAnalysis
from src.models.embedding_analysis.embedding_analysis import SimilarityAnalysis
from src.models.model_variations.model import ModelCombinedSimilarity

# Import export utilities
from src.model_exports.export_utils import (
    export_siid_pairs_csv,
    export_cross_store_results_to_csv,
    export_cross_store_results_to_excel
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


from config.settings import NEO4J_CONFIG, AZURE_OPENAI_CONFIG, PROJECT_ROOT

NEO4J_URI = NEO4J_CONFIG["uri"]
NEO4J_USERNAME = NEO4J_CONFIG["user"]
NEO4J_PASSWORD = NEO4J_CONFIG["password"]
NEO4J_DATABASE = NEO4J_CONFIG.get("database", "neo4j")

AZURE_OPENAI_API_KEY = AZURE_OPENAI_CONFIG["api_key"]
AZURE_OPENAI_ENDPOINT = AZURE_OPENAI_CONFIG["api_base"]
AZURE_OPENAI_API_VERSION = AZURE_OPENAI_CONFIG["api_version"]


# ============================================================
# Pydantic Models for Parameters
# ============================================================

class CombinedWeights(BaseModel):
    """Weights for combining different similarity methods."""
    graph: float = Field(ge=0, le=1, description="Weight for graph-based similarity")
    name: float = Field(ge=0, le=1, description="Weight for name embedding similarity")
    description: float = Field(ge=0, le=1, description="Weight for description embedding similarity")
    euclidean: float = Field(ge=0, le=1, description="Weight for euclidean distance similarity")


class FoodGraphWeights(BaseModel):
    """Graph-based weights for FOOD products."""
    brand: float = Field(default=0.300, ge=0, le=1)
    format: float = Field(default=0.150, ge=0, le=1)
    subcategory: float = Field(default=0.250, ge=0, le=1)
    first_ingredient: float = Field(default=0.300, ge=0, le=1)
    second_ingredient: float = Field(default=0.100, ge=0, le=1)
    unit_measure: float = Field(default=0.100, ge=0, le=1)
    allergen: float = Field(default=0.020, ge=0, le=1)
    country_origin: float = Field(default=0.050, ge=0, le=1)
    other_ingredient: float = Field(default=0.020, ge=0, le=1)
    quantity: float = Field(default=0.100, ge=0, le=1)


class NonFoodSuperGraphWeights(BaseModel):
    """Graph-based weights for NON-FOOD SUPERMARKET products."""
    brand: float = Field(default=0.100, ge=0, le=1)
    format: float = Field(default=0.200, ge=0, le=1)
    subcategory: float = Field(default=0.300, ge=0, le=1)
    first_component: float = Field(default=0.300, ge=0, le=1)
    second_component: float = Field(default=0.100, ge=0, le=1)
    unit_measure: float = Field(default=0.150, ge=0, le=1)
    country_origin: float = Field(default=0.100, ge=0, le=1)
    quantity: float = Field(default=0.150, ge=0, le=1)


class NonFoodElecGraphWeights(BaseModel):
    """Graph-based weights for NON-FOOD ELECTRONICS products."""
    brand: float = Field(default=0.400, ge=0, le=1)
    format: float = Field(default=0.150, ge=0, le=1)
    subcategory: float = Field(default=0.300, ge=0, le=1)
    first_component: float = Field(default=0.250, ge=0, le=1)
    second_component: float = Field(default=0.150, ge=0, le=1)
    unit_measure: float = Field(default=0.100, ge=0, le=1)
    country_origin: float = Field(default=0.050, ge=0, le=1)
    quantity: float = Field(default=0.100, ge=0, le=1)


class FoodWeights(BaseModel):
    """Complete weights configuration for FOOD products."""
    graph: FoodGraphWeights = Field(default_factory=FoodGraphWeights)
    combined: CombinedWeights = Field(
        default_factory=lambda: CombinedWeights(
            graph=0.10, name=0.40, description=0.40, euclidean=0.10
        )
    )


class NonFoodSuperWeights(BaseModel):
    """Complete weights configuration for NON-FOOD SUPERMARKET products."""
    graph: NonFoodSuperGraphWeights = Field(default_factory=NonFoodSuperGraphWeights)
    combined: CombinedWeights = Field(
        default_factory=lambda: CombinedWeights(
            graph=0.25, name=0.42, description=0.25, euclidean=0.08
        )
    )


class NonFoodElecWeights(BaseModel):
    """Complete weights configuration for NON-FOOD ELECTRONICS products."""
    graph: NonFoodElecGraphWeights = Field(default_factory=NonFoodElecGraphWeights)
    combined: CombinedWeights = Field(
        default_factory=lambda: CombinedWeights(
            graph=0.33, name=0.42, description=0.17, euclidean=0.08
        )
    )


class QualityThresholds(BaseModel):
    """Minimum quality thresholds for filtering results."""
    min_matches: int = Field(default=3, ge=1, description="Minimum number of shared nodes (Brand, Format, etc.) required in graph to consider a match")
    min_graph_score: float = Field(default=0.5, ge=0, description="Minimum graph-based similarity score")
    min_name_similarity: float = Field(default=0.5, ge=0, le=1, description="Minimum name embedding similarity")
    min_description_similarity: float = Field(default=0.5, ge=0, le=1, description="Minimum description embedding similarity")
    min_euclidean_similarity: float = Field(default=0.0, ge=0, le=1, description="Minimum euclidean distance similarity")
    cat_score_threshold: float = Field(default=0.5, ge=0, description="Maximum category graph score difference from best match to include")
    combined_score_threshold: float = Field(default=0.5, ge=0, description="Maximum combined graph score difference from best match to include")


class ModelParameters(BaseModel):
    """Main parameters for the cross-store similarity model."""
    store_a: str = Field(description="Source store name")
    store_b: str = Field(description="Target store name")
    list_ids: List[str] = Field(description="List of product IDs to analyze")
    top_n_results: int = Field(default=3, ge=1, description="Number of top similar products to return")
    
    food_weights: FoodWeights = Field(default_factory=FoodWeights)
    non_food_super_weights: NonFoodSuperWeights = Field(default_factory=NonFoodSuperWeights)
    non_food_elec_weights: NonFoodElecWeights = Field(default_factory=NonFoodElecWeights)
    
    quality_thresholds: QualityThresholds = Field(default_factory=QualityThresholds)
    avoid_duplicate_product_b: bool = Field(default=True, description="Whether to avoid duplicate product B matches")


# ============================================================
# Pretty Printing Utilities
# ============================================================
def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(title: str):
    """Print a formatted section."""
    print(f"\n📋 {title}")
    print("-" * 70)


# ============================================================
# Main Function
# ============================================================
def model(params: ModelParameters):
    """
    Main function to execute cross-store similarity analysis using refactored architecture.
    
    Args:
        params: ModelParameters object containing all configuration
            - store_a: Source store name
            - store_b: Target store name
            - list_ids: List of product IDs to analyze
            - top_n_results: Number of top similar products to return
            - food_weights: Weights configuration for FOOD products
            - non_food_super_weights: Weights configuration for NON-FOOD SUPERMARKET
            - non_food_elec_weights: Weights configuration for NON-FOOD ELECTRONICS
            - quality_thresholds: Minimum quality thresholds for filtering
    
    Returns:
        dict: Simplified results mapping {store_a_id: [store_b_id1, store_b_id2, ...]}
    """
    from dotenv import load_dotenv
    load_dotenv()

    print_header("NEO4J CROSS-STORE SIMILARITY ANALYSIS (REFACTORED)")
    
    # ============================================================
    # INITIALIZE NEO4J CONNECTOR
    # ============================================================
    print_section("Initializing Neo4j Connection")
    
    try:
        neo4j_connector = Neo4jConnector()
        neo4j_driver = neo4j_connector.get_neo4j_driver(
            uri=NEO4J_URI,
            user=NEO4J_USERNAME,
            password=NEO4J_PASSWORD
        )
       
        if not neo4j_driver:
            error_msg = "Failed to get Neo4j driver"
            print(f"❌ {error_msg}")
            raise ConnectionError(error_msg)
            
        print("✅ Neo4j connector initialized")
        
        # Test connection
        with neo4j_driver.session(database=NEO4J_DATABASE) as session:
            result = session.run("RETURN 1 AS test")
            test_value = result.single()['test']
            if test_value == 1:
                print("✅ Successfully connected to Neo4j database")
            else:
                error_msg = "Connection test failed"
                print(f"❌ {error_msg}")
                raise ConnectionError(error_msg)
                
    except Exception as e:
        print(f"❌ Failed to connect to Neo4j: {e}")
        print("\n💡 Troubleshooting steps:")
        print("  1. Verify Neo4j is running (check Docker/Desktop Neo4j)")
        print("  2. Check credentials in .env file")
        print("  3. Verify URI format (neo4j://localhost:7687)")
        print("  4. Check firewall settings")
        raise
    
    # ============================================================
    # INITIALIZE REFACTORED COMPONENTS
    # ============================================================
    print_section("Initializing Analysis Components")
    
    # Create ModelCombinedSimilarity instance 
    # (internally creates CategoryAnalysis and SimilarityAnalysis with their own Neo4j connectors)
    model = ModelCombinedSimilarity()
    print("✅ ModelCombinedSimilarity initialized")
    print("  - CategoryAnalysis ready")
    print("  - SimilarityAnalysis ready")
    
    # ============================================================
    # CONFIGURE ANALYSIS PARAMETERS
    # ============================================================
    print_section("Configuration")
    
    distance_metric = 'cosine'   # Options: 'cosine', 'euclidean', 'dot_product', 'manhattan'
    
    print(f"  Source Store: {params.store_a}")
    print(f"  Target Store: {params.store_b}")
    print(f"  Distance Metric: {distance_metric.upper()}")
    print("  Min Graph Matches: 2")
    print("  Score Threshold: 0.5")
    print(f"  Top N Results per Product: {params.top_n_results}")
    print("  Max Workers (Neo4j): 10 [OPTIMIZED for memory]")
    print("  Max Embedding Workers: 50 [OPTIMIZED for memory]")
    print("  Batch Size: 10 products per batch")
    
    # Build weight dictionaries from Pydantic models
    weights_food = {
        # Graph-based weights (characteristics)
        "Brand": params.food_weights.graph.brand,
        "Format": params.food_weights.graph.format,
        "Internal_Subcategory": params.food_weights.graph.subcategory,
        "First_level_ingredient": params.food_weights.graph.first_ingredient,
        "Second_level_ingredient": params.food_weights.graph.second_ingredient,
        "Unit_measure": params.food_weights.graph.unit_measure,
        "Allergen": params.food_weights.graph.allergen,
        "Country_of_Origin": params.food_weights.graph.country_origin,
        "Other_Ingredient": params.food_weights.graph.other_ingredient,
        "Quantity": params.food_weights.graph.quantity,
        # Combined weights (similarity methods)
        "graph": params.food_weights.combined.graph,
        "name": params.food_weights.combined.name,
        "description": params.food_weights.combined.description,
        "euclidean": params.food_weights.combined.euclidean
    }
    
    weights_non_food_super = {
        # Graph-based weights (characteristics)
        "Brand": params.non_food_super_weights.graph.brand,
        "Format": params.non_food_super_weights.graph.format,
        "Internal_Subcategory": params.non_food_super_weights.graph.subcategory,
        "First_level_component": params.non_food_super_weights.graph.first_component,
        "Second_level_component": params.non_food_super_weights.graph.second_component,
        "Unit_measure": params.non_food_super_weights.graph.unit_measure,
        "Country_of_Origin": params.non_food_super_weights.graph.country_origin,
        "Quantity": params.non_food_super_weights.graph.quantity,
        # Combined weights (similarity methods)
        "graph": params.non_food_super_weights.combined.graph,
        "name": params.non_food_super_weights.combined.name,
        "description": params.non_food_super_weights.combined.description,
        "euclidean": params.non_food_super_weights.combined.euclidean
    }
    
    weights_non_food_elec = {
        # Graph-based weights (characteristics)
        "Brand": params.non_food_elec_weights.graph.brand,
        "Format": params.non_food_elec_weights.graph.format,
        "Internal_Subcategory": params.non_food_elec_weights.graph.subcategory,
        "First_level_component": params.non_food_elec_weights.graph.first_component,
        "Second_level_component": params.non_food_elec_weights.graph.second_component,
        "Unit_measure": params.non_food_elec_weights.graph.unit_measure,
        "Country_of_Origin": params.non_food_elec_weights.graph.country_origin,
        "Quantity": params.non_food_elec_weights.graph.quantity,
        # Combined weights (similarity methods)
        "graph": params.non_food_elec_weights.combined.graph,
        "name": params.non_food_elec_weights.combined.name,
        "description": params.non_food_elec_weights.combined.description,
        "euclidean": params.non_food_elec_weights.combined.euclidean
    }

    cat_score_threshold = params.quality_thresholds.cat_score_threshold
    combined_score_threshold = params.quality_thresholds.combined_score_threshold
    
    print(f"\n  FOOD Weights (Graph + Combined):")
    for key, value in weights_food.items():
        category = "[Graph]" if key not in ["graph", "name", "description", "euclidean"] else "[Combined]"
        print(f"    {category} {key}: {value:.3f}")
    
    print(f"\n  NON-FOOD SUPERMARKET Weights (Graph + Combined):")
    for key, value in weights_non_food_super.items():
        category = "[Graph]" if key not in ["graph", "name", "description", "euclidean"] else "[Combined]"
        print(f"    {category} {key}: {value:.3f}")
    
    print(f"\n  NON-FOOD ELECTRONICS Weights (Graph + Combined):")
    for key, value in weights_non_food_elec.items():
        category = "[Graph]" if key not in ["graph", "name", "description", "euclidean"] else "[Combined]"
        print(f"    {category} {key}: {value:.3f}")
    
    # ============================================================
    # RUN CROSS-STORE SIMILARITY ANALYSIS
    # ============================================================
    print_section("Running Cross-Store Similarity Analysis")
    
    # ============================================================
    # ID OBTENTION METHOD CONFIGURATION
    # ============================================================
    # Choose one of three methods to obtain product IDs from store A:
    # 
    # METHOD 1: 'neo4j' - Load all products from Neo4j store (default)
    #   - Automatically fetches all products from the specified store
    #   - Use when you want to analyze all products in the store
    #   Example:
    #       id_obtention_method='neo4j'
    #       (no additional parameters needed)
    #
    # METHOD 2: 'external_file' - Load IDs from external file
    #   - Reads product IDs from a text file (one ID per line)
    #   - Use when you have a pre-generated list of IDs to analyze
    #   - Requires: external_ids_path parameter
    #   Example:
    #       id_obtention_method='external_file'
    #       external_ids_path=str(PROJECT_ROOT / "src" / "models" / "2_eroski_id.txt")
    #
    # METHOD 3: 'id_list' - Use provided list of IDs
    #   - Directly provide a list of product IDs in code
    #   - Use for quick tests or when IDs are generated dynamically
    #   - Requires: id_lists parameter
    #   Example:
    #       id_obtention_method='id_list'
    #       id_lists=['123456789', '9876543210', '1111111111']
    # ============================================================
    
    # AUTOMATIC CONFIGURATION: Determine method based on list_ids
    # If list_ids is empty, load all products from Neo4j
    # If list_ids has elements, use the provided list
    if params.list_ids and len(params.list_ids) > 0:
        id_obtention_method = 'id_list'
        print(f"📝 Using id_list method with {len(params.list_ids)} product IDs")
    else:
        id_obtention_method = 'neo4j'
        print(f"📝 Using neo4j method (loading all products from {params.store_a})")
    
    try:
        results = model.find_cross_store_similarities(
            store_a=params.store_a,
            store_b=params.store_b,
            min_matches=params.quality_thresholds.min_matches,
            weights_food=weights_food,
            weights_non_food_super=weights_non_food_super,
            weights_non_food_elec=weights_non_food_elec,
            distance_metric=distance_metric,
            max_workers=10,  # OPTIMIZED: Reduced to 10 to prevent Neo4j memory issues
            max_embedding_workers=50,  # OPTIMIZED: Reduced to 50
            id_obtention_method=id_obtention_method,
            id_lists=params.list_ids,
            external_ids_path=str(PROJECT_ROOT / "src" / "models" / "2_eroski_id.txt"),
            cat_score_threshold=cat_score_threshold,
            combined_score_threshold=combined_score_threshold,
            top_n=params.top_n_results,
            min_graph_score=params.quality_thresholds.min_graph_score,
            min_name_similarity=params.quality_thresholds.min_name_similarity,
            min_description_similarity=params.quality_thresholds.min_description_similarity,
            min_euclidean_similarity=params.quality_thresholds.min_euclidean_similarity,
            avoid_duplicate_product_b=params.avoid_duplicate_product_b
        )
        
        print("✅ Analysis complete!")
        
        # Transform results to simplified format: {store_a_id: [store_b_id1, store_b_id2, ...]}
        # Ensure all keys and values are strings (not floats)
        simplified_result = {
            str(product_id): [str(prod['id']) for prod in data.get('similar_products_b', [])]
            for product_id, data in results.items()
            if product_id != '_metadata'
        }
        
        # Print simplified results
        import json
        print("\n📋 Simplified Results:")
        print(simplified_result)
        
        # Display summary
        metadata = results.get('_metadata', {})
        total_products = len([k for k in results.keys() if k != '_metadata'])
        products_with_matches = sum(
            1 for k, v in results.items() 
            if k != '_metadata' and v.get('similar_products_b')
        )
        
        print("\n📊 Results Summary:")
        print(f"  Total products analyzed: {total_products}")
        print(f"  Products with matches: {products_with_matches}")
        if metadata:
            print(f"  Processing time: {metadata.get('processing_time_seconds', 'N/A')} seconds")
            print(f"  Score threshold used: {metadata.get('score_threshold', 'N/A')}")
        
    except Exception as e:
        logging.error(f"Error during analysis: {e}")
        neo4j_connector.close_connection()
        raise
    
    # ============================================================
    # DISPLAY SAMPLE RESULTS (OPTIONAL)
    # ============================================================
    print_section("Sample Results (Top 3 Products)")
    
    count = 0
    for product_a_id, data in results.items():
        if product_a_id == '_metadata':
            continue
        
        count += 1
        if count > 3:  # Show only first 3 products
            break
        
        product_a = data.get('product_a', {})
        similar_b = data.get('similar_products_b', [])
        is_not_found = data.get('not_found', False)
        
        print(f"\n[{count}] Product from {params.store_a}:")
        print(f"    ID: {product_a_id}")
        
        if is_not_found:
            print("    ❌ Product not found in database")
        else:
            product_name = product_a.get('product_name') or 'N/A'
            print(f"    Name: {product_name[:60]}")
            print(f"    SIID: {product_a.get('siid', 'N/A')}")
        
        if similar_b:
            print(f"    Found {len(similar_b)} matches in {params.store_b}:")
            for i, prod_b in enumerate(similar_b[:3], 1):  # Show top 3 matches
                combined_score = prod_b.get('combined_score', 0.0)
                graph_score = prod_b.get('weighted_score', 0.0)
                name_sim = prod_b.get('name_similarity', 0.0)
                
                print(f"      #{i} | Combined: {combined_score:.3f} | Graph: {graph_score:.3f} | Name: {name_sim:.3f}")
                print(f"         Name: {prod_b.get('product_name', 'N/A')[:50]}")
                print(f"         ID: {prod_b.get('id', 'N/A')}")
        else:
            print(f"    ❌ No matches found in {params.store_b}")
    
    print(f"\n{'='*70}")
    
    # ============================================================
    # EXPORT RESULTS
    # ============================================================
    print_section("Exporting Results")
    
    try:
        # Export to Excel
        excel_file = export_cross_store_results_to_excel(
            results=results,
            store_a=params.store_a,
            store_b=params.store_b
        )
        print(f"✅ Excel file created: {excel_file}")
        
        # Export to CSV
        # csv_file = export_cross_store_results_to_csv(
        #     results=results,
        #     store_a=params.store_a,
        #     store_b=params.store_b
        # )
        # print(f"✅ CSV file created: {csv_file}")
        
        # Export SIID pairs (ranks 1-3 only)
        # siid_csv = export_siid_pairs_csv(
        #     results=results,
        #     store_a=params.store_a,
        #     store_b=params.store_b,
        #     metadata=metadata
        # )
        # if siid_csv:
        #     print(f"✅ SIID pairs CSV created: {siid_csv}")
        #     print("   (Contains only ranks 1, 2, and 3)")
        
    except Exception as e:
        logging.error(f"Error during export: {e}")
    
    # ============================================================
    # CLEANUP
    # ============================================================
    print_section("Cleanup")
    neo4j_connector.close_connection()
    print("✅ Neo4j connection closed")
    
    print_header("ANALYSIS COMPLETE")
    print()
    
    # Return the simplified result
    return simplified_result


if __name__ == "__main__":
    params = ModelParameters(
        store_a="eroski-01013",
        store_b="makro-01013",
        list_ids=[],
        top_n_results=1
    )
    model(params)
