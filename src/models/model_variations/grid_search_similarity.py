"""
Grid Search for Cross-Store Similarity Parameters - REFACTORED VERSION

This module performs a comprehensive grid search over the parameters of 
find_cross_store_similarities using the refactored architecture to find optimal configurations.

For each parameter combination:
- Generates similarity results CSV
- Tracks execution time and statistics
- Saves summary CSV with all configurations and results

Architecture:
    Uses the refactored ModelCombinedSimilarity class which orchestrates:
    - CategoryAnalysis: Graph-based similarity
    - SimilarityAnalysis: Embedding-based similarity
    - Euclidean distance: Numerical feature similarity

Author: Auto-generated from refactored architecture
Date: November 28, 2025
"""

import logging
import sys
import time
import pandas as pd
import itertools
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from config.logs import setup_logging
from config.settings import PROJECT_ROOT, NEO4J_CONFIG

# Import refactored components
from src.connectors.neo4j_connector import Neo4jConnector
from src.models.model_variations.model import ModelCombinedSimilarity
from src.model_exports.export_utils import export_siid_pairs_csv

# Setup logging
setup_logging(level=logging.INFO)


class GridSearchSimilarityRefactored:
    """
    Perform grid search over similarity parameters using refactored architecture.
    """
    
    def __init__(self, output_dir: str = None):
        """
        Initialize grid search.
        
        Args:
            output_dir: Directory to save results (default: data/grid_search_results/)
        """
        self.output_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "grid_search_results"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize refactored model
        self.model = ModelCombinedSimilarity()
        
        # Initialize Neo4j connector for connection management
        self.neo4j_connector = Neo4jConnector()
        
        # Results tracking
        self.grid_search_results = []
        
    def define_parameter_grid(self) -> Dict[str, List[Any]]:
        """
        Define the parameter grid to search over.
        Fine-tuned version focusing on top 4 configurations (20, 24, 28, 32).
        
        These configs achieved 94.02% match rate with:
        - distance_metric: euclidean
        - score_threshold: 0.5
        - Same graph weights for FOOD/NON-FOOD
        
        We'll do fine variations around their combined weights.
        
        Returns:
            Dictionary with parameter names as keys and lists of values to try
        """
        grid = {
            # Graph matching parameters - FIXED (from best configs)
            'min_matches': [2],
            
            # FOOD Node type weights - FIXED (all top configs use these)
            'weight_brand': [0.3],
            'weight_format': [0.15],
            'weight_subcategory': [0.25],
            'weight_first_ingredient': [0.3],
            'weight_second_ingredient': [0.10],
            
            # NON-FOOD SUPERMARKET Node type weights - FIXED
            'weight_brand_non_food_super': [0.20],
            'weight_format_non_food_super': [0.30],
            'weight_subcategory_non_food_super': [0.30],
            'weight_first_component_non_food_super': [0.10],
            'weight_second_component_non_food_super': [0.10],
            
            # NON-FOOD ELECTRONICS Node type weights - FIXED
            'weight_brand_non_food_elec': [0.40],
            'weight_format_non_food_elec': [0.15],
            'weight_subcategory_non_food_elec': [0.30],
            'weight_first_component_non_food_elec': [0.25],
            'weight_second_component_non_food_elec': [0.15],
            
            # Combined score weights - FINE VARIATIONS around best configs
            # Config 20: (0.37, 0.32, 0.21, 0.11)
            # Config 24/32: (0.47, 0.33, 0.13, 0.07)
            # Config 28: (0.25, 0.42, 0.25, 0.08) - NON-FOOD variant
            # Format: (graph, name, description, euclidean)
            'combined_weights_preset_food': [
                # Around Config 20 (0.37, 0.32, 0.21, 0.11)
                (37, 32, 21, 11),  # Original Config 20
                (33, 35, 21, 11),  # -4 graph, +3 name
                (40, 30, 21, 10),  # +3 graph, -2 name
                (37, 32, 18, 14),  # -3 desc, +3 euclidean
                
                # Around Config 24/32 (0.47, 0.33, 0.13, 0.07)
                (47, 33, 13, 7),   # Original Config 24/32
                (43, 37, 13, 7),   # -4 graph, +4 name
                (50, 30, 13, 7),   # +3 graph, -3 name
                (47, 33, 10, 10),  # -3 desc, +3 euclidean
            ],
            
            'combined_weights_preset_non_food_super': [
                # Around Config 28 NON-FOOD (0.25, 0.42, 0.25, 0.08)
                (25, 42, 25, 8),   # Original Config 28
                (21, 46, 25, 8),   # -4 graph, +4 name
                (28, 39, 25, 8),   # +3 graph, -3 name
                (25, 42, 21, 12),  # -4 desc, +4 euclidean
                
                # Also try Config 20 variant for NON-FOOD
                (37, 32, 21, 11),
                (47, 33, 13, 7),
            ],
            
            'combined_weights_preset_non_food_elec': [
                # Use same as FOOD (configs don't differentiate much)
                (37, 32, 21, 11),
                (47, 33, 13, 7),
                (25, 42, 25, 8),
            ],
            
            # Similarity parameters - FOCUSED on best performers
            'distance_metric': ['euclidean'],  # All top 4 use euclidean
            'score_threshold': [0.5],          # All top 4 use 0.5
            
            # Performance parameters
            'max_workers': [100],
            'max_embedding_workers': [200]
        }
        
        return grid
    
    def _validate_weights(self, weights: Dict[str, float], combined_weights: Dict[str, float]) -> bool:
        """
        Validate that weight combinations are reasonable.
        
        Args:
            weights: Node type weights
            combined_weights: Combined score weights (graph, name, description, euclidean)
            
        Returns:
            True if weights are valid, False otherwise
        """
        # Combined weights should sum to ~1.0
        combined_sum = sum(combined_weights.values())
        if abs(combined_sum - 1.0) > 0.05:
            return False
        
        # All weights should be non-negative
        if any(v < 0 for v in combined_weights.values()):
            return False
        
        return True
    
    def generate_parameter_combinations(self) -> List[Dict[str, Any]]:
        """
        Generate all valid parameter combinations from the grid.
        
        Returns:
            List of parameter dictionaries
        """
        grid = self.define_parameter_grid()
        
        # Generate all combinations
        keys = grid.keys()
        values = grid.values()
        combinations = []
        
        for combination in itertools.product(*values):
            params = dict(zip(keys, combination))
            
            # Extract combined weights presets (separate for FOOD, NON-FOOD SUPER, NON-FOOD ELEC)
            combined_preset_food = params.pop('combined_weights_preset_food')
            combined_preset_non_food_super = params.pop('combined_weights_preset_non_food_super')
            combined_preset_non_food_elec = params.pop('combined_weights_preset_non_food_elec')
            
            # Normalize the FOOD preset values to sum to 1.0
            preset_sum_food = sum(combined_preset_food)
            combined_weights_food = {
                'graph': combined_preset_food[0] / preset_sum_food,
                'name': combined_preset_food[1] / preset_sum_food,
                'description': combined_preset_food[2] / preset_sum_food,
                'euclidean': combined_preset_food[3] / preset_sum_food
            }
            
            # Normalize the NON-FOOD SUPERMARKET preset values to sum to 1.0
            preset_sum_non_food_super = sum(combined_preset_non_food_super)
            combined_weights_non_food_super = {
                'graph': combined_preset_non_food_super[0] / preset_sum_non_food_super,
                'name': combined_preset_non_food_super[1] / preset_sum_non_food_super,
                'description': combined_preset_non_food_super[2] / preset_sum_non_food_super,
                'euclidean': combined_preset_non_food_super[3] / preset_sum_non_food_super
            }
            
            # Normalize the NON-FOOD ELECTRONICS preset values to sum to 1.0
            preset_sum_non_food_elec = sum(combined_preset_non_food_elec)
            combined_weights_non_food_elec = {
                'graph': combined_preset_non_food_elec[0] / preset_sum_non_food_elec,
                'name': combined_preset_non_food_elec[1] / preset_sum_non_food_elec,
                'description': combined_preset_non_food_elec[2] / preset_sum_non_food_elec,
                'euclidean': combined_preset_non_food_elec[3] / preset_sum_non_food_elec
            }
            
            # Save a copy of remaining params before extracting weight components
            params_copy = {k: v for k, v in params.items() if not k.startswith('weight_')}
            
            # Build FOOD complete weights dictionary (graph + combined)
            weights_food = {
                # Graph weights
                'Brand': params.get('weight_brand'),
                'Format': params.get('weight_format'),
                'Internal_Subcategory': params.get('weight_subcategory'),
                'First_level_ingredient': params.get('weight_first_ingredient'),
                'Second_level_ingredient': params.get('weight_second_ingredient'),
                'Unit_measure': 0.10,
                'Allergen': 0.02,
                'Country_of_Origin': 0.05,
                'Other_Ingredient': 0.02,
                'Quantity': 0.10,
                # Combined weights (FOOD-specific)
                'graph': combined_weights_food['graph'],
                'name': combined_weights_food['name'],
                'description': combined_weights_food['description'],
                'euclidean': combined_weights_food['euclidean']
            }
            
            # Build NON-FOOD SUPERMARKET complete weights dictionary (graph + combined)
            weights_non_food_super = {
                # Graph weights
                'Brand': params.get('weight_brand_non_food_super'),
                'Format': params.get('weight_format_non_food_super'),
                'Internal_Subcategory': params.get('weight_subcategory_non_food_super'),
                'First_level_component': params.get('weight_first_component_non_food_super'),
                'Second_level_component': params.get('weight_second_component_non_food_super'),
                'Unit_measure': 0.150,
                'Country_of_Origin': 0.100,
                'Quantity': 0.150,
                # Combined weights (NON-FOOD SUPERMARKET-specific)
                'graph': combined_weights_non_food_super['graph'],
                'name': combined_weights_non_food_super['name'],
                'description': combined_weights_non_food_super['description'],
                'euclidean': combined_weights_non_food_super['euclidean']
            }
            
            # Build NON-FOOD ELECTRONICS complete weights dictionary (graph + combined)
            weights_non_food_elec = {
                # Graph weights
                'Brand': params.get('weight_brand_non_food_elec'),
                'Format': params.get('weight_format_non_food_elec'),
                'Internal_Subcategory': params.get('weight_subcategory_non_food_elec'),
                'First_level_component': params.get('weight_first_component_non_food_elec'),
                'Second_level_component': params.get('weight_second_component_non_food_elec'),
                'Unit_measure': 0.100,
                'Country_of_Origin': 0.050,
                'Quantity': 0.100,
                # Combined weights (NON-FOOD ELECTRONICS-specific)
                'graph': combined_weights_non_food_elec['graph'],
                'name': combined_weights_non_food_elec['name'],
                'description': combined_weights_non_food_elec['description'],
                'euclidean': combined_weights_non_food_elec['euclidean']
            }
            
            # Validate (use FOOD combined weights for validation)
            if self._validate_weights(weights_food, combined_weights_food):
                params['weights_food'] = weights_food
                params['weights_non_food_super'] = weights_non_food_super
                params['weights_non_food_elec'] = weights_non_food_elec
                params.update(params_copy)  # Restore other params
                combinations.append(params)
        
        logging.info(f"✅ Generated {len(combinations)} valid parameter combinations")
        return combinations
    
    def run_single_configuration(
        self,
        config_id: int,
        total_configs: int,
        params: Dict[str, Any],
        store_a: str,
        store_b: str,
        limit_products_a: int = None,
        id_obtention_method: str = 'external_file',
        external_ids_path: str = None
    ) -> Dict[str, Any]:
        """
        Run similarity analysis with a single parameter configuration.
        
        Args:
            config_id: Configuration number (for tracking)
            total_configs: Total number of configurations
            params: Parameter dictionary
            store_a: Origin store name
            store_b: Destination store name
            limit_products_a: Limit on products to process
            id_obtention_method: Method to obtain product IDs ('neo4j', 'external_file', 'id_list')
            external_ids_path: Path to external IDs file (required if id_obtention_method='external_file')
            
        Returns:
            Dictionary with results and metadata
        """
        logging.info(f"\n{'='*80}")
        logging.info(f"🔬 CONFIGURATION {config_id}/{total_configs}")
        logging.info(f"{'='*80}")
        
        # Log parameters
        logging.info("📋 Parameters:")
        for key, value in params.items():
            if key in ['weights_food', 'weights_non_food_super', 'weights_non_food_elec']:
                logging.info(f"   {key}:")
                for k, v in value.items():
                    if v is not None:
                        logging.info(f"      {k}: {v:.3f}")
                    else:
                        logging.info(f"      {k}: None")
            else:
                logging.info(f"   {key}: {value}")
        
        # Start timer
        start_time = time.time()
        
        try:
            # Run similarity analysis using refactored model
            results = self.model.find_cross_store_similarities(
                store_a=store_a,
                store_b=store_b,
                min_matches=params['min_matches'],
                weights_food=params['weights_food'],
                weights_non_food_super=params['weights_non_food_super'],
                weights_non_food_elec=params['weights_non_food_elec'],
                limit_products_a=limit_products_a,
                distance_metric=params['distance_metric'],
                max_workers=params['max_workers'],
                max_embedding_workers=params['max_embedding_workers'],
                id_obtention_method=id_obtention_method,
                external_ids_path=external_ids_path,
                score_threshold=params['score_threshold']
            )
            
            # Calculate statistics
            elapsed_time = time.time() - start_time
            
            total_products = len([k for k in results.keys() if k != '_metadata'])
            products_with_matches = sum(
                1 for k, v in results.items() 
                if k != '_metadata' and v.get('similar_products_b') and not v.get('not_found', False)
            )
            products_not_found = sum(
                1 for k, v in results.items()
                if k != '_metadata' and v.get('not_found', False)
            )
            
            avg_matches = 0
            if products_with_matches > 0:
                total_matches = sum(
                    len(v.get('similar_products_b', []))
                    for k, v in results.items()
                    if k != '_metadata' and v.get('similar_products_b') and not v.get('not_found', False)
                )
                avg_matches = total_matches / products_with_matches
            
            # Calculate average scores (including euclidean)
            avg_combined_score = 0
            avg_graph_score = 0
            avg_name_sim = 0
            avg_desc_sim = 0
            avg_euclidean_sim = 0
            score_count = 0
            
            for k, v in results.items():
                if k == '_metadata' or v.get('not_found', False):
                    continue
                for prod in v.get('similar_products_b', []):
                    if 'combined_score' in prod:
                        avg_combined_score += prod['combined_score']
                        avg_graph_score += prod.get('weighted_score', 0)
                        avg_name_sim += prod.get('name_similarity', 0)
                        avg_desc_sim += prod.get('description_similarity', 0)
                        avg_euclidean_sim += prod.get('euclidean_similarity', 0)
                        score_count += 1
            
            if score_count > 0:
                avg_combined_score /= score_count
                avg_graph_score /= score_count
                avg_name_sim /= score_count
                avg_desc_sim /= score_count
                avg_euclidean_sim /= score_count
            
            # Generate unique filename for this configuration
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_filename = f"config_{config_id:04d}_{timestamp}.csv"
            csv_path = self.output_dir / csv_filename
            
            # Export results to CSV
            export_siid_pairs_csv(
                results=results,
                store_a=store_a,
                store_b=store_b,
                output_path=str(csv_path),
                metadata=results.get('_metadata', {})
            )
            
            # Prepare result summary
            result_summary = {
                'config_id': config_id,
                'timestamp': timestamp,
                'csv_file': csv_filename,
                'elapsed_time_seconds': round(elapsed_time, 2),
                'store_a': store_a,
                'store_b': store_b,
                
                # Parameters
                'min_matches': params['min_matches'],
                'distance_metric': params['distance_metric'],
                'score_threshold': params['score_threshold'],
                'max_workers': params['max_workers'],
                'max_embedding_workers': params['max_embedding_workers'],
                
                # FOOD Graph weights (flattened)
                'weight_brand_food': params['weights_food']['Brand'],
                'weight_format_food': params['weights_food']['Format'],
                'weight_subcategory_food': params['weights_food']['Internal_Subcategory'],
                'weight_first_ingredient_food': params['weights_food']['First_level_ingredient'],
                'weight_second_ingredient_food': params['weights_food']['Second_level_ingredient'],
                
                # NON-FOOD SUPERMARKET Graph weights (flattened)
                'weight_brand_non_food_super': params['weights_non_food_super']['Brand'],
                'weight_format_non_food_super': params['weights_non_food_super']['Format'],
                'weight_subcategory_non_food_super': params['weights_non_food_super']['Internal_Subcategory'],
                'weight_first_component_non_food_super': params['weights_non_food_super']['First_level_component'],
                'weight_second_component_non_food_super': params['weights_non_food_super']['Second_level_component'],
                
                # NON-FOOD ELECTRONICS Graph weights (flattened)
                'weight_brand_non_food_elec': params['weights_non_food_elec']['Brand'],
                'weight_format_non_food_elec': params['weights_non_food_elec']['Format'],
                'weight_subcategory_non_food_elec': params['weights_non_food_elec']['Internal_Subcategory'],
                'weight_first_component_non_food_elec': params['weights_non_food_elec']['First_level_component'],
                'weight_second_component_non_food_elec': params['weights_non_food_elec']['Second_level_component'],
                
                # FOOD Combined weights
                'combined_weight_graph_food': params['weights_food']['graph'],
                'combined_weight_name_food': params['weights_food']['name'],
                'combined_weight_description_food': params['weights_food']['description'],
                'combined_weight_euclidean_food': params['weights_food']['euclidean'],
                
                # NON-FOOD SUPERMARKET Combined weights
                'combined_weight_graph_non_food_super': params['weights_non_food_super']['graph'],
                'combined_weight_name_non_food_super': params['weights_non_food_super']['name'],
                'combined_weight_description_non_food_super': params['weights_non_food_super']['description'],
                'combined_weight_euclidean_non_food_super': params['weights_non_food_super']['euclidean'],
                
                # NON-FOOD ELECTRONICS Combined weights
                'combined_weight_graph_non_food_elec': params['weights_non_food_elec']['graph'],
                'combined_weight_name_non_food_elec': params['weights_non_food_elec']['name'],
                'combined_weight_description_non_food_elec': params['weights_non_food_elec']['description'],
                'combined_weight_euclidean_non_food_elec': params['weights_non_food_elec']['euclidean'],
                
                # Results
                'total_products': total_products,
                'products_with_matches': products_with_matches,
                'products_not_found': products_not_found,
                'match_rate': round(products_with_matches / total_products * 100, 2) if total_products > 0 else 0,
                'avg_matches_per_product': round(avg_matches, 2),
                'avg_combined_score': round(avg_combined_score, 4),
                'avg_graph_score': round(avg_graph_score, 4),
                'avg_name_similarity': round(avg_name_sim, 4),
                'avg_description_similarity': round(avg_desc_sim, 4),
                'avg_euclidean_similarity': round(avg_euclidean_sim, 4)
            }
            
            logging.info(f"\n✅ Configuration {config_id} completed successfully!")
            logging.info(f"   Time: {elapsed_time:.2f}s")
            logging.info(f"   Match rate: {result_summary['match_rate']:.2f}%")
            logging.info(f"   Avg matches: {result_summary['avg_matches_per_product']:.2f}")
            logging.info(f"   Avg combined score: {result_summary['avg_combined_score']:.4f}")
            logging.info(f"   Avg euclidean similarity: {result_summary['avg_euclidean_similarity']:.4f}")
            logging.info(f"   CSV saved: {csv_filename}")
            
            return result_summary
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            logging.error(f"❌ Configuration {config_id} failed: {e}")
            import traceback
            logging.error(traceback.format_exc())
            
            return {
                'config_id': config_id,
                'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
                'csv_file': 'FAILED',
                'elapsed_time_seconds': round(elapsed_time, 2),
                'error': str(e),
                'min_matches': params.get('min_matches', ''),
                'distance_metric': params.get('distance_metric', ''),
                'score_threshold': params.get('score_threshold', '')
            }
    
    def run_grid_search(
        self,
        store_a: str,
        store_b: str,
        limit_products_a: int = None,
        id_obtention_method: str = 'external_file',
        external_ids_path: str = None,
        max_configs: int = None
    ) -> pd.DataFrame:
        """
        Run grid search over all parameter combinations.
        
        Args:
            store_a: Origin store name
            store_b: Destination store name
            limit_products_a: Limit on products to process per configuration
            id_obtention_method: Method to obtain product IDs ('neo4j', 'external_file', 'id_list')
            external_ids_path: Path to external IDs file (required if id_obtention_method='external_file')
            max_configs: Maximum number of configurations to test (for debugging)
            
        Returns:
            DataFrame with all results
        """
        logging.info(f"\n{'='*80}")
        logging.info(f"🚀 STARTING GRID SEARCH (REFACTORED)")
        logging.info(f"{'='*80}")
        logging.info(f"   Store A: {store_a}")
        logging.info(f"   Store B: {store_b}")
        logging.info(f"   Product limit: {limit_products_a}")
        logging.info(f"   Output directory: {self.output_dir}")
        logging.info(f"   Using refactored architecture with euclidean distance")
        
        # Test Neo4j connection
        try:
            neo4j_driver = self.neo4j_connector.get_neo4j_driver()
            if not neo4j_driver:
                logging.error("❌ Failed to connect to Neo4j")
                return None
            logging.info("✅ Neo4j connection established")
        except Exception as e:
            logging.error(f"❌ Failed to connect to Neo4j: {e}")
            return None
        
        # Generate parameter combinations
        combinations = self.generate_parameter_combinations()
        
        # Limit for debugging
        if max_configs:
            combinations = combinations[:max_configs]
            logging.info(f"⚠️ Limited to first {max_configs} configurations for debugging")
        
        print(combinations)
        total_configs = len(combinations)
        logging.info(f"📊 Total configurations to test: {total_configs}")
        
        # Run each configuration
        for i, params in enumerate(combinations, 1):
            result = self.run_single_configuration(
                config_id=i,
                total_configs=total_configs,
                params=params,
                store_a=store_a,
                store_b=store_b,
                limit_products_a=limit_products_a,
                id_obtention_method=id_obtention_method,
                external_ids_path=external_ids_path
            )
            self.grid_search_results.append(result)
            
            # Save intermediate results every 5 configs
            if i % 5 == 0:
                self.save_summary()
        
        # Final save
        self.save_summary()
        
        # Close connection
        self.neo4j_connector.close_connection()
        
        logging.info(f"\n{'='*80}")
        logging.info(f"✅ GRID SEARCH COMPLETED!")
        logging.info(f"{'='*80}")
        logging.info(f"   Total configurations: {total_configs}")
        logging.info(f"   Successful: {sum(1 for r in self.grid_search_results if 'error' not in r)}")
        logging.info(f"   Failed: {sum(1 for r in self.grid_search_results if 'error' in r)}")
        
        return pd.DataFrame(self.grid_search_results)
    
    def save_summary(self) -> str:
        """
        Save grid search summary to CSV.
        
        Returns:
            Path to summary CSV file
        """
        if not self.grid_search_results:
            logging.warning("⚠️ No results to save")
            return None
        
        # Create DataFrame
        df = pd.DataFrame(self.grid_search_results)
        
        # Sort by match rate and avg combined score
        if 'match_rate' in df.columns and 'avg_combined_score' in df.columns:
            df = df.sort_values(['match_rate', 'avg_combined_score'], ascending=[False, False])
        
        # Save to CSV
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        summary_path = self.output_dir / f"grid_search_summary_refactored_{timestamp}.csv"
        df.to_csv(summary_path, index=False, sep=';', encoding='utf-8')
        
        logging.info(f"💾 Summary saved: {summary_path}")
        
        # Also save top 10 configurations
        if len(df) > 10 and 'match_rate' in df.columns:
            top10_path = self.output_dir / f"grid_search_top10_refactored_{timestamp}.csv"
            df.head(10).to_csv(top10_path, index=False, sep=';', encoding='utf-8')
            logging.info(f"🏆 Top 10 configurations saved: {top10_path}")
        
        return str(summary_path)


