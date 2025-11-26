"""
Verify Product IDs in Neo4j Database

This script reads product IDs from a file (two columns) and verifies if they exist
in the Neo4j database for specified supermarkets.

Usage:
    Edit the parameters in the main() function and run:
    python verify_product_ids.py

Author: Your Name
"""

import os
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configure logging
from config.logs import setup_logging
setup_logging(level=logging.INFO)

from src.connectors.neo4j_connector import get_neo4j_driver

# Neo4j configuration
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


class ProductIDVerifier:
    """Verify product IDs exist in Neo4j database."""
    
    def __init__(self):
        """Initialize the verifier."""
        self.driver = None
        
    def connect_to_neo4j(self) -> bool:
        """Establish connection to Neo4j database."""
        try:
            self.driver = get_neo4j_driver(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)
            logging.info("✅ Connected to Neo4j database")
            return True
        except Exception as e:
            logging.error(f"❌ Failed to connect to Neo4j: {e}")
            return False
    
    def close_connection(self):
        """Close the Neo4j driver connection."""
        if self.driver:
            self.driver.close()
            logging.info("🔌 Neo4j connection closed")
    
    def read_id_file(self, file_path: str) -> List[Tuple[str, str]]:
        """
        Read product IDs from a file with two columns (tab or space separated).
        
        Args:
            file_path: Path to the file containing product IDs
            
        Returns:
            List of tuples (id1, id2)
        """
        id_pairs = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Split by tab or whitespace
                    parts = line.split()
                    if len(parts) != 2:
                        logging.warning(f"Line {line_num}: Expected 2 columns, got {len(parts)}. Skipping.")
                        continue
                    
                    id_pairs.append((parts[0], parts[1]))
            
            logging.info(f"📄 Read {len(id_pairs)} ID pairs from {file_path}")
            return id_pairs
            
        except FileNotFoundError:
            logging.error(f"❌ File not found: {file_path}")
            return []
        except Exception as e:
            logging.error(f"❌ Error reading file: {e}")
            return []
    
    def verify_product_exists(self, product_id: str, store_name: str = None) -> Dict[str, Any]:
        """
        Verify if a product ID exists in the Neo4j database.
        
        Args:
            product_id: Product ID to verify
            store_name: Optional store name to filter by
            
        Returns:
            Dictionary with verification results
        """
        if not self.driver:
            return {
                'id': product_id,
                'exists': False,
                'error': 'No database connection'
            }
        
        try:
            # Build query based on whether store is specified
            if store_name:
                query = """
                MATCH (p:Product {siid: $product_id})-[:SELLS]-(s:Store {name: $store_name})
                RETURN p.siid AS id,
                       p.product_name AS product_name,
                       p.price AS price,
                       s.name AS store
                LIMIT 1
                """
                params = {"product_id": product_id, "store_name": store_name}
            else:
                query = """
                MATCH (p:Product {siid: $product_id})
                OPTIONAL MATCH (p)-[:SELLS]-(s:Store)
                RETURN p.siid AS id,
                       p.product_name AS product_name,
                       p.price AS price,
                       s.name AS store
                LIMIT 1
                """
                params = {"product_id": product_id}
            
            with self.driver.session(database=NEO4J_DATABASE) as session:
                # Print the actual query with real values substituted for debugging
                query_with_values = query
                for key, value in params.items():
                    if isinstance(value, str):
                        query_with_values = query_with_values.replace(f"${key}", f"'{value}'")
                    else:
                        query_with_values = query_with_values.replace(f"${key}", str(value))
                
                logging.info(f"🔍 Executing query with real values:")
                logging.info(f"{query_with_values.strip()}")
                
                result = session.run(query, params)
                record = result.single()
                
                if record:
                    return {
                        'id': product_id,
                        'exists': True,
                        'product_name': record.get('product_name'),
                        'price': record.get('price'),
                        'store': record.get('store'),
                        'error': None
                    }
                else:
                    return {
                        'id': product_id,
                        'exists': False,
                        'error': f'Product not found{" in store " + store_name if store_name else ""}'
                    }
                    
        except Exception as e:
            return {
                'id': product_id,
                'exists': False,
                'error': str(e)
            }
    
    def verify_id_pairs(
        self, 
        id_pairs: List[Tuple[str, str]], 
        store1: str = None, 
        store2: str = None
    ) -> Dict[str, Any]:
        """
        Verify all ID pairs from the file.
        
        Args:
            id_pairs: List of (id1, id2) tuples
            store1: Store name for first column IDs
            store2: Store name for second column IDs
            
        Returns:
            Dictionary with verification results and statistics
        """
        results = []
        stats = {
            'total_pairs': len(id_pairs),
            'both_exist': 0,
            'only_first_exists': 0,
            'only_second_exists': 0,
            'neither_exists': 0,
            'errors': 0
        }
        
        logging.info(f"\n🔍 Verifying {len(id_pairs)} ID pairs...")
        if store1:
            logging.info(f"   First column: {store1} store")
        if store2:
            logging.info(f"   Second column: {store2} store")
        
        for idx, (id1, id2) in enumerate(id_pairs, 1):
            if idx % 100 == 0:
                logging.info(f"   Progress: {idx}/{len(id_pairs)} pairs verified...")
            
            # Format IDs with store prefix (e.g., "eroski_1404")
            formatted_id1 = f"{store1}_01013-{id1}" if store1 else id1
            formatted_id2 = f"{store2}_01013-{id2}" if store2 else id2
            
            # Verify first ID
            result1 = self.verify_product_exists(formatted_id1, store1)
            
            # Verify second ID
            result2 = self.verify_product_exists(formatted_id2, store2)
            
            # Compile results
            pair_result = {
                'pair_number': idx,
                'id1': id1,
                'id1_exists': result1['exists'],
                'id1_store': result1.get('store'),
                'id1_product_name': result1.get('product_name'),
                'id1_error': result1.get('error'),
                'id2': id2,
                'id2_exists': result2['exists'],
                'id2_store': result2.get('store'),
                'id2_product_name': result2.get('product_name'),
                'id2_error': result2.get('error'),
            }
            
            results.append(pair_result)
            
            # Update statistics
            if result1['exists'] and result2['exists']:
                stats['both_exist'] += 1
            elif result1['exists']:
                stats['only_first_exists'] += 1
            elif result2['exists']:
                stats['only_second_exists'] += 1
            else:
                stats['neither_exists'] += 1
            
            if result1.get('error') or result2.get('error'):
                stats['errors'] += 1
        
        return {
            'results': results,
            'stats': stats
        }
    
    
    def export_both_exist_to_csv(self, verification_data: Dict[str, Any], output_path: str):
        """
        Export only the ID pairs where both IDs exist in their respective stores.
        
        Args:
            verification_data: Results from verify_id_pairs()
            output_path: Path to output CSV file
        """
        import csv
        
        try:
            # Filter results to only include pairs where both exist
            both_exist = [r for r in verification_data['results'] if r['id1_exists'] and r['id2_exists']]
            
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=';')
                
                # Write header
                writer.writerow([
                    'Pair #',
                    'siid_a', 'ID 1 Store', 'ID 1 Product Name',
                    'siid_b', 'ID 2 Store', 'ID 2 Product Name'
                ])
                
                # Write data
                for result in both_exist:
                    writer.writerow([
                        result['pair_number'],
                        result['id1'],
                        result['id1_store'] or '',
                        result['id1_product_name'] or '',
                        result['id2'],
                        result['id2_store'] or '',
                        result['id2_product_name'] or ''
                    ])
            
            logging.info(f"✅ Both-exist pairs exported to: {output_path}")
            logging.info(f"   Total pairs where both exist: {len(both_exist)}")
            
        except Exception as e:
            logging.error(f"❌ Error exporting both-exist results: {e}")
    
    def print_summary(self, stats: Dict[str, Any], store1: str = None, store2: str = None):
        """
        Print summary statistics.
        
        Args:
            stats: Statistics dictionary from verify_id_pairs()
            store1: Store name for first column
            store2: Store name for second column
        """
        print("\n" + "="*70)
        print("  VERIFICATION SUMMARY")
        print("="*70)
        
        if store1 or store2:
            print(f"\nStores:")
            if store1:
                print(f"  Column 1: {store1}")
            if store2:
                print(f"  Column 2: {store2}")
        
        print(f"\nTotal ID pairs: {stats['total_pairs']}")
        print(f"\nResults:")
        print(f"  ✅ Both IDs exist:        {stats['both_exist']:4d} ({stats['both_exist']/stats['total_pairs']*100:.1f}%)")
        print(f"  ⚠️  Only first ID exists:  {stats['only_first_exists']:4d} ({stats['only_first_exists']/stats['total_pairs']*100:.1f}%)")
        print(f"  ⚠️  Only second ID exists: {stats['only_second_exists']:4d} ({stats['only_second_exists']/stats['total_pairs']*100:.1f}%)")
        print(f"  ❌ Neither ID exists:     {stats['neither_exists']:4d} ({stats['neither_exists']/stats['total_pairs']*100:.1f}%)")
        
        if stats['errors'] > 0:
            print(f"\n  ⚠️  Errors encountered:    {stats['errors']}")
        
        print("="*70 + "\n")


