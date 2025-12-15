"""
FastAPI application for supermarket data comparison and preprocessing.
"""
import io
import json
import hashlib
from data.schemas.taxonomy import ColumnRegistry
import uvicorn
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from bson import ObjectId
from neo4j.graph import Node, Relationship

from config.settings import NEO4J_CONFIG, AZURE_OPENAI_CONFIG, PROJECT_ROOT

from src.connectors.neo4j_connector import Neo4jConnector
from src.connectors.mongodb_connector import get_mongo_client
from src.connectors.postgresql_connector import get_postgresql_connection

from src.api.api_utils.upload_file import PreprocessingResponse, process_uploaded_file
from src.api.api_utils.neo4j_queries import FilterTriplets, get_nodes_by_label, generate_query_for_filter_triplets, verify_node_exists_query

from src.models.model_variations.get_similar_products import (
    model,
    ModelParameters,
    FoodWeights,
    NonFoodSuperWeights,
    NonFoodElecWeights,
    QualityThresholds
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

NEO4J_URI = NEO4J_CONFIG["uri"]
NEO4J_USERNAME = NEO4J_CONFIG["user"]
NEO4J_PASSWORD = NEO4J_CONFIG["password"]
NEO4J_DATABASE = NEO4J_CONFIG.get("database", "neo4j")

AZURE_OPENAI_API_KEY = AZURE_OPENAI_CONFIG["api_key"]
AZURE_OPENAI_ENDPOINT = AZURE_OPENAI_CONFIG["api_base"]
AZURE_OPENAI_API_VERSION = AZURE_OPENAI_CONFIG["api_version"]


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class ProductUpdateRequest(BaseModel):
    """Request model for updating a product"""
    updates: Dict[str, Any]
    
    class Config:
        json_schema_extra = {
            "example": {
                "updates": {
                    "name": "New Product Name",
                    "price": 12.99,
                    "internal_category": "Beverages"
                }
            }
        }


class CompareStoresRequest(BaseModel):
    """Request model for comparing products between stores"""
    store_a: str = Field(description="Name of the source store (e.g., 'eroski-01013-01')")
    store_b: str = Field(description="Name of the target store (e.g., 'makro-01013-01')")
    list_ids: List[str] = Field(description="List of product IDs (siids) to compare from store_a")
    top_n_results: int = Field(default=5, ge=1, description="Number of similar products to return per product")
    
    # Optional weight configurations (use defaults if not provided)
    food_weights: Optional[FoodWeights] = Field(default=None, description="Custom weights for FOOD products")
    non_food_super_weights: Optional[NonFoodSuperWeights] = Field(default=None, description="Custom weights for NON-FOOD SUPERMARKET products")
    non_food_elec_weights: Optional[NonFoodElecWeights] = Field(default=None, description="Custom weights for NON-FOOD ELECTRONICS products")
    quality_thresholds: Optional[QualityThresholds] = Field(default=None, description="Custom quality thresholds for filtering")
    
    class Config:
        json_schema_extra = {
            "example": {
                "store_a": "eroski-01013-01",
                "store_b": "makro-01013-01",
                "list_ids": ["23421704", "22405302"],
                "top_n_results": 5
            }
        }


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _generate_product_hash(product_data: Dict[str, Any], registry: ColumnRegistry) -> str:
    """
    Generate product hash using the same logic as the preprocessing pipeline.
    
    Args:
        product_data: Product data dictionary
        registry: Column registry to determine which columns to exclude from hash
        
    Returns:
        str: SHA256 hash of the product
    """
    # Get uptable columns to exclude from hash
    uptable_cols = registry.get_uptable_columns()
    exclude_cols = {col.name.lower() for col in uptable_cols}
    
    # Get columns to include in hash (those NOT in exclude list)
    hash_cols = sorted([k for k in product_data.keys() if k.lower() not in exclude_cols])
    
    # Create hash string
    values = [str(product_data.get(col, '')) for col in hash_cols]
    concat = '|'.join(values)
    
    return hashlib.sha256(concat.encode('utf-8')).hexdigest()


def _remove_embedding_keys(obj):
    """
    Recursively remove any dict keys that contain the substring 'emebedding' (typo)
    or 'embedding' (correct spelling), case-insensitive. Works in-place for dicts
    and returns a cleaned copy for other types.
    """
    # helper predicate
    def is_embedding_key(k: str) -> bool:
        try:
            kl = k.lower()
            return 'emebedding' in kl or 'embedding' in kl
        except Exception:
            return False

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and is_embedding_key(k):
                # skip any key that looks like embedding
                continue
            # recurse into values
            out[k] = _remove_embedding_keys(v)
        return out
    elif isinstance(obj, list):
        return [_remove_embedding_keys(i) for i in obj]
    else:
        return obj



# Initialize FastAPI app
app = FastAPI(
    title="Supermarket Data API",
    description="API for comparing supermarket products and processing new data files",
    version="1.0.0"
)


def get_neo4j_connection() -> Neo4jConnector:
    """
    Get a Neo4j connector instance.
    
    Returns:
        Neo4jConnector: Connected Neo4j connector instance
        
    Raises:
        HTTPException: If connection fails
    """
    connector = Neo4jConnector()
    if not connector.connect_to_neo4j():
        raise HTTPException(
            status_code=503,
            detail="Failed to connect to Neo4j database"
        )
    return connector

def run_query(connector: Neo4jConnector, q):
    """
    Execute a query that may be either:
      - a plain Cypher string
      - a tuple (query_string, params_dict)

    Returns the connector.execute_query result.
    """
    if isinstance(q, tuple):
        query_str, params = q
        return connector.execute_query(query_str, params)
    else:
        return connector.execute_query(q)


# Serialize neo4j entities into plain dicts for JSON ---
def _serialize_entity(e):
    if e is None:
        return None

    # neo4j Node
    if Node and isinstance(e, Node):
        out = dict(e)
        try:
            out["_labels"] = list(e.labels)
        except Exception:
            pass
        try:
            out["_id"] = int(e.id)
        except Exception:
            pass
        return out

    # neo4j Relationship
    if Relationship and isinstance(e, Relationship):
        out = dict(e)
        try:
            out["_type"] = e.type
        except Exception:
            pass
        try:
            out["_id"] = int(e.id)
        except Exception:
            pass
        # start/end as ids if available
        try:
            out["_start_id"] = int(e.start_node.id)
            out["_end_id"] = int(e.end_node.id)
        except Exception:
            pass
        return out

    # already a dict-like or scalar
    if isinstance(e, dict):
        return e
    try:
        return dict(e)
    except Exception:
        return e


def _extract_first_value_from_record(record):
    # record may be a mapping-like (dict/neo4j.Record). Try common keys n,r then fallback.
    try:
        if "n" in record:
            return record["n"]
        if "r" in record:
            return record["r"]
        # fallback to first value
        vals = list(record.values())
        if vals:
            return vals[0]
    except Exception:
        pass
    # last resort: return record itself
    return record



# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Supermarket Data API",
        "version": "1.0.0",
        "endpoints": {
            "POST /compare": "Compare products between two stores",
            "POST /upload": "Upload and preprocess a new supermarket file",
            "GET /mongodb/product/{siid}": "Get product by siid from MongoDB"
        }
    }

