### Imports
from pathlib import Path
import sys
import time
import logging
import os
from pathlib import Path
import sys
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from config.settings import NEO4J_CONFIG, AZURE_OPENAI_CONFIG, LOGGING_CONFIG
from langchain_community.vectorstores.neo4j_vector import Neo4jVector
from neo4j import GraphDatabase

# Try to import both embedders

try:
    from langchain_openai import AzureOpenAIEmbeddings
except Exception:
    AzureOpenAIEmbeddings = None

# Logging
level_name = LOGGING_CONFIG.get("level", "INFO") if isinstance(LOGGING_CONFIG, dict) else "INFO"
numeric_level = getattr(logging, level_name.upper(), logging.INFO)
logging.basicConfig(level=numeric_level, format=LOGGING_CONFIG.get("format", "%(asctime)s - %(levelname)s - %(message)s") if isinstance(LOGGING_CONFIG, dict) else "%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("neo4j_embeddings")

# Choose embedder via env var ACG_EMBEDDER (hf|openai)
EMBEDDER = "openai"


def get_embedder():
    if EMBEDDER == "openai":
        if AzureOpenAIEmbeddings is None:
            raise RuntimeError("AzureOpenAIEmbeddings is not available")
        return AzureOpenAIEmbeddings(
            model="text-embedding-3-large",
            api_key=AZURE_OPENAI_CONFIG.get("api_key"),
            azure_endpoint=AZURE_OPENAI_CONFIG.get("api_endpoint"),
            api_version=AZURE_OPENAI_CONFIG.get("api_version"),
        )
    else:
        raise RuntimeError("Unknown EMBEDDER. Use 'hf' or 'openai'.")


def process_batch_embeddings(embedder, texts_batch):
    """
    Process a batch of texts and generate embeddings using the embedder.
    
    Args:
        embedder: The embedding model instance
        texts_batch: List of tuples (node_id, text)
    
    Returns:
        List of tuples (node_id, embedding_vector)
    """
    try:
        # Extract just the texts
        texts = [text for _, text in texts_batch]
        
        # Generate embeddings in batch
        embeddings = embedder.embed_documents(texts)
        
        # Pair back with node IDs
        results = [(node_id, emb) for (node_id, _), emb in zip(texts_batch, embeddings)]
        return results
    except Exception as e:
        logger.error(f"Error generating embeddings for batch: {e}")
        return []


def generate_embeddings_multithreaded(
    driver,
    embedder,
    node_label,
    text_property,
    embedding_property,
    batch_size=1,
    max_workers=1000
):
    """
    Generate embeddings for nodes using multithreaded processing.
    
    Args:
        driver: Neo4j driver instance
        embedder: Embedding model instance
        node_label: Label of nodes to process
        text_property: Property containing text to embed
        embedding_property: Property to store embeddings
        batch_size: Number of texts per batch
        max_workers: Number of parallel threads
    """
    
    # Fetch all nodes that need embeddings
    query = f"""
    MATCH (n:{node_label})
    WHERE n.{text_property} IS NOT NULL 
      AND n.{embedding_property} IS NULL
    RETURN elementId(n) AS node_id, n.{text_property} AS text
    """
    
    with driver.session() as session:
        result = session.run(query)
        nodes_to_process = [(record["node_id"], record["text"]) for record in result]
    
    total_nodes = len(nodes_to_process)
    logger.info(f"Found {total_nodes} nodes to process for {text_property}")
    
    if total_nodes == 0:
        logger.info("No nodes to process - all embeddings already exist")
        return
    
    # Create batches
    batches = [nodes_to_process[i:i + batch_size] for i in range(0, total_nodes, batch_size)]
    total_batches = len(batches)
    
    logger.info(f"Processing {total_batches} batches with {max_workers} workers...")
    
    # Progress tracking
    write_lock = Lock()
    processed_count = 0
    
    def process_and_write_batch(batch):
        """Process a batch and write results to Neo4j"""
        nonlocal processed_count
        
        # Generate embeddings
        results = process_batch_embeddings(embedder, batch)
        
        if not results:
            return 0
        
        # Write to Neo4j
        update_query = f"""
        UNWIND $batch AS item
        MATCH (n:{node_label})
        WHERE elementId(n) = item.node_id
        SET n.{embedding_property} = item.embedding
        """
        
        batch_data = [
            {"node_id": node_id, "embedding": emb}
            for node_id, emb in results
        ]
        
        with driver.session() as session:
            session.run(update_query, {"batch": batch_data})
        
        with write_lock:
            processed_count += len(results)
        
        return len(results)
    
    # Process batches in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_and_write_batch, batch): i 
                   for i, batch in enumerate(batches)}
        
        with tqdm(total=total_nodes, desc=f"Generating {text_property} embeddings") as pbar:
            for future in as_completed(futures):
                try:
                    count = future.result()
                    pbar.update(count)
                except Exception as e:
                    logger.error(f"Batch processing error: {e}")
    
    logger.info(f"✅ Completed: {processed_count}/{total_nodes} nodes processed")


def create_vector_index(driver, index_name, node_label, embedding_property):
    """Create vector index in Neo4j if it doesn't exist"""
    
    # Check if index exists
    check_query = "SHOW INDEXES YIELD name WHERE name = $index_name RETURN count(*) as count"
    
    with driver.session() as session:
        result = session.run(check_query, {"index_name": index_name})
        exists = result.single()["count"] > 0
    
    if exists:
        logger.info(f"Vector index '{index_name}' already exists")
        return
    
    # Create index
    create_query = f"""
    CREATE VECTOR INDEX {index_name} IF NOT EXISTS
    FOR (n:{node_label})
    ON n.{embedding_property}
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: 3072,
        `vector.similarity_function`: 'cosine'
    }}}}
    """
    
    with driver.session() as session:
        session.run(create_query)
    
    logger.info(f"✅ Created vector index '{index_name}'")


def main():
    start = time.time()
    suffix = "hf" if EMBEDDER == "hf" else "openai"
    logger.info("Starting embeddings generation using: %s", EMBEDDER)

    embedder = get_embedder()
    
    # Create Neo4j driver
    driver = GraphDatabase.driver(
        NEO4J_CONFIG["uri"],
        auth=(
            NEO4J_CONFIG.get("user") or NEO4J_CONFIG.get("username"),
            NEO4J_CONFIG["password"]
        )
    )
    
    try:
        # Configuration
        batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "50"))
        max_workers = int(os.getenv("EMBEDDING_MAX_WORKERS", "10"))
        
        logger.info(f"Configuration: batch_size={batch_size}, max_workers={max_workers}")
        
        # Process product_name embeddings
        logger.info("=" * 70)
        logger.info("Generating product_name embeddings")
        logger.info("=" * 70)
        
        generate_embeddings_multithreaded(
            driver=driver,
            embedder=embedder,
            node_label="Product",
            text_property="product_name",
            embedding_property=f"product_name_embedding_{suffix}",
            batch_size=batch_size,
            max_workers=max_workers
        )
        
        create_vector_index(
            driver=driver,
            index_name=f"product_name_embedding_{suffix}",
            node_label="Product",
            embedding_property=f"product_name_embedding_{suffix}"
        )
        
        # Process description embeddings
        logger.info("=" * 70)
        logger.info("Generating description embeddings")
        logger.info("=" * 70)
        
        generate_embeddings_multithreaded(
            driver=driver,
            embedder=embedder,
            node_label="Product",
            text_property="description",
            embedding_property=f"description_embedding_{suffix}",
            batch_size=batch_size,
            max_workers=max_workers
        )
        
        create_vector_index(
            driver=driver,
            index_name=f"description_embedding_{suffix}",
            node_label="Product",
            embedding_property=f"description_embedding_{suffix}"
        )
        
        total = time.time() - start
        logger.info("=" * 70)
        logger.info(f"✅ Embeddings generation finished in {total:.2f} seconds ({total/60:.2f} minutes)")
        logger.info("=" * 70)
        
    finally:
        driver.close()


if __name__ == "__main__":
    main()