def main():
    """Main function."""
    # ============================================================
    # CONFIGURATION - Edit these parameters directly
    # ============================================================
    file_name = '0_eroski_makro_id.txt'  # File with product IDs (two columns)
    store1 = 'eroski'                   # Store name for first column
    store2 = 'makro'                    # Store name for second column
    output_file = '1_verification_results.csv'  # Output CSV file
    
    # Load environment variables
    load_dotenv()
    
    # Initialize verifier
    verifier = ProductIDVerifier()
    
    # Connect to Neo4j
    if not verifier.connect_to_neo4j():
        logging.error("Failed to connect to Neo4j. Exiting.")
        return
    
    try:
        # Resolve file path
        if not os.path.isabs(file_name):
            file_path = project_root / "src" / "models" / file_name
        else:
            file_path = Path(file_name)
        
        # Read ID pairs from file
        id_pairs = verifier.read_id_file(str(file_path))
        
        if not id_pairs:
            logging.error("No ID pairs to verify. Exiting.")
            return
        
        # Verify all ID pairs
        verification_data = verifier.verify_id_pairs(
            id_pairs=id_pairs,
            store1=store1,
            store2=store2
        )
        
        # Print summary
        verifier.print_summary(
            verification_data['stats'],
            store1=store1,
            store2=store2
        )
    
        
        # Export filtered results (only pairs where both exist)
        both_exist_file = output_file.replace('.csv', '_both_exist.csv')
        if not os.path.isabs(both_exist_file):
            both_exist_path = project_root / "src" / "models" / both_exist_file
        else:
            both_exist_path = Path(both_exist_file)
        
        verifier.export_both_exist_to_csv(verification_data, str(both_exist_path))
        
    finally:
        # Close connection
        verifier.close_connection()


if __name__ == "__main__":
    main()