# 1. Get all nodes for a given label in Neo4j
@app.get("/nodes_by_label")
async def get_nodes_by_label_endpoint(label: str, limit: int = 10)-> Dict[str, Any]:
    """
    Get all nodes for a given label from Neo4j. Labels can be node labels, node properties, relationship types, or relationship properties.
    
    Args:
        label: Node, relationship, node property or relationship property label to query
        limit: Maximum number of labels to return
    Returns:
        List of info with the given label
    """

    try:
        connector = get_neo4j_connection()
        query = get_nodes_by_label(label, limit=limit)
        logging.info(query)

        if not query:
            logger.error(f"Label '{label}' not found in ontology or no query generated.")
            raise HTTPException(
                status_code=404,
                detail=f"Label '{label}' not found in ontology"
            )
        results = run_query(connector, query)
        nodes = []
        if results:
            for record in results:
                val = _extract_first_value_from_record(record)
                # serialize and sanitize any embedding-like keys
                ser = _serialize_entity(val)
                nodes.append(_remove_embedding_keys(ser))

    except Exception as e:
        logging.error(f"Error fetching nodes for label {label}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching nodes: {str(e)}"
        )
    return {"label": label, "nodes": nodes}

# 2. Get nodes by label filtered by another label's value.
@app.post("/nodes/filter")
def get_nodes_by_label_and_filter(
    ref_label: str,
    filters: List[FilterTriplets],
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Get nodes by label filtered by another label's value.
    
    Args:
        ref_label: Node label to query (label of reference)
        filters: List of FilterTriplets to apply as filters
        limit: Maximum number of nodes to return
    Returns:
        Dict[str, Any]: List of filtered nodes for the given label
    """
    
    try:
        connector = get_neo4j_connection()
        query = generate_query_for_filter_triplets(ref_label, filters, limit=limit, connector=connector)
        results = run_query(connector, query)
        nodes = []
        if results:
            for record in results:
                val = _extract_first_value_from_record(record)
                ser = _serialize_entity(val)
                nodes.append(_remove_embedding_keys(ser))
        return {"label": ref_label, "filtered_nodes": nodes}     

    except Exception as e:
        # Fixed error message to handle list of filters
        filter_desc = ", ".join([f"{f.field} {f.filter_type} {f.value}" for f in filters])
        logger.error(f"Error fetching filtered nodes for label {ref_label} with filters [{filter_desc}]: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching filtered nodes: {str(e)}"
        )



# 3. Get products by label filtered by multiple filters.

@app.post("/products/search")
async def search_products(
    filters: List[FilterTriplets],
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Get products by label filtered by multiple filters.

    This function delegates to `generate_query_for_filter_triplets` and returns the
    parameterized query and params dict.

    Args:
        filters: List of FilterTriplets to apply as filters
        limit: Maximum number of products to return
    Returns:
        Dict[str, Any]: List of filtered products
    """

    try:
        connector = get_neo4j_connection()
        query = generate_query_for_filter_triplets("Product", filters, limit=limit)
        results = run_query(connector, query)
        nodes = []
        if results:
            for record in results:
                val = _extract_first_value_from_record(record)
                ser = _serialize_entity(val)
                nodes.append(_remove_embedding_keys(ser))
        return {"label": "Product", "filtered_nodes": nodes} 

    except Exception as e:
        logger.error(f"Error fetching filtered nodes for label Product with filters {filters}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching filtered nodes: {str(e)}"
        )

# 4. Upload/update documents.

@app.post("/upload", response_model=PreprocessingResponse)
async def upload_and_preprocess(
    file: UploadFile = File(..., description="Supermarket data file (CSV, XLSX, XLS)"),
):
    """
    Upload a file with products from a new supermarket or new updates from  data already saved.

    Args:
        file: Uploaded file (CSV, XLSX, XLS)
    Returns:
        PreprocessingResponse: Result of the preprocessing pipeline
    ...
    """
    try:
        logging.info(f"Received file upload: {file.filename}")

        # Ensure target directory exists (where process_uploaded_file expects files)
        raw_base_dir = Path(PROJECT_ROOT) / "data" / "raw"
        raw_base_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize filename and save uploaded file to expected location
        safe_name = Path(file.filename).name
        saved_path = raw_base_dir / safe_name
        contents = await file.read()
        saved_path.write_bytes(contents)
        logging.info(f"Saved uploaded file to {saved_path}")

        file.file = io.BytesIO(contents)
        file.filename = safe_name
    
        # Pass the filename (as expected by process_uploaded_file) so pipeline finds it
        result = process_uploaded_file(file, base_path=str(raw_base_dir))
        # Handle pipeline early-exit (None) — return a valid PreprocessingResponse
        if result is None:
            logger.info("Pipeline skipped processing (no new/modified products).")
            return PreprocessingResponse(
                status="skipped",
                filename=safe_name,
                original_path=str(saved_path),
                processed_path=None,
                rows_processed=0,
                columns_processed=0,
                message="No new or modified products found; pipeline skipped.",
                errors=None,
                ingestion_results=None
            )

        # Handle pipeline error response
        if isinstance(result, dict) and result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message", "Processing error"))

        # Ensure ingestion_results exists
        if isinstance(result, dict) and "ingestion_results" not in result:
            result["ingestion_results"] = None

        logger.info(f"Successfully processed file: {safe_name} -> {result.get('processed_path')}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# 5. Get similar products between two stores.
@app.post("/compare")
async def compare_stores(request: CompareStoresRequest) -> Dict[str, List]:
    """
    Compare products between two stores using the similarity model.
    
    Args:
        request: CompareStoresRequest containing:
            - store_a: Source store name
            - store_b: Target store name  
            - list_ids: List of product IDs to compare
            - top_n_results: Number of similar products to return per product
            - Optional weight configurations and quality thresholds
        
    Returns:
        Dict mapping store_a product IDs to lists of similar store_b product IDs
    """

    connector = None
    
    try:
        logger.info(f"Comparing stores: {request.store_a} vs {request.store_b}")
        logger.info(f"Analyzing {len(request.list_ids)} products")
        
        # Connect to Neo4j
        connector = get_neo4j_connection()
        
        # Verify both stores exist
        verify_store_1_query = verify_node_exists_query(label="Store", node_name=request.store_a)
        store1_result = run_query(connector, verify_store_1_query)
        if not store1_result:
            raise HTTPException(
                status_code=404,
                detail=f"Store '{request.store_a}' not found in database"
            )
        logger.info(f"Store '{request.store_a}' found in database")
        
        verify_store_2_query = verify_node_exists_query(label="Store", node_name=request.store_b)
        store2_result = run_query(connector, verify_store_2_query)
        if not store2_result:
            raise HTTPException(
                status_code=404,
                detail=f"Store '{request.store_b}' not found in database"
            )
        logger.info(f"Store '{request.store_b}' found in database")
        
        # Build ModelParameters from request
        model_params = ModelParameters(
            store_a=request.store_a,
            store_b=request.store_b,
            list_ids=request.list_ids,
            top_n_results=request.top_n_results,
            food_weights=request.food_weights or FoodWeights(),
            non_food_super_weights=request.non_food_super_weights or NonFoodSuperWeights(),
            non_food_elec_weights=request.non_food_elec_weights or NonFoodElecWeights(),
            quality_thresholds=request.quality_thresholds or QualityThresholds()
        )

        # Perform comparison
        comparison_ids = model(model_params)

        logger.info(f"Comparison complete: {len(comparison_ids)} products analyzed")

        return comparison_ids
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error comparing stores: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
    finally:
        if connector:
            connector.close_connection()


# 6. Get product by siid from MongoDB (FICHAS DE PRODUCTOS)
@app.get("/mongodb/product/{siid}")
async def get_product_by_siid(siid: str)-> Dict[str, Any]:
    """
    Get product information from MongoDB by siid.
    Searches across all collections until the product is found.
    
    Args:
        siid: Product's siid identifier
        
    Returns:
        Product information from MongoDB including the collection where it was found
    """
    try:
        db = get_mongo_client()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Failed to connect to MongoDB"
            )
        
        # Search in all collections
        collections = db.list_collection_names()
        
        for coll_name in collections:
            coll = db[coll_name]
            doc = coll.find_one({"siid": siid})
            if doc:
                logger.info(f"Found product with siid {siid} in collection {coll_name}")
                product_doc = _serialize_doc(doc)

                # Inline lightweight cleaning: remove keys with None, empty string, or 'null' (case-insensitive)
                def inline_clean(o):
                    if isinstance(o, dict):
                        out = {}
                        for kk, vv in o.items():
                            # remove None
                            if vv is None:
                                continue
                            # remove empty string or explicit 'null' text
                            if isinstance(vv, str) and (vv.strip() == "" or vv.strip().lower() == "null"):
                                continue
                            # recurse for nested structures
                            if isinstance(vv, (dict, list)):
                                cleaned = inline_clean(vv)
                                out[kk] = cleaned
                            else:
                                out[kk] = vv
                        return out
                    elif isinstance(o, list):
                        lst = []
                        for item in o:
                            if item is None:
                                continue
                            if isinstance(item, str) and (item.strip() == "" or item.strip().lower() == "null"):
                                continue
                            if isinstance(item, (dict, list)):
                                lst.append(inline_clean(item))
                            else:
                                lst.append(item)
                        return lst
                    else:
                        return o

                product_doc = inline_clean(product_doc)

                # Remove any embedding keys before returning the product
                product_doc = _remove_embedding_keys(product_doc)

                result = {
                    "collection": coll_name,
                    "product": product_doc
                }

                # If product has a product_hash, try to fetch price_history from PostgreSQL
                product_hash = None
                try:
                    product_hash = doc.get('product_hash') or doc.get('Product_Hash')
                except Exception:
                    product_hash = None

                if product_hash:
                    try:
                        pg_conn = get_postgresql_connection()
                        cur = pg_conn.cursor()
                        # Query price_history from product_vector_data table by product_hash
                        cur.execute("SELECT price_history FROM product_vector_data WHERE product_hash = %s LIMIT 1;", (product_hash,))
                        row = cur.fetchone()
                        if row and row[0] is not None:
                            ph = row[0]
                            # Lightweight inline clean for price_history
                            def _clean_ph(x):
                                if isinstance(x, dict):
                                    outp = {}
                                    for k2, v2 in x.items():
                                        if v2 is None:
                                            continue
                                        if isinstance(v2, str) and (v2.strip() == "" or v2.strip().lower() == "null"):
                                            continue
                                        if isinstance(v2, (dict, list)):
                                            outp[k2] = _clean_ph(v2)
                                        else:
                                            outp[k2] = v2
                                    return outp
                                if isinstance(x, list):
                                    lst2 = []
                                    for it in x:
                                        if it is None:
                                            continue
                                        if isinstance(it, str) and (it.strip() == "" or it.strip().lower() == "null"):
                                            continue
                                        if isinstance(it, (dict, list)):
                                            lst2.append(_clean_ph(it))
                                        else:
                                            lst2.append(it)
                                    return lst2
                                return x

                            if isinstance(ph, (dict, list)):
                                ph = _clean_ph(ph)
                            result['price_history'] = ph
                        cur.close()
                        pg_conn.close()
                    except Exception as e:
                        logger.warning(f"Could not fetch price_history from PostgreSQL for product_hash {product_hash}: {e}")
                        result['price_history'] = None

                return result
        
        # If not found in any collection
        raise HTTPException(
            status_code=404,
            detail=f"Product with siid '{siid}' not found in any collection"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching product by siid {siid}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching product: {str(e)}"
        )

# 8. Get product_hash by MongoDB id field
@app.get("/mongodb/product-hash/{id}")
async def get_product_hash_by_id(id: str) -> Dict[str, Any]:
    """
    Get all products with matching 'id' field from MongoDB.
    Searches across all collections and returns all matching documents.
    
    Args:
        id: Product's id field value
        
    Returns:
        List of all products with matching id, including collection and product_hash
    """
    try:
        db = get_mongo_client()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Failed to connect to MongoDB"
            )
        
        # Search in all collections by 'id' field (not _id)
        collections = db.list_collection_names()
        all_products = []
        
        for coll_name in collections:
            coll = db[coll_name]
            # Find ALL documents with this id
            docs = coll.find({"id": id})
            
            for doc in docs:
                logger.info(f"Found product with id {id} in collection {coll_name}")
                
                # Try to get product_hash from various possible field names
                product_hash = doc.get('product_hash') or doc.get('Product_Hash')
                
                if not product_hash:
                    logger.warning(f"Product found in {coll_name} but has no product_hash field")
                    continue
                
                all_products.append({
                    "id": id,
                    "collection": coll_name,
                    "product_hash": product_hash,
                    "siid": doc.get("siid"),
                    "store": doc.get("store")
                })
        
        # If not found in any collection
        if not all_products:
            raise HTTPException(
                status_code=404,
                detail=f"No products with id '{id}' found in any collection"
            )
        
        return {
            "id": id,
            "total_found": len(all_products),
            "products": all_products
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching product_hash by id {id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching product_hash: {str(e)}"
        )

# 9. Delete product by product_hash from all databases
@app.delete("/product/{product_hash}")
async def delete_product_by_hash(product_hash: str) -> Dict[str, Any]:
    """
    Delete a product from all three databases by product_hash.
    
    - Neo4j: DETACH DELETE of Product node
    - PostgreSQL: DELETE from product_vector_data table
    - MongoDB: DELETE from all collections where product_hash matches
    
    Args:
        product_hash: Product's unique hash identifier
        
    Returns:
        Deletion results from all databases
    """
    results = {
        "product_hash": product_hash,
        "neo4j": {"deleted": False, "error": None},
        "postgresql": {"deleted": False, "rows_affected": 0, "error": None},
        "mongodb": {"deleted": False, "collections_affected": [], "documents_deleted": 0, "error": None}
    }
    
    # 1. Delete from Neo4j
    connector = None
    try:
        connector = get_neo4j_connection()
        # DETACH DELETE removes the node and all its relationships
        delete_query = """
        MATCH (p:Product {product_hash: $product_hash})
        DETACH DELETE p
        RETURN count(p) as deleted_count
        """
        result = connector.execute_query(delete_query, {"product_hash": product_hash})
        
        if result and len(result) > 0:
            deleted_count = result[0].get("deleted_count", 0)
            results["neo4j"]["deleted"] = deleted_count > 0
            results["neo4j"]["deleted_count"] = deleted_count
            logger.info(f"Deleted {deleted_count} product(s) from Neo4j with product_hash: {product_hash}")
        else:
            logger.warning(f"No products found in Neo4j with product_hash: {product_hash}")
            
    except Exception as e:
        logger.error(f"Error deleting from Neo4j: {e}", exc_info=True)
        results["neo4j"]["error"] = str(e)
    finally:
        if connector:
            connector.close_connection()
    
    # 2. Delete from PostgreSQL
    try:
        pg_conn = get_postgresql_connection()
        if pg_conn:
            cur = pg_conn.cursor()
            delete_sql = "DELETE FROM product_vector_data WHERE product_hash = %s;"
            cur.execute(delete_sql, (product_hash,))
            rows_affected = cur.rowcount
            pg_conn.commit()
            cur.close()
            pg_conn.close()
            
            results["postgresql"]["deleted"] = rows_affected > 0
            results["postgresql"]["rows_affected"] = rows_affected
            logger.info(f"Deleted {rows_affected} row(s) from PostgreSQL with product_hash: {product_hash}")
        else:
            results["postgresql"]["error"] = "Failed to connect to PostgreSQL"
            
    except Exception as e:
        logger.error(f"Error deleting from PostgreSQL: {e}", exc_info=True)
        results["postgresql"]["error"] = str(e)
    
    # 3. Delete from MongoDB (all collections)
    try:
        db = get_mongo_client()
        if db is not None:
            collections = db.list_collection_names()
            total_deleted = 0
            affected_collections = []
            
            for coll_name in collections:
                coll = db[coll_name]
                # Try both field name variations
                delete_result = coll.delete_many({
                    "$or": [
                        {"product_hash": product_hash},
                        {"Product_Hash": product_hash}
                    ]
                })
                
                if delete_result.deleted_count > 0:
                    total_deleted += delete_result.deleted_count
                    affected_collections.append(coll_name)
                    logger.info(f"Deleted {delete_result.deleted_count} document(s) from MongoDB collection {coll_name}")
            
            results["mongodb"]["deleted"] = total_deleted > 0
            results["mongodb"]["documents_deleted"] = total_deleted
            results["mongodb"]["collections_affected"] = affected_collections
        else:
            results["mongodb"]["error"] = "Failed to connect to MongoDB"
            
    except Exception as e:
        logger.error(f"Error deleting from MongoDB: {e}", exc_info=True)
        results["mongodb"]["error"] = str(e)
    
    # Determine overall success
    any_deleted = (
        results["neo4j"]["deleted"] or 
        results["postgresql"]["deleted"] or 
        results["mongodb"]["deleted"]
    )
    
    any_errors = (
        results["neo4j"]["error"] is not None or
        results["postgresql"]["error"] is not None or
        results["mongodb"]["error"] is not None
    )
    
    if not any_deleted and not any_errors:
        results["status"] = "not_found"
        results["message"] = f"No product found with product_hash: {product_hash}"
    elif any_deleted and not any_errors:
        results["status"] = "success"
        results["message"] = "Product successfully deleted from all databases"
    elif any_deleted and any_errors:
        results["status"] = "partial"
        results["message"] = "Product partially deleted - some databases reported errors"
    else:
        results["status"] = "error"
        results["message"] = "Failed to delete product from all databases"
    
    return results


@app.get("/health")
async def health_check():
    """
    Health check endpoint to verify API and database connectivity.
    
    Returns:
        Health status information
    """
    connector = None
    try:
        # Test Neo4j connection
        connector = Neo4jConnector()
        neo4j_connected = connector.connect_to_neo4j()
        
        if neo4j_connected:
            test_result = connector.test_connection()
            neo4j_status = {
                "connected": True,
                "database": test_result.get('database'),
                "node_count": test_result.get('node_count'),
                "relationship_count": test_result.get('relationship_count')
            }
        else:
            neo4j_status = {"connected": False, "error": "Failed to connect"}
        
        # Test MongoDB connection
        try:
            db = get_mongo_client()
            # pymongo Database objects do not support truth-value testing.
            # Compare explicitly against None and guard collection listing.
            if db is not None:
                try:
                    collections = db.list_collection_names()
                    mongodb_status = {
                        "connected": True,
                        "database": getattr(db, "name", None),
                        "collection_count": len(collections)
                    }
                except Exception as e_col:
                    mongodb_status = {"connected": False, "error": f"Failed to list collections: {e_col}"}
            else:
                mongodb_status = {"connected": False, "error": "Failed to connect"}
        except Exception as e:
             mongodb_status = {"connected": False, "error": str(e)}

        try:
            pg = get_postgresql_connection()
            if pg:
                pg.close()
                postgresql_status = {"connected": True}
            else:
                postgresql_status = {"connected": False, "error": "Failed to connect"}
        
        except Exception as e:
            postgresql_status = {"connected": False, "error": str(e)}

        all_healthy = neo4j_connected and mongodb_status.get("connected", False) and postgresql_status.get("connected", False)
        
        return {
            "status": "healthy" if all_healthy else "degraded",
            "timestamp": datetime.now().isoformat(),
            "neo4j": neo4j_status,
            "mongodb": mongodb_status,
            "postgresql": postgresql_status
        }
        
    except Exception as e:
        logger.error(f"Health check error: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "neo4j": {"connected": False, "error": str(e)},
            "mongodb": {"connected": False, "error": str(e)},
            "postgresql": {"connected": False, "error": str(e)}
        }
    finally:
        if connector:
            connector.close_connection()

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )