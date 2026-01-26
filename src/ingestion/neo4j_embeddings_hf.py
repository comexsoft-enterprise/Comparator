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

from config.settings import NEO4J_CONFIG, LOGGING_CONFIG
from langchain_community.vectorstores.neo4j_vector import Neo4jVector
from neo4j import GraphDatabase

# HuggingFace embedder for all-MiniLM-L6-v2
try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

# Logging
level_name = LOGGING_CONFIG.get("level", "INFO") if isinstance(LOGGING_CONFIG, dict) else "INFO"
numeric_level = getattr(logging, level_name.upper(), logging.INFO)
logging.basicConfig(level=numeric_level, format=LOGGING_CONFIG.get("format", "%(asctime)s - %(levelname)s - %(message)s") if isinstance(LOGGING_CONFIG, dict) else "%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("neo4j_embeddings_hf")

EMBEDDER = "hf"

def get_embedder():
    if SentenceTransformer is None:
        raise RuntimeError("SentenceTransformer is not available")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    class HFEmbedder:
        def embed_documents(self, texts):
            return model.encode(texts, show_progress_bar=False, convert_to_numpy=True).tolist()
    return HFEmbedder()

def process_batch_embeddings(embedder, texts_batch):
    try:
        texts = [text for _, text in texts_batch]
        embeddings = embedder.embed_documents(texts)
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
    # Use backticks for embedding_property if it contains a dash
    emb_prop = embedding_property
    if '-' in emb_prop:
        emb_prop = f'`{emb_prop}`'
    query = f"""
MATCH (n:{node_label})-[:SELLS]-(s:Store)
WHERE n.{text_property} IS NOT NULL
  AND n.{emb_prop} IS NULL
  AND s.name IN ['makro-01013', 'eroski-01013']
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
    batches = [nodes_to_process[i:i + batch_size] for i in range(0, total_nodes, batch_size)]
    total_batches = len(batches)
    logger.info(f"Processing {total_batches} batches with {max_workers} workers...")
    write_lock = Lock()
    processed_count = 0

    def process_and_write_batch(batch):
        nonlocal processed_count
        results = process_batch_embeddings(embedder, batch)
        if not results:
            return 0
        # Use backticks for embedding_property if it contains a dash
        emb_prop = embedding_property
        if '-' in emb_prop:
            emb_prop = f'`{emb_prop}`'
        update_query = f"""
UNWIND $batch AS item
MATCH (n:{node_label})
WHERE elementId(n) = item.node_id
SET n.{emb_prop} = item.embedding
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
    check_query = "SHOW INDEXES YIELD name WHERE name = $index_name RETURN count(*) as count"
    with driver.session() as session:
        result = session.run(check_query, {"index_name": index_name})
        exists = result.single()["count"] > 0
    if exists:
        logger.info(f"Vector index '{index_name}' already exists")
        return
    idx_name = index_name
    emb_prop = embedding_property
    if '-' in idx_name:
        idx_name = f'`{idx_name}`'
    if '-' in emb_prop:
        emb_prop = f'`{emb_prop}`'
    create_query = f"""
CREATE VECTOR INDEX {idx_name} IF NOT EXISTS
FOR (n:{node_label})
ON n.{emb_prop}
OPTIONS {{indexConfig: {{
    `vector.dimensions`: 384,
    `vector.similarity_function`: 'cosine'
}}}}
"""
    with driver.session() as session:
        session.run(create_query)
    logger.info(f"✅ Created vector index '{index_name}'")

def main():
    start = time.time()
    suffix = "hf_all-MiniLM-L6-v2"
    logger.info("Starting embeddings generation using: %s", EMBEDDER)
    embedder = get_embedder()
    driver = GraphDatabase.driver(
        NEO4J_CONFIG["uri"],
        auth=(
            NEO4J_CONFIG.get("user") or NEO4J_CONFIG.get("username"),
            NEO4J_CONFIG["password"]
        )
    )
    try:
        batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "50"))
        max_workers = int(os.getenv("EMBEDDING_MAX_WORKERS", "10"))
        logger.info(f"Configuration: batch_size={batch_size}, max_workers={max_workers}")
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
