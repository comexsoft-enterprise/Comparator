"""
Generate Embeddings and Store in Neo4j Product Nodes

This script generates embeddings for product names and descriptions using Azure OpenAI
and stores them directly as properties in the Product nodes in Neo4j.

Usage:
    from src.models.generate_embeddings_to_neo4j import EmbeddingGenerator
    
    generator = EmbeddingGenerator()
    generator.connect_to_neo4j()
    generator.process_store_embeddings(store_name="eroski", limit=100)
    generator.close_connection()
"""

import logging
import os
import sys
import json
from pathlib import Path
from typing import List, Dict, Any
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configure logging
from config.logs import setup_logging
setup_logging(level=logging.INFO)

from config.settings import PROJECT_ROOT
from src.connectors.neo4j_connector import get_neo4j_driver

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = "text-embedding-3-large"


class EmbeddingGenerator:
    """
    Generate embeddings for products and store them in Neo4j.
    """
    
    def __init__(self):
        self.driver = None
        self.openai_client = None
        self.azure_deployment = AZURE_OPENAI_DEPLOYMENT
        self.cache_lock = Lock()
        
        # Initialize OpenAI client
        if AZURE_OPENAI_API_KEY:
            self.openai_client = OpenAI(
                base_url=AZURE_OPENAI_ENDPOINT,
                api_key=AZURE_OPENAI_API_KEY
            )
            logging.info(f"✅ Azure OpenAI client initialized")
            logging.info(f"   Endpoint: {AZURE_OPENAI_ENDPOINT}")
            logging.info(f"   Deployment: {self.azure_deployment}")
        else:
            raise ValueError("❌ AZURE_OPENAI_API_KEY not found in environment")
    
    def connect_to_neo4j(self) -> bool:
        """Establish connection to Neo4j database."""
        try:
            self.driver = get_neo4j_driver(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)
            if self.driver:
                logging.info("✅ Successfully connected to Neo4j database")
                return True
            logging.error("❌ Failed to connect to Neo4j database")
            return False
        except Exception as e:
            logging.error(f"❌ Error connecting to Neo4j: {e}")
            return False
    
    def close_connection(self):
        """Close the Neo4j driver connection."""
        if self.driver:
            self.driver.close()
            logging.info("🔌 Neo4j connection closed")
    
    def execute_query(self, query: str, parameters: dict = None) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return results."""
        if not self.driver:
            logging.error("Driver not initialized")
            return []
        try:
            with self.driver.session(database=NEO4J_DATABASE) as session:
                result = session.run(query, parameters or {})
                return [dict(record) for record in result]
        except Exception as e:
            logging.error(f"Error executing query: {e}")
            return []
    
    def get_products_from_store(self, store_name: str, limit: int = None) -> List[Dict[str, Any]]:
        """
        Get all products from a specific store.
        
        Args:
            store_name: Name of the store (e.g., "eroski", "makro")
            limit: Optional limit on number of products
            
        Returns:
            List of product dictionaries with id, product_name, description
        """
        query = """
        MATCH (p:Product)<-[:SELLS]-(s:Store {name: $store_name})
        RETURN p.id AS id,
               p.siid AS siid,
               p.product_name AS product_name,
               p.description AS description
        """
        
        if limit:
            query += f" LIMIT {int(limit)}"
        
        logging.info(f"📋 Fetching products from store: {store_name}")
        products = self.execute_query(query, {"store_name": store_name})
        logging.info(f"✅ Found {len(products)} products in store '{store_name}'")
        
        return products
    
    def generate_single_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text using Azure OpenAI.
        
        Args:
            text: Text to generate embedding for
            
        Returns:
            Embedding vector as list of floats
        """
        if not text or not text.strip():
            return None
        
        try:
            response = self.openai_client.embeddings.create(
                model=self.azure_deployment,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logging.error(f"Error generating embedding: {e}")
            return None
    
    def generate_embeddings_parallel(self, texts: List[str], max_workers: int = 100) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in parallel.
        
        Args:
            texts: List of texts to generate embeddings for
            max_workers: Maximum number of parallel workers
            
        Returns:
            List of embedding vectors
        """
        embeddings = [None] * len(texts)
        
        def generate_with_index(idx: int, text: str):
            """Helper function to generate embedding with index tracking"""
            if not text or not text.strip():
                return idx, None
            
            try:
                response = self.openai_client.embeddings.create(
                    model=self.azure_deployment,
                    input=text
                )
                return idx, response.data[0].embedding
            except Exception as e:
                logging.error(f"Error generating embedding for index {idx}: {e}")
                return idx, None
        
        logging.info(f"🤖 Generating {len(texts)} embeddings with {max_workers} parallel workers...")
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(generate_with_index, i, text): i
                for i, text in enumerate(texts)
            }
            
            # Progress tracking
            completed = 0
            total = len(texts)
            
            # Collect results as they complete
            for future in as_completed(futures):
                try:
                    idx, embedding = future.result()
                    embeddings[idx] = embedding
                    completed += 1
                    
                    # Progress update every 10% or every 100 items
                    if completed % max(1, total // 10) == 0 or completed % 100 == 0:
                        pct = (completed / total) * 100
                        elapsed = time.time() - start_time
                        rate = completed / elapsed if elapsed > 0 else 0
                        logging.info(f"   Progress: {completed}/{total} ({pct:.1f}%) - {rate:.1f} embeddings/s")
                
                except Exception as e:
                    logging.error(f"Error processing future: {e}")
        
        elapsed_time = time.time() - start_time
        successful = sum(1 for e in embeddings if e is not None)
        logging.info(f"✅ Generated {successful}/{len(texts)} embeddings in {elapsed_time:.2f}s ({successful/elapsed_time:.1f} embeddings/s)")
        
        return embeddings
    
    def update_product_embeddings(self, product_id: int, name_embedding: List[float], desc_embedding: List[float]) -> bool:
        """
        Update a product node with name and description embeddings.
        
        Args:
            product_id: Product ID
            name_embedding: Embedding vector for product name
            desc_embedding: Embedding vector for product description
            
        Returns:
            True if successful, False otherwise
        """
        if not self.driver:
            logging.error("Driver not initialized")
            return False
        
        try:
            with self.driver.session(database=NEO4J_DATABASE) as session:
                # Convert embeddings to JSON strings for storage
                query = """
                MATCH (p:Product {id: $product_id})
                SET p.name_embedding = $name_embedding,
                    p.description_embedding = $desc_embedding,
                    p.embeddings_updated_at = datetime()
                RETURN p.id AS id
                """
                
                params = {
                    "product_id": product_id,
                    "name_embedding": json.dumps(name_embedding) if name_embedding else None,
                    "desc_embedding": json.dumps(desc_embedding) if desc_embedding else None
                }
                
                result = session.run(query, params)
                return result.single() is not None
        
        except Exception as e:
            logging.error(f"Error updating product {product_id}: {e}")
            return False
    
    def process_store_embeddings(self, store_name: str, limit: int = None, max_workers: int = 100, batch_size: int = 100):
        """
        Generate and store embeddings for all products in a store.
        
        Args:
            store_name: Name of the store
            limit: Optional limit on number of products to process
            max_workers: Maximum number of parallel workers for API calls
            batch_size: Number of products to process in each batch
        """
        logging.info(f"\n{'='*70}")
        logging.info(f"🚀 Starting embedding generation for store: {store_name}")
        logging.info(f"{'='*70}\n")
        
        # Get products from store
        products = self.get_products_from_store(store_name, limit)
        
        if not products:
            logging.warning(f"⚠️ No products found in store '{store_name}'")
            return
        
        total_products = len(products)
        logging.info(f"📊 Processing {total_products} products in batches of {batch_size}")
        
        # Process in batches
        successful_updates = 0
        failed_updates = 0
        
        for batch_start in range(0, total_products, batch_size):
            batch_end = min(batch_start + batch_size, total_products)
            batch_products = products[batch_start:batch_end]
            batch_num = (batch_start // batch_size) + 1
            total_batches = (total_products + batch_size - 1) // batch_size
            
            logging.info(f"\n📦 Processing batch {batch_num}/{total_batches} (products {batch_start+1}-{batch_end})")
            
            # Collect all texts for this batch
            names = []
            descriptions = []
            
            for product in batch_products:
                names.append(product.get('product_name', '') or '')
                descriptions.append(product.get('description', '') or '')
            
            # Generate embeddings for names
            logging.info(f"   Generating name embeddings...")
            name_embeddings = self.generate_embeddings_parallel(names, max_workers=max_workers)
            
            # Generate embeddings for descriptions
            logging.info(f"   Generating description embeddings...")
            desc_embeddings = self.generate_embeddings_parallel(descriptions, max_workers=max_workers)
            
            # Update Neo4j with embeddings
            logging.info(f"   Updating Neo4j with embeddings...")
            for i, product in enumerate(batch_products):
                product_id = product['id']
                name_emb = name_embeddings[i]
                desc_emb = desc_embeddings[i]
                
                success = self.update_product_embeddings(product_id, name_emb, desc_emb)
                
                if success:
                    successful_updates += 1
                else:
                    failed_updates += 1
                    logging.warning(f"   ⚠️ Failed to update product {product_id}")
            
            logging.info(f"   ✅ Batch {batch_num} complete: {successful_updates}/{batch_start + len(batch_products)} products updated")
        
        # Final summary
        logging.info(f"\n{'='*70}")
        logging.info(f"✅ Embedding generation complete!")
        logging.info(f"   Store: {store_name}")
        logging.info(f"   Total products: {total_products}")
        logging.info(f"   Successfully updated: {successful_updates}")
        logging.info(f"   Failed updates: {failed_updates}")
        logging.info(f"{'='*70}\n")
    
    def verify_embeddings(self, store_name: str, sample_size: int = 5):
        """
        Verify that embeddings were stored correctly by sampling a few products.
        
        Args:
            store_name: Name of the store
            sample_size: Number of products to sample
        """
        query = """
        MATCH (p:Product)<-[:SELLS]-(s:Store {name: $store_name})
        WHERE p.name_embedding IS NOT NULL
        RETURN p.id AS id,
               p.product_name AS product_name,
               p.name_embedding AS name_embedding,
               p.description_embedding AS desc_embedding,
               p.embeddings_updated_at AS updated_at
        LIMIT $sample_size
        """
        
        logging.info(f"\n🔍 Verifying embeddings for store: {store_name}")
        results = self.execute_query(query, {"store_name": store_name, "sample_size": sample_size})
        
        if not results:
            logging.warning(f"⚠️ No products with embeddings found in store '{store_name}'")
            return
        
        logging.info(f"✅ Found {len(results)} products with embeddings:")
        
        for i, product in enumerate(results, 1):
            name_emb = json.loads(product['name_embedding_openai']) if product['name_embedding_openai'] else None
            desc_emb = json.loads(product['desc_embedding_openai']) if product['desc_embedding_openai'] else None
            
            logging.info(f"\n   [{i}] Product ID: {product['id']}")
            logging.info(f"       Name: {product['product_name']}")
            logging.info(f"       Name embedding length: {len(name_emb) if name_emb else 0}")
            logging.info(f"       Description embedding length: {len(desc_emb) if desc_emb else 0}")
            logging.info(f"       Updated at: {product['updated_at']}")


def main():
    """Main function to run the embedding generation process."""
    
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()
    
    # Initialize generator
    generator = EmbeddingGenerator()
    
    # Connect to Neo4j
    if not generator.connect_to_neo4j():
        logging.error("❌ Failed to connect to Neo4j. Exiting.")
        return
    
    try:
        # Example: Generate embeddings for Eroski store (first 100 products)
        store_name = "amazon"
        limit = 100
        
        logging.info(f"🚀 Starting embedding generation for {store_name}")
        logging.info(f"   Processing limit: {limit if limit else 'all products'}")
        
        # Generate and store embeddings
        generator.process_store_embeddings(
            store_name=store_name,
            limit=limit,
            max_workers=100,
            batch_size=100
        )
        
        # Verify a sample
        generator.verify_embeddings(store_name, sample_size=5)
    
    finally:
        generator.close_connection()


if __name__ == "__main__":
    main()