def main():
    """
    Main function to run grid search with refactored architecture.
    """
    from dotenv import load_dotenv
    load_dotenv()
    
    # ============================================================
    # CONFIGURATION
    # ============================================================
    store_a = "eroski-01013-01"
    store_b = "makro-01013-01"
    limit_products_a = 20000  # Small limit for testing grid search
    id_obtention_method = 'external_file'  # Options: 'neo4j', 'external_file', 'id_list'
    external_ids_path = str(PROJECT_ROOT / "src" / "models" / "2_eroski_id.txt")
    
    # For debugging: limit number of configurations
    max_configs = None  # Set to None to run all, or a number to limit
    
    # ============================================================
    # RUN GRID SEARCH
    # ============================================================
    grid_search = GridSearchSimilarityRefactored()
    
    results_df = grid_search.run_grid_search(
        store_a=store_a,
        store_b=store_b,
        limit_products_a=limit_products_a,
        id_obtention_method=id_obtention_method,
        external_ids_path=external_ids_path,
        max_configs=max_configs
    )
    
    if results_df is not None:
        print("\n" + "="*80)
        print("📊 GRID SEARCH RESULTS SUMMARY (REFACTORED)")
        print("="*80)
        
        # Display top 5 configurations
        if 'match_rate' in results_df.columns and 'avg_combined_score' in results_df.columns:
            print("\n🏆 Top 5 Configurations:")
            display_cols = ['config_id', 'match_rate', 'avg_combined_score', 
                          'avg_euclidean_similarity', 'avg_matches_per_product', 
                          'elapsed_time_seconds']
            print(results_df.head(5)[display_cols].to_string())
        
        print("\n" + "="*80)
        print("✅ Grid search complete! Check output directory for detailed results.")
        print("="*80)

if __name__ == "__main__":
    main()