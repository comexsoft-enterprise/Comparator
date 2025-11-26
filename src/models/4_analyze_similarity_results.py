"""
Analyze Similarity Results

This script compares test results from the similarity algorithm with ground truth data
to evaluate the performance of product matching.

Usage:
    Edit the parameters in the main() function and run:
    python analyze_similarity_results.py

Author: Your Name
"""

import os
import sys
import csv
import logging
from pathlib import Path
from typing import Dict, Set, List, Tuple
from collections import defaultdict

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configure logging
from config.logs import setup_logging
setup_logging(level=logging.INFO)

from config.settings import PROJECT_ROOT


class SimilarityAnalyzer:
    """Analyze similarity test results against ground truth."""
    
    def __init__(self):
        """Initialize the analyzer."""
        self.ground_truth = {}  # {siid_a: siid_b}
        self.test_results = {}  # {siid_a: [(siid_b, rank), ...]}
        
    def load_ground_truth(self, file_path: str) -> int:
        """
        Load ground truth CSV file.
        
        Args:
            file_path: Path to ground truth CSV file
            
        Returns:
            Number of pairs loaded
        """
        self.ground_truth = {}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter=';')
                
                for row in reader:
                    siid_a = row.get('siid_a', '').strip()
                    siid_b = row.get('siid_b', '').strip()
                    
                    if siid_a and siid_b:
                        self.ground_truth[siid_a] = siid_b
            
            logging.info(f"✅ Loaded {len(self.ground_truth)} ground truth pairs from {file_path}")
            return len(self.ground_truth)
            
        except FileNotFoundError:
            logging.error(f"❌ File not found: {file_path}")
            return 0
        except Exception as e:
            logging.error(f"❌ Error reading ground truth file: {e}")
            return 0
    
    def load_test_results(self, file_path: str) -> int:
        """
        Load test results CSV file (with ranks).
        
        Args:
            file_path: Path to test results CSV file
            
        Returns:
            Number of test pairs loaded
        """
        self.test_results = defaultdict(list)
        total_pairs = 0
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter=';')
                
                for row in reader:
                    siid_a = row.get('siid_a', '').strip()
                    siid_b = row.get('siid_b', '').strip()
                    rank = row.get('rank', '').strip()
                    
                    if siid_a and siid_b:
                        try:
                            rank_int = int(rank) if rank else 999
                        except ValueError:
                            rank_int = 999
                        
                        self.test_results[siid_a].append((siid_b, rank_int))
                        total_pairs += 1
            
            logging.info(f"✅ Loaded {total_pairs} test result pairs from {file_path}")
            logging.info(f"   Covering {len(self.test_results)} unique products")
            return total_pairs
            
        except FileNotFoundError:
            logging.error(f"❌ File not found: {file_path}")
            return 0
        except Exception as e:
            logging.error(f"❌ Error reading test results file: {e}")
            return 0
    
    def analyze_performance(self) -> Dict:
        """
        Analyze test results against ground truth.
        
        Returns:
            Dictionary with analysis metrics
        """
        metrics = {
            'total_ground_truth': len(self.ground_truth),
            'total_test_products': len(self.test_results),
            'rank_1_correct': 0,
            'rank_2_correct': 0,
            'rank_3_correct': 0,
            'top_3_correct': 0,
            'not_in_top_3': 0,
            'not_tested': 0,
            'no_ground_truth': 0,
            'detailed_results': []
        }
        
        # Check each ground truth pair
        for siid_a, true_siid_b in self.ground_truth.items():
            if siid_a not in self.test_results:
                # Product was not tested
                metrics['not_tested'] += 1
                metrics['detailed_results'].append({
                    'siid_a': siid_a,
                    'true_siid_b': true_siid_b,
                    'predicted_siid_b': None,
                    'rank': None,
                    'status': 'NOT_TESTED'
                })
                continue
            
            # Get predictions for this product
            predictions = self.test_results[siid_a]
            
            # Check if true match is in predictions
            found = False
            found_rank = None
            
            for pred_siid_b, rank in predictions:
                if pred_siid_b == true_siid_b:
                    found = True
                    found_rank = rank
                    
                    if rank == 1:
                        metrics['rank_1_correct'] += 1
                    elif rank == 2:
                        metrics['rank_2_correct'] += 1
                    elif rank == 3:
                        metrics['rank_3_correct'] += 1
                    
                    if rank <= 3:
                        metrics['top_3_correct'] += 1
                    
                    metrics['detailed_results'].append({
                        'siid_a': siid_a,
                        'true_siid_b': true_siid_b,
                        'predicted_siid_b': pred_siid_b,
                        'rank': rank,
                        'status': f'CORRECT_RANK_{rank}'
                    })
                    break
            
            if not found:
                # True match not in top 3
                metrics['not_in_top_3'] += 1
                
                # Get what was predicted as rank 1
                predicted_rank_1 = predictions[0][0] if predictions else None
                
                metrics['detailed_results'].append({
                    'siid_a': siid_a,
                    'true_siid_b': true_siid_b,
                    'predicted_siid_b': predicted_rank_1,
                    'rank': None,
                    'status': 'NOT_IN_TOP_3'
                })
        
        # Check for products in test results but not in ground truth
        for siid_a in self.test_results:
            if siid_a not in self.ground_truth:
                metrics['no_ground_truth'] += 1
        
        # Calculate percentages
        tested_count = metrics['total_ground_truth'] - metrics['not_tested']
        if tested_count > 0:
            metrics['rank_1_accuracy'] = metrics['rank_1_correct'] / tested_count * 100
            metrics['rank_2_accuracy'] = metrics['rank_2_correct'] / tested_count * 100
            metrics['rank_3_accuracy'] = metrics['rank_3_correct'] / tested_count * 100
            metrics['top_3_accuracy'] = metrics['top_3_correct'] / tested_count * 100
        else:
            metrics['rank_1_accuracy'] = 0
            metrics['rank_2_accuracy'] = 0
            metrics['rank_3_accuracy'] = 0
            metrics['top_3_accuracy'] = 0
        
        return metrics
    
    def print_summary(self, metrics: Dict):
        """
        Print summary of analysis results.
        
        Args:
            metrics: Dictionary with analysis metrics
        """
        print("\n" + "="*70)
        print("  SIMILARITY ANALYSIS SUMMARY")
        print("="*70)
        
        print(f"\nGround Truth Dataset:")
        print(f"  Total pairs: {metrics['total_ground_truth']}")
        
        print(f"\nTest Results Dataset:")
        print(f"  Products tested: {metrics['total_test_products']}")
        print(f"  Products in ground truth: {metrics['total_ground_truth'] - metrics['not_tested']}")
        print(f"  Products NOT in ground truth: {metrics['no_ground_truth']}")
        
        tested = metrics['total_ground_truth'] - metrics['not_tested']
        
        print(f"\n📊 Performance Metrics (on {tested} tested products):")
        print(f"  ✅ Rank 1 correct:  {metrics['rank_1_correct']:4d} ({metrics['rank_1_accuracy']:.1f}%)")
        print(f"  ✅ Rank 2 correct:  {metrics['rank_2_correct']:4d} ({metrics['rank_2_accuracy']:.1f}%)")
        print(f"  ✅ Rank 3 correct:  {metrics['rank_3_correct']:4d} ({metrics['rank_3_accuracy']:.1f}%)")
        print(f"  {'─'*50}")
        print(f"  ✅ Top-3 accuracy:  {metrics['top_3_correct']:4d} ({metrics['top_3_accuracy']:.1f}%)")
        print(f"  ❌ Not in top 3:    {metrics['not_in_top_3']:4d} ({metrics['not_in_top_3']/tested*100:.1f}%)")
        
        if metrics['not_tested'] > 0:
            print(f"\n⚠️  Not tested:       {metrics['not_tested']:4d}")
        
        print("="*70 + "\n")
    
    def export_detailed_results(self, metrics: Dict, output_path: str):
        """
        Export detailed analysis results to CSV (excluding NOT_TESTED).
        
        Args:
            metrics: Dictionary with analysis metrics
            output_path: Path to output CSV file
        """
        try:
            # Filter out NOT_TESTED entries
            tested_results = [r for r in metrics['detailed_results'] 
                            if r['status'] != 'NOT_TESTED']
            
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')
                
                # Write header
                writer.writerow([
                    'siid_a',
                    'true_siid_b',
                    'predicted_siid_b',
                    'rank',
                    'status'
                ])
                
                # Write data
                for result in tested_results:
                    writer.writerow([
                        result['siid_a'],
                        result['true_siid_b'],
                        result.get('predicted_siid_b', ''),
                        result.get('rank', ''),
                        result['status']
                    ])
            
            logging.info(f"✅ Detailed results exported to: {output_path}")
            logging.info(f"   Rows exported: {len(tested_results)} (NOT_TESTED excluded)")
            
        except Exception as e:
            logging.error(f"❌ Error exporting detailed results: {e}")
    
    def export_errors(self, metrics: Dict, output_path: str):
        """
        Export only errors (not in top 3) to CSV (excluding NOT_TESTED).
        
        Args:
            metrics: Dictionary with analysis metrics
            output_path: Path to output CSV file
        """
        try:
            # Only include NOT_IN_TOP_3, exclude NOT_TESTED
            errors = [r for r in metrics['detailed_results'] 
                     if r['status'] == 'NOT_IN_TOP_3']
            
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')
                
                # Write header
                writer.writerow([
                    'siid_a',
                    'true_siid_b',
                    'predicted_siid_b',
                    'status'
                ])
                
                # Write data
                for result in errors:
                    writer.writerow([
                        result['siid_a'],
                        result['true_siid_b'],
                        result.get('predicted_siid_b', ''),
                        result['status']
                    ])
            
            logging.info(f"✅ Errors exported to: {output_path}")
            logging.info(f"   Total errors: {len(errors)}")
            
        except Exception as e:
            logging.error(f"❌ Error exporting errors: {e}")


