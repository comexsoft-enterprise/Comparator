"""
Custom Grid Search for Cross-Store Similarity Analysis

This module provides a customizable grid search with different parameter configurations.
Modify the main() function to test your specific parameter combinations.

Author: AI Assistant
"""

import logging
import os
import sys
import json
import pandas as pd
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple
from itertools import product
from datetime import datetime

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from config.logs import setup_logging
setup_logging(level=logging.INFO)

from config.settings import PROJECT_ROOT

# Import from module with number prefix using importlib
import importlib.util
spec = importlib.util.spec_from_file_location(
    "get_similar_neo4j",
    str(project_root / "src" / "models" / "3_get_similar_neo4j.py")
)
get_similar_neo4j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(get_similar_neo4j)

Neo4jController = get_similar_neo4j.Neo4jController
export_cross_store_results_to_csv = get_similar_neo4j.export_cross_store_results_to_csv
export_siid_pairs_csv = get_similar_neo4j.export_siid_pairs_csv
DEFAULT_WEIGHTS = get_similar_neo4j.DEFAULT_WEIGHTS


class SimilarityGridSearch:
    """
    Automated grid search system for cross-store similarity analysis.
    Tests multiple parameter combinations and exports results for comparison.
    """
    
    def __init__(self, output_dir: str = None):
        """
        Initialize the grid search system.
        
        Args:
            output_dir: Directory to save results (default: data/grid_search_results)
        """
        self.output_dir = output_dir or str(PROJECT_ROOT / "data" / "grid_search_results")
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        
        # Initialize Neo4j controller
        self.controller = Neo4jController()
        
        # Store all test results
        self.all_results = []
        
        logging.info(f"✅ Grid search initialized. Results will be saved to: {self.output_dir}")
    
    def define_parameter_grid(
        self,
        stores: List[Tuple[str, str]] = None,
        min_matches_values: List[int] = None,
        weight_configs: List[Dict[str, float]] = None,
        combined_weight_configs: List[Dict[str, float]] = None,
        distance_metrics: List[str] = None,
        category_filters: List[str] = None,
        limit_products: List[int] = None,
        max_workers_values: List[int] = None,
        max_embedding_workers_values: List[int] = None,
        use_external_ids: bool = False,
        external_ids_path: str = None,
        score_threshold_values: List[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Define the parameter grid for testing.
        
        Args:
            stores: List of (store_a, store_b) tuples
            min_matches_values: List of minimum match thresholds
            weight_configs: List of weight dictionaries for graph attributes
            combined_weight_configs: List of weight dictionaries for combined scoring
            distance_metrics: List of distance metrics to test
            category_filters: List of category filters (None = no filter)
            limit_products: List of product limits for testing
            max_workers_values: List of max workers for parallel processing
            max_embedding_workers_values: List of max workers for embedding generation
            use_external_ids: If True, read product IDs from external file
            external_ids_path: Path to file containing product IDs (required if use_external_ids=True)
            score_threshold_values: List of score thresholds for filtering similar products (default: [0.15])
            
        Returns:
            List of parameter combinations to test
        """
        # Default values
        if stores is None:
            stores = [("eroski", "makro")]
        
        if min_matches_values is None:
            min_matches_values = [3, 4, 5]
        
        if weight_configs is None:
            # Test default weights and a few variations
            weight_configs = [
                DEFAULT_WEIGHTS,  # Default configuration
                {
                    "Brand": 0.3,
                    "Format": 0.15,
                    "Product_type": 0.60,
                    "Unit_measure": 0.10,
                    "First_level_ingredient": 0.20,
                    "Second_level_ingredient": 0.10,
                    "Allergen": 0.05
                },
                {  # More weight on product type
                    "Brand": 0.2,
                    "Format": 0.1,
                    "Product_type": 0.7,
                    "Unit_measure": 0.1,
                    "First_level_ingredient": 0.2,
                    "Second_level_ingredient": 0.1,
                    "Allergen": 0.05
                },
                {  # More weight on product type
                    "Brand": 0.15,
                    "Format": 0.05,
                    "Product_type": 0.7,
                    "Unit_measure": 0.05,
                    "First_level_ingredient": 0.25,
                    "Second_level_ingredient": 0.15,
                    "Allergen": 0.05
                }
            ]
        
        if combined_weight_configs is None:
            # Test different combinations of graph, name, and description weights
            combined_weight_configs = [
                {"graph": 0.4, "name": 0.4, "description": 0.2},  # Default
                {"graph": 0.33, "name": 0.34, "description": 0.33},  # Equal
                {"graph": 0.3, "name": 0.34, "description": 0.33}  # Equal
            ]
        
        if distance_metrics is None:
            distance_metrics = ["cosine", "euclidean", "dot_product"]
        
        if category_filters is None:
            category_filters = [None]  # None means no filter
        
        if limit_products is None:
            limit_products = [2000]  # Small sample for testing
        
        if max_workers_values is None:
            max_workers_values = [10]
        
        if max_embedding_workers_values is None:
            max_embedding_workers_values = [100]
        
        if score_threshold_values is None:
            score_threshold_values = [0.15]
        
        # Generate all combinations
        param_grid = []
        
        for (store_a, store_b), min_matches, weights, combined_weights, metric, category, limit, max_w, max_emb_w, score_thresh in product(
            stores,
            min_matches_values,
            weight_configs,
            combined_weight_configs,
            distance_metrics,
            category_filters,
            limit_products,
            max_workers_values,
            max_embedding_workers_values,
            score_threshold_values
        ):
            param_config = {
                "store_a": store_a,
                "store_b": store_b,
                "min_matches": min_matches,
                "weights": weights.copy(),
                "combined_weights": combined_weights.copy(),
                "distance_metric": metric,
                "category_filter": category,
                "limit_products_a": limit,
                "max_workers": max_w,
                "max_embedding_workers": max_emb_w,
                "use_external_ids": use_external_ids,
                "external_ids_path": external_ids_path,
                "score_threshold": score_thresh
            }
            param_grid.append(param_config)
        
        logging.info(f"📊 Generated parameter grid with {len(param_grid)} combinations")
        return param_grid
    
    def run_single_test(
        self,
        params: Dict[str, Any],
        test_id: int,
        total_tests: int
    ) -> Dict[str, Any]:
        """
        Run a single test with given parameters.
        
        Args:
            params: Dictionary of parameters for the test
            test_id: ID of this test (for logging)
            total_tests: Total number of tests (for logging)
            
        Returns:
            Dictionary with test results and metrics
        """
        logging.info(f"\n{'='*80}")
        logging.info(f"🧪 TEST {test_id}/{total_tests}")
        logging.info(f"{'='*80}")
        logging.info(f"Parameters:")
        for key, value in params.items():
            if key not in ['weights', 'combined_weights']:
                logging.info(f"  {key}: {value}")
        logging.info(f"  Graph weights: {params['weights']}")
        logging.info(f"  Combined weights: {params['combined_weights']}")
        
        start_time = time.time()
        
        try:
            # Connect to Neo4j
            if not self.controller.driver:
                self.controller.connect_to_neo4j()
            
            # Run similarity analysis
            results = self.controller.find_cross_store_similarities(
                store_a=params["store_a"],
                store_b=params["store_b"],
                min_matches=params["min_matches"],
                weights=params["weights"],
                limit_products_a=params["limit_products_a"],
                distance_metric=params["distance_metric"],
                category_filter=params["category_filter"],
                max_workers=params["max_workers"],
                max_embedding_workers=params["max_embedding_workers"],
                use_external_ids=params.get("use_external_ids", False),
                external_ids_path=params.get("external_ids_path"),
                score_threshold=params.get("score_threshold", 0.15)
            )
            
            # Calculate metrics
            total_products = len(results) - 1 if '_metadata' in results else len(results)
            products_with_matches = sum(1 for k, r in results.items() if k != '_metadata' and r.get('similar_products_b'))
            
            # Average scores
            avg_graph_score = 0.0
            avg_combined_score = 0.0
            avg_name_sim = 0.0
            avg_desc_sim = 0.0
            total_matches = 0
            
            for key, data in results.items():
                # Skip metadata
                if key == '_metadata':
                    continue
                
                for prod in data.get('similar_products_b', []):
                    avg_graph_score += prod.get('weighted_score', 0.0)
                    avg_combined_score += prod.get('combined_score', 0.0)
                    avg_name_sim += prod.get('name_similarity', 0.0)
                    avg_desc_sim += prod.get('description_similarity', 0.0)
                    total_matches += 1
            
            if total_matches > 0:
                avg_graph_score /= total_matches
                avg_combined_score /= total_matches
                avg_name_sim /= total_matches
                avg_desc_sim /= total_matches
            
            elapsed_time = time.time() - start_time
            
            # Export to CSV with parameter details in filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Build filename with key parameters
            metric = params['distance_metric']
            min_m = params['min_matches']
            graph_w = params['combined_weights']['graph']
            name_w = params['combined_weights']['name']
            desc_w = params['combined_weights']['description']
            
            csv_filename = (f"test_{test_id}_{params['store_a']}_to_{params['store_b']}_"
                          f"metric_{metric}_minm_{min_m}_"
                          f"w_g{graph_w:.2f}_n{name_w:.2f}_d{desc_w:.2f}_{timestamp}.csv")
            csv_path = str(Path(self.output_dir) / csv_filename)
            
            export_cross_store_results_to_csv(
                results=results,
                store_a=params["store_a"],
                store_b=params["store_b"],
                output_path=csv_path
            )
            
            # Also export SIID pairs (ranks 1-3 only) like in 3_get_similar_neo4j.py
            siid_csv_filename = csv_filename.replace('test_', 'siid_pairs_test_')
            siid_csv_path = str(Path(self.output_dir) / siid_csv_filename)
            
            get_similar_neo4j.export_siid_pairs_csv(
                results=results,
                store_a=params["store_a"],
                store_b=params["store_b"],
                output_path=siid_csv_path
            )
            
            # Compile test result
            test_result = {
                "test_id": test_id,
                "timestamp": timestamp,
                "parameters": params.copy(),
                "metrics": {
                    "total_products": total_products,
                    "products_with_matches": products_with_matches,
                    "match_rate": products_with_matches / total_products if total_products > 0 else 0,
                    "total_matches": total_matches,
                    "avg_matches_per_product": total_matches / products_with_matches if products_with_matches > 0 else 0,
                    "avg_graph_score": avg_graph_score,
                    "avg_combined_score": avg_combined_score,
                    "avg_name_similarity": avg_name_sim,
                    "avg_description_similarity": avg_desc_sim,
                    "execution_time_seconds": elapsed_time
                },
                "output_file": csv_path,
                "success": True
            }
            
            logging.info(f"\n✅ Test {test_id} completed successfully!")
            logging.info(f"   Match rate: {test_result['metrics']['match_rate']:.2%}")
            logging.info(f"   Execution time: {elapsed_time:.2f}s")
            logging.info(f"   Results saved to: {csv_filename}")
            
            return test_result
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            
            logging.error(f"❌ Test {test_id} failed: {e}")
            
            return {
                "test_id": test_id,
                "timestamp": datetime.now().strftime('%Y%m%d_%H%M%S'),
                "parameters": params.copy(),
                "metrics": {},
                "output_file": None,
                "success": False,
                "error": str(e),
                "execution_time_seconds": elapsed_time
            }
    
    def run_grid_search(
        self,
        param_grid: List[Dict[str, Any]] = None,
        **grid_params
    ) -> pd.DataFrame:
        """
        Run grid search with all parameter combinations.
        
        Args:
            param_grid: Pre-defined parameter grid (if None, will be generated from grid_params)
            **grid_params: Parameters for define_parameter_grid() if param_grid is None
            
        Returns:
            DataFrame with all test results
        """
        # Generate parameter grid if not provided
        if param_grid is None:
            param_grid = self.define_parameter_grid(**grid_params)
        
        total_tests = len(param_grid)
        logging.info(f"\n🚀 Starting grid search with {total_tests} parameter combinations")
        logging.info(f"📁 Results will be saved to: {self.output_dir}\n")
        
        # Run all tests
        for i, params in enumerate(param_grid, 1):
            test_result = self.run_single_test(params, i, total_tests)
            self.all_results.append(test_result)
        
        # Create summary DataFrame
        summary_data = []
        for result in self.all_results:
            row = {
                "test_id": result["test_id"],
                "timestamp": result["timestamp"],
                "store_a": result["parameters"]["store_a"],
                "store_b": result["parameters"]["store_b"],
                "min_matches": result["parameters"]["min_matches"],
                "distance_metric": result["parameters"]["distance_metric"],
                "category_filter": result["parameters"]["category_filter"],
                "limit_products": result["parameters"]["limit_products_a"],
                "max_workers": result["parameters"]["max_workers"],
                "max_embedding_workers": result["parameters"]["max_embedding_workers"],
                "use_external_ids": result["parameters"].get("use_external_ids", False),
                "external_ids_path": result["parameters"].get("external_ids_path"),
                "success": result["success"]
            }
            
            # Add weight parameters
            for key, value in result["parameters"]["weights"].items():
                row[f"weight_{key.lower()}"] = value
            
            for key, value in result["parameters"]["combined_weights"].items():
                row[f"combined_weight_{key}"] = value
            
            # Add metrics
            if result["success"]:
                for key, value in result["metrics"].items():
                    row[key] = value
            else:
                row["error"] = result.get("error", "Unknown error")
            
            row["output_file"] = result["output_file"]
            summary_data.append(row)
        
        summary_df = pd.DataFrame(summary_data)
        
        # Save summary
        summary_path = Path(self.output_dir) / f"grid_search_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        summary_df.to_excel(summary_path, index=False)
        
        logging.info(f"\n{'='*80}")
        logging.info(f"✅ Grid search completed!")
        logging.info(f"   Total tests: {total_tests}")
        logging.info(f"   Successful: {sum(1 for r in self.all_results if r['success'])}")
        logging.info(f"   Failed: {sum(1 for r in self.all_results if not r['success'])}")
        logging.info(f"   Summary saved to: {summary_path}")
        logging.info(f"{'='*80}\n")
        
        # Print top 5 configurations by avg_combined_score
        if summary_df[summary_df['success']].shape[0] > 0:
            top_5 = summary_df[summary_df['success']].nlargest(5, 'avg_combined_score')
            logging.info("\n🏆 Top 5 configurations by avg_combined_score:")
            for idx, row in top_5.iterrows():
                logging.info(f"\n  #{idx + 1}")
                logging.info(f"    Test ID: {row['test_id']}")
                logging.info(f"    Avg Combined Score: {row['avg_combined_score']:.3f}")
                logging.info(f"    Match Rate: {row['match_rate']:.2%}")
                logging.info(f"    Distance Metric: {row['distance_metric']}")
                logging.info(f"    Min Matches: {row['min_matches']}")
                logging.info(f"    Combined Weights: graph={row['combined_weight_graph']:.2f}, name={row['combined_weight_name']:.2f}, desc={row['combined_weight_description']:.2f}")
        
        return summary_df
    
    def close(self):
        """Close Neo4j connection."""
        if self.controller:
            self.controller.close_connection()


def main():
    """
    Custom grid search configuration.
    EDIT THE PARAMETERS BELOW TO TEST YOUR SPECIFIC COMBINATIONS.
    """
    from dotenv import load_dotenv
    load_dotenv()
    
    logging.info("="*80)
    logging.info("  CUSTOM GRID SEARCH FOR SIMILARITY ANALYSIS")
    logging.info("="*80)
    
    # Initialize grid search
    grid_search = SimilarityGridSearch()
    
    # ============================================================
    # CUSTOMIZE YOUR PARAMETERS HERE
    # ============================================================
    
    # Example 1: Test different distance metrics
    param_grid = grid_search.define_parameter_grid(
        stores=[("eroski", "makro")],
        min_matches_values=[3,4,5],  # Test different minimum match thresholds
        distance_metrics=["cosine", "euclidean"],  # All metrics
        combined_weight_configs=[
            {"graph": 0.6, "name": 0.3, "description": 0.1},  # More graph weight
            {"graph": 0.4, "name": 0.2, "description": 0.2},
            {"graph": 0.3, "name": 0.4, "description": 0.3}
        ],
        weight_configs=[
            {
                "Brand": 0.20,
                "Format": 0.10,
                "Product_type": 0.70,
                "Unit_measure": 0.10,
                "First_level_ingredient": 0.20,
                "Second_level_ingredient": 0.10,
                "Allergen": 0.05
            },
            {  
                "Brand": 0.35,
                "Format": 0.25,
                "Product_type": 0.6,
                "Unit_measure": 0.1,
                "First_level_ingredient": 0.3,
                "Second_level_ingredient": 0.2,
                "Allergen": 0.05
            },
                {  # More weight on product type
                    "Brand": 0.15,
                    "Format": 0.05,
                    "Product_type": 0.7,
                    "Unit_measure": 0.05,
                    "First_level_ingredient": 0.25,
                    "Second_level_ingredient": 0.15,
                    "Allergen": 0.05
                }
        ],
        limit_products=None,  # Test with 50 products (ignored if use_external_ids=True)
        max_workers_values=[100],
        max_embedding_workers_values=[100],
        score_threshold_values=[0.05, 0.10],
        use_external_ids=True,  # Set to True to use file with specific IDs
        external_ids_path='2_eroski_id2.txt'  # Your ID file
    )
    
    # Example 2: Test different graph attribute weights
    # Uncomment to use this configuration instead:
    # param_grid = grid_search.define_parameter_grid(
    #     stores=[("eroski", "makro")],
    #     min_matches_values=[3],
    #     distance_metrics=["cosine"],
    #     weight_configs=[
    #         {  # Balanced
    #             "Brand": 0.3,
    #             "Format": 0.15,
    #             "Product_type": 0.6,
    #             "Unit_measure": 0.1,
    #             "First_level_ingredient": 0.2,
    #             "Second_level_ingredient": 0.1,
    #             "Allergen": 0.05
    #         },
    #         {  # Brand-heavy
    #             "Brand": 0.5,
    #             "Format": 0.1,
    #             "Product_type": 0.4,
    #             "Unit_measure": 0.1,
    #             "First_level_ingredient": 0.15,
    #             "Second_level_ingredient": 0.05,
    #             "Allergen": 0.05
    #         },
    #         {  # Product type heavy
    #             "Brand": 0.2,
    #             "Format": 0.1,
    #             "Product_type": 0.8,
    #             "Unit_measure": 0.1,
    #             "First_level_ingredient": 0.2,
    #             "Second_level_ingredient": 0.1,
    #             "Allergen": 0.05
    #         }
    #     ],
    #     combined_weight_configs=[
    #         {"graph": 0.4, "name": 0.4, "description": 0.2}
    #     ],
    #     use_external_ids=True,
    #     external_ids_path='2_eroski_id_2.txt'
    # )
    
    # ============================================================
    # RUN THE GRID SEARCH
    # ============================================================
    
    results_df = grid_search.run_grid_search(param_grid=param_grid)
    
    # Display summary statistics
    logging.info("\n📊 Summary Statistics:")
    logging.info(f"   Total configurations tested: {len(results_df)}")
    
    if results_df[results_df['success']].shape[0] > 0:
        successful = results_df[results_df['success']]
        logging.info(f"\n   Successful tests: {len(successful)}")
        logging.info(f"   Average match rate: {successful['match_rate'].mean():.2%}")
        logging.info(f"   Average combined score: {successful['avg_combined_score'].mean():.3f}")
        logging.info(f"   Average execution time: {successful['execution_time_seconds'].mean():.2f}s")
        logging.info(f"\n   Best match rate: {successful['match_rate'].max():.2%}")
        logging.info(f"   Best combined score: {successful['avg_combined_score'].max():.3f}")
    
    # Close connections
    grid_search.close()
    
    logging.info("\n✅ Custom grid search completed. Check the output directory for detailed results.")


if __name__ == "__main__":
    main()