def main():
    """Main function."""
    # ============================================================
    # CONFIGURATION - Edit these parameters directly
    # ============================================================
    ground_truth_file = '1_verification_results_both_exist.csv'  # Real pairs CSV
    grid_search_results_dir = PROJECT_ROOT / "data" / "grid_search_results"  # Directory with test results
    analysis_output_dir = PROJECT_ROOT / "data" / "grid_search_results" / "analysis"  # Output directory for analysis
    
    print("="*70)
    print("  SIMILARITY RESULTS ANALYZER - BATCH MODE")
    print("="*70)
    
    # Resolve ground truth path
    if not os.path.isabs(ground_truth_file):
        ground_truth_path = project_root / "src" / "models" / ground_truth_file
    else:
        ground_truth_path = Path(ground_truth_file)
    
    # Check if ground truth exists
    if not ground_truth_path.exists():
        logging.error(f"❌ Ground truth file not found: {ground_truth_path}")
        return
    
    # Find all SIID CSV files in grid search results directory
    if not grid_search_results_dir.exists():
        logging.error(f"❌ Grid search results directory not found: {grid_search_results_dir}")
        return
    
    siid_files = list(grid_search_results_dir.glob("siid_*.csv"))
    
    if not siid_files:
        logging.error(f"❌ No SIID CSV files found in: {grid_search_results_dir}")
        logging.info("   Looking for files matching pattern: siid_*.csv")
        return
    
    print(f"\n📂 Found {len(siid_files)} SIID test result files")
    print(f"📂 Ground truth: {ground_truth_path.name}")
    print(f"📂 Output directory: {analysis_output_dir}")
    
    # Create output directory
    analysis_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Process each SIID file
    all_results_summary = []
    
    for idx, test_results_path in enumerate(sorted(siid_files), 1):
        print(f"\n{'='*70}")
        print(f"  [{idx}/{len(siid_files)}] Analyzing: {test_results_path.name}")
        print(f"{'='*70}")
        
        # Initialize analyzer
        analyzer = SimilarityAnalyzer()
        
        # Load ground truth
        print(f"\n📂 Loading ground truth from: {ground_truth_path.name}")
        gt_count = analyzer.load_ground_truth(str(ground_truth_path))
        
        if gt_count == 0:
            logging.error("No ground truth data loaded. Skipping this file.")
            continue
        
        # Load test results
        print(f"📂 Loading test results from: {test_results_path.name}")
        test_count = analyzer.load_test_results(str(test_results_path))
        
        if test_count == 0:
            logging.error("No test results loaded. Skipping this file.")
            continue
        
        # Analyze performance
        print("\n🔍 Analyzing performance...")
        metrics = analyzer.analyze_performance()
        
        # Print summary
        analyzer.print_summary(metrics)
        
        # Generate output filenames based on test file name
        base_name = test_results_path.stem  # filename without extension
        detailed_output_path = analysis_output_dir / f"analysis_{base_name}_detailed.csv"
        errors_output_path = analysis_output_dir / f"analysis_{base_name}_errors.csv"
        
        # Export detailed results
        analyzer.export_detailed_results(metrics, str(detailed_output_path))
        
        # Export errors
        analyzer.export_errors(metrics, str(errors_output_path))
        
        # Store summary for comparison
        tested = metrics['total_ground_truth'] - metrics['not_tested']
        all_results_summary.append({
            'test_file': test_results_path.name,
            'total_ground_truth': metrics['total_ground_truth'],
            'total_tested': tested,
            'rank_1_correct': metrics['rank_1_correct'],
            'rank_1_accuracy': metrics['rank_1_accuracy'],
            'rank_2_correct': metrics['rank_2_correct'],
            'rank_2_accuracy': metrics['rank_2_accuracy'],
            'rank_3_correct': metrics['rank_3_correct'],
            'rank_3_accuracy': metrics['rank_3_accuracy'],
            'top_3_correct': metrics['top_3_correct'],
            'top_3_accuracy': metrics['top_3_accuracy'],
            'not_in_top_3': metrics['not_in_top_3'],
            'not_tested': metrics['not_tested'],
            'detailed_output': detailed_output_path.name,
            'errors_output': errors_output_path.name
        })
        
        print(f"\n✅ Analysis complete for: {test_results_path.name}")
        print(f"   Detailed results: {detailed_output_path.name}")
        print(f"   Errors only: {errors_output_path.name}")
    
    # Export comparison summary
    if all_results_summary:
        print(f"\n{'='*70}")
        print(f"  GENERATING COMPARISON SUMMARY")
        print(f"{'='*70}")
        
        summary_path = analysis_output_dir / "analysis_comparison_summary.csv"
        
        try:
            with open(summary_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=all_results_summary[0].keys(), delimiter=';')
                writer.writeheader()
                writer.writerows(all_results_summary)
            
            logging.info(f"\n✅ Comparison summary exported to: {summary_path.name}")
            
            # Print top 3 best performers
            sorted_by_top3 = sorted(all_results_summary, key=lambda x: x['top_3_accuracy'], reverse=True)
            print(f"\n🏆 Top 3 Best Performers (by Top-3 Accuracy):")
            for i, result in enumerate(sorted_by_top3[:3], 1):
                print(f"\n  #{i}: {result['test_file']}")
                print(f"      Top-3 Accuracy: {result['top_3_accuracy']:.1f}%")
                print(f"      Rank 1: {result['rank_1_accuracy']:.1f}%")
                print(f"      Rank 2: {result['rank_2_accuracy']:.1f}%")
                print(f"      Rank 3: {result['rank_3_accuracy']:.1f}%")
            
        except Exception as e:
            logging.error(f"❌ Error creating comparison summary: {e}")
    
    print(f"\n{'='*70}")
    print(f"✅ BATCH ANALYSIS COMPLETE!")
    print(f"   Analyzed {len(all_results_summary)} test files")
    print(f"   Results saved to: {analysis_output_dir}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
