# API Documentation

## Table of Contents
1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Endpoints](#endpoints)
   - [Health Check](#health-check)
   - [Product Comparison](#product-comparison)
   - [Neo4j Data Retrieval](#neo4j-data-retrieval)
   - [MongoDB Data Retrieval](#mongodb-data-retrieval)
   - [File Upload & Processing](#file-upload--processing)
   - [Product Management](#product-management)
4. [Filter System](#filter-system)
5. [Model Parameters & Weights](#model-parameters--weights)
6. [Response Formats](#response-formats)
7. [Error Handling](#error-handling)

---

## Overview

The **Supermarket Data API** is a FastAPI-based RESTful service for comparing products across different supermarket chains and managing product data stored in **Neo4j**, **MongoDB**, and **PostgreSQL** databases.

**Base URL**: `http://localhost:8000`

**Key Features**:
- Cross-store product similarity comparison using ML models
- Advanced filtering on graph database (Neo4j) using ontology
- Product data retrieval from MongoDB with enriched information
- File upload and preprocessing pipeline
- Product deletion across all databases

---

## Getting Started

### Prerequisites
- Python 3.10+
- Neo4j database (running on default port 7687)
- MongoDB (running on default port 27017)
- PostgreSQL database
- Azure OpenAI API access (for embeddings)

### Running the API

```bash
# Start the API server
cd src/api
python main.py

# Or with uvicorn directly
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Testing the API

```bash
# Check if API is running
curl http://localhost:8000/

# Health check
curl http://localhost:8000/health
```

---

## Endpoints

### Health Check

#### `GET /health`

Check the health status of the API and all database connections.

**Request**: No parameters required

**Response**:
```json
{
  "status": "healthy",
  "timestamp": "2025-12-18T10:30:00.123456",
  "neo4j": {
    "connected": true,
    "database": "neo4j",
    "node_count": 150000,
    "relationship_count": 500000
  },
  "mongodb": {
    "connected": true,
    "database": "supermarket_db",
    "collection_count": 5
  },
  "postgresql": {
    "connected": true
  }
}
```

**Status Values**:
- `healthy`: All databases connected
- `degraded`: Some databases unavailable
- `unhealthy`: Critical errors

---

### Product Comparison

#### `POST /compare`

Compare products from one store against similar products in another store using ML-based similarity analysis.

**Request Body**:
```json
{
  "store_a": "eroski-01013",
  "store_b": "makro-01013",
  "list_ids": ["product_id_1", "product_id_2"],
  "top_n_results": 5,
  "food_weights": {
    "graph": {
      "brand": 0.3,
      "format": 0.15,
      "subcategory": 0.25,
      "first_ingredient": 0.3,
      "second_ingredient": 0.1,
      "unit_measure": 0.1,
      "allergen": 0.02,
      "country_origin": 0.05,
      "other_ingredient": 0.02,
      "quantity": 0.1
    },
    "combined": {
      "graph": 0.1,
      "name": 0.4,
      "description": 0.4,
      "euclidean": 0.1
    }
  },
  "quality_thresholds": {
    "min_graph_score": 0.5,
    "min_name_similarity": 0.5,
    "min_description_similarity": 0.5,
    "min_euclidean_similarity": 0.0
  }
}
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `store_a` | string | Yes | Source store identifier (e.g., "eroski-01013") |
| `store_b` | string | Yes | Target store identifier (e.g., "makro-01013") |
| `list_ids` | array[string] | Yes | List of product IDs (siids) from store_a to compare |
| `top_n_results` | integer | No | Number of similar products to return per input product (default: 5, min: 1) |
| `food_weights` | object | No | Custom weights for FOOD category products (see [Weight Configuration](#weight-configuration)) |
| `non_food_super_weights` | object | No | Custom weights for NON-FOOD SUPERMARKET category products |
| `non_food_elec_weights` | object | No | Custom weights for NON-FOOD ELECTRONICS category products |
| `quality_thresholds` | object | No | Minimum quality thresholds for filtering results |

**Response**:
```json
{
  "product_id_1": ["similar_product_1", "similar_product_2", "similar_product_3"],
  "product_id_2": ["similar_product_4", "similar_product_5"]
}
```

**Errors**:
- `404`: Store not found in database
- `500`: Internal server error during comparison

**Example**:
```bash
curl -X POST http://localhost:8000/compare \
  -H "Content-Type: application/json" \
  -d '{
    "store_a": "eroski-01013",
    "store_b": "makro-01013",
    "list_ids": ["12345", "67890"],
    "top_n_results": 3
  }'
```

---

### Neo4j Data Retrieval

#### `GET /nodes_by_label`

Retrieve nodes from Neo4j by their label, property, or relationship type.

**Query Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `label` | string | Yes | Label to query (node label, relationship type, node property, or relationship property) |
| `limit` | integer | No | Maximum number of results (default: 10) |

**Response**:
```json
{
  "label": "Brand",
  "nodes": [
    {
      "name": "Coca-Cola",
      "_labels": ["Brand"],
      "_id": 12345
    },
    {
      "name": "Pepsi",
      "_labels": ["Brand"],
      "_id": 12346
    }
  ]
}
```

**Example**:
```bash
# Get all Brand nodes
curl "http://localhost:8000/nodes_by_label?label=Brand&limit=20"

# Get all products with store property
curl "http://localhost:8000/nodes_by_label?label=store&limit=10"
```

---

#### `POST /nodes/filter`

Get nodes filtered by multiple conditions using the ontology-based filter system.

**Request Body**:
```json
{
  "ref_label": "Product",
  "filters": [
    {
      "field": "Brand",
      "filter_type": "equal",
      "value": "Coca-Cola"
    },
    {
      "field": "price",
      "filter_type": "less_than",
      "value": 5.0
    }
  ],
  "limit": 10
}
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ref_label` | string | Yes | Node label to query (e.g., "Product", "Brand") |
| `filters` | array[FilterTriplet] | Yes | List of filter conditions (see [Filter System](#filter-system)) |
| `limit` | integer | No | Maximum number of results (default: 10) |

**Response**:
```json
{
  "label": "Product",
  "filtered_nodes": [
    {
      "siid": "12345",
      "name": "Coca-Cola Zero 2L",
      "price": 2.5,
      "store": "eroski-01013",
      "_labels": ["Product"],
      "_id": 98765
    }
  ]
}
```

**Example**:
```bash
curl -X POST http://localhost:8000/nodes/filter \
  -H "Content-Type: application/json" \
  -d '{
    "ref_label": "Product",
    "filters": [
      {"field": "Brand", "filter_type": "equal", "value": "Coca-Cola"},
      {"field": "price", "filter_type": "between", "value": [1.0, 3.0]}
    ],
    "limit": 5
  }'
```

---

#### `POST /products/search`

Search for products using multiple filters. This is a specialized version of `/nodes/filter` that always queries the Product node.

**Request Body**:
```json
{
  "filters": [
    {
      "field": "store",
      "filter_type": "equal",
      "value": "eroski-01013"
    },
    {
      "field": "Format",
      "filter_type": "contains",
      "value": "Bottle"
    }
  ],
  "limit": 20
}
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filters` | array[FilterTriplet] | Yes | List of filter conditions |
| `limit` | integer | No | Maximum number of results (default: 10) |

**Response**: Same as `/nodes/filter` but always returns Product nodes

**Example**:
```bash
curl -X POST http://localhost:8000/products/search \
  -H "Content-Type: application/json" \
  -d '{
    "filters": [
      {"field": "store", "filter_type": "equal", "value": "eroski-01013"},
      {"field": "price", "filter_type": "greater_than", "value": 10.0}
    ],
    "limit": 50
  }'
```

---

### MongoDB Data Retrieval

#### `GET /mongodb/product/{siid}`

Retrieve complete product information from MongoDB by its siid (Store Item ID).

**Path Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `siid` | string | Yes | Product's unique store item identifier |

**Response**:
```json
{
  "collection": "eroski_products",
  "product": {
    "_id": "507f1f77bcf86cd799439011",
    "siid": "12345",
    "store": "eroski-01013",
    "name": "Coca-Cola Zero 2L",
    "brand": "Coca-Cola",
    "price": 2.5,
    "description": "Sugar-free cola drink",
    "product_hash": "abc123def456...",
    "nutritional_info": {
      "calories": 0,
      "sugar": 0,
      "fat": 0
    }
  },
  "price_history": [
    {
      "date": "2025-12-01",
      "price": 2.3,
      "store": "eroski-01013"
    },
    {
      "date": "2025-12-15",
      "price": 2.5,
      "store": "eroski-01013"
    }
  ]
}
```

**Notes**:
- Searches across all MongoDB collections
- Returns the first match found
- Includes `price_history` from PostgreSQL if `product_hash` is available
- Removes embedding fields and null/empty values from response

**Errors**:
- `404`: Product not found in any collection
- `503`: Failed to connect to MongoDB
- `500`: Internal server error

**Example**:
```bash
curl http://localhost:8000/mongodb/product/12345
```

---

#### `GET /mongodb/product-file/{siid}`

Get a complete product "file" with merged information from matched products across stores.

**Path Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `siid` | string | Yes | Product's unique store item identifier |

**Query Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `format` | boolean | No | If true, returns LLM-formatted summary (default: true) |
| `lang` | string | No | Language for formatted output: "es" or "en" (default: "es") |

**Response** (when `format=false`):
```json
{
  "siid": "12345",
  "store": "eroski-01013",
  "common_fields": {
    "brand": "Coca-Cola",
    "format": "Bottle",
    "ean": "1234567890123",
    "measure_value": 2.0,
    "unit_measure": "L"
  },
  "specific_fields": {
    "name": "Coca-Cola Zero 2L",
    "price": 2.5,
    "description": "Sugar-free cola drink"
  },
  "matched_products": [
    {
      "siid": "67890",
      "store": "makro-01013",
      "name": "Coca-Cola Zero 2L",
      "price": 2.3
    }
  ]
}
```

**Response** (when `format=true`):
```json
{
  "formatted_summary": "**Coca-Cola Zero 2L**\n\nProducto sin azúcar de la marca Coca-Cola...",
  "raw_data": { /* ... same as format=false ... */ }
}
```

**Common Fields** (merged using most frequent value):
- Brand, Format, Measure value, Unit measure, Model, EAN
- Any field containing "nutri" (nutritional information)

**Non-common Fields**: All other taxonomy fields specific to the product

**Example**:
```bash
# Get raw data
curl "http://localhost:8000/mongodb/product-file/12345?format=false"

# Get formatted summary in English
curl "http://localhost:8000/mongodb/product-file/12345?format=true&lang=en"
```

---

#### `GET /mongodb/product-hash/{id}`

Find all products with a specific `id` field and return their product hashes.

**Path Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string | Yes | Product's id field value |

**Response**:
```json
{
  "id": "ABC123",
  "total_found": 3,
  "products": [
    {
      "id": "ABC123",
      "collection": "eroski_products",
      "product_hash": "hash1...",
      "siid": "12345",
      "store": "eroski-01013"
    },
    {
      "id": "ABC123",
      "collection": "makro_products",
      "product_hash": "hash2...",
      "siid": "67890",
      "store": "makro-01013"
    }
  ]
}
```

**Use Case**: Find all variations of a product across different stores or collections

**Example**:
```bash
curl http://localhost:8000/mongodb/product-hash/ABC123
```

---

### File Upload & Processing

#### `POST /upload`

Upload a supermarket data file (CSV, XLSX, or XLS) for preprocessing and ingestion into the database.

**Request**: Multipart form data with file

**Supported Formats**: CSV, XLSX, XLS

**Form Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | file | Yes | Supermarket data file |

**Response**:
```json
{
  "status": "success",
  "filename": "eroski_products_2025.csv",
  "original_path": "/data/raw/eroski_products_2025.csv",
  "processed_path": "/data/processed/eroski_products_2025_processed.csv",
  "rows_processed": 1500,
  "columns_processed": 45,
  "message": "File processed successfully",
  "errors": null,
  "ingestion_results": {
    "neo4j": {
      "nodes_created": 1500,
      "relationships_created": 4500
    },
    "mongodb": {
      "documents_inserted": 1500,
      "collection": "eroski_products"
    },
    "postgresql": {
      "rows_inserted": 1500
    }
  }
}
```

**Status Values**:
- `success`: File processed and ingested successfully
- `skipped`: No new/modified products found, processing skipped
- `error`: Processing failed

**Processing Pipeline**:
1. File validation and sanitization
2. Column mapping and standardization
3. Data cleaning and normalization
4. Product hash generation
5. Deduplication check
6. Ingestion into Neo4j, MongoDB, and PostgreSQL

**Example**:
```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@/path/to/eroski_products.csv"
```

**Python Example**:
```python
import requests

with open("eroski_products.csv", "rb") as f:
    files = {"file": f}
    response = requests.post("http://localhost:8000/upload", files=files)
    print(response.json())
```

---

### Product Management

#### `DELETE /product/{product_hash}`

Delete a product from all three databases (Neo4j, MongoDB, PostgreSQL) using its product_hash.

**Path Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `product_hash` | string | Yes | Product's unique hash identifier |

**Response**:
```json
{
  "product_hash": "abc123def456...",
  "status": "success",
  "message": "Product successfully deleted from all databases",
  "neo4j": {
    "deleted": true,
    "deleted_count": 1,
    "error": null
  },
  "postgresql": {
    "deleted": true,
    "rows_affected": 1,
    "error": null
  },
  "mongodb": {
    "deleted": true,
    "documents_deleted": 2,
    "collections_affected": ["eroski_products", "makro_products"],
    "error": null
  }
}
```

**Status Values**:
- `success`: Product deleted from all databases
- `partial`: Deleted from some databases, errors in others
- `not_found`: Product not found in any database
- `error`: Failed to delete from all databases

**Example**:
```bash
curl -X DELETE http://localhost:8000/product/abc123def456...
```

---

## Filter System

The filter system enables powerful querying of the Neo4j graph database using the ontology. Filters can be applied to node properties, relationships, and combinations thereof.

### Filter Structure

Each filter is defined as a **FilterTriplet** with three components:

```json
{
  "field": "Brand",
  "filter_type": "equal",
  "value": "Coca-Cola"
}
```

### Filter Types

| Filter Type | Description | Value Type | Example |
|-------------|-------------|------------|---------|
| `equal` | Exact match | string/number | `{"field": "store", "filter_type": "equal", "value": "eroski-01013"}` |
| `contains` | Substring match (case-insensitive) | string | `{"field": "name", "filter_type": "contains", "value": "cola"}` |
| `less_than` | Less than comparison | number | `{"field": "price", "filter_type": "less_than", "value": 5.0}` |
| `greater_than` | Greater than comparison | number | `{"field": "price", "filter_type": "greater_than", "value": 1.0}` |
| `between` | Range (inclusive) | array[number] | `{"field": "price", "filter_type": "between", "value": [1.0, 5.0]}` |
| `in` | Match any in list | array[string/number] | `{"field": "store", "filter_type": "in", "value": ["eroski-01013", "makro-01013"]}` |

### Ontology-Based Filtering

The filter system uses the Neo4j ontology to automatically determine:

1. **Field Type**: Whether the field is a node label, node property, relationship type, or relationship property
2. **Path Construction**: How to traverse the graph to apply the filter
3. **Query Optimization**: Whether to use Product-centric optimization

### Filter Examples

#### Example 1: Simple Property Filter

Find all products from a specific store with price less than 5:

```json
{
  "ref_label": "Product",
  "filters": [
    {
      "field": "store",
      "filter_type": "equal",
      "value": "eroski-01013"
    },
    {
      "field": "price",
      "filter_type": "less_than",
      "value": 5.0
    }
  ]
}
```

#### Example 2: Relationship Filter

Find all products associated with a specific brand:

```json
{
  "ref_label": "Product",
  "filters": [
    {
      "field": "Brand",
      "filter_type": "equal",
      "value": "Coca-Cola"
    }
  ]
}
```

**Generated Cypher** (simplified):
```cypher
MATCH (p:Product)-[:HAS_BRAND]->(b:Brand)
WHERE b.name = "Coca-Cola"
RETURN p
```

#### Example 3: Multi-Hop Filter

Find products with specific format and ingredient:

```json
{
  "ref_label": "Product",
  "filters": [
    {
      "field": "Format",
      "filter_type": "equal",
      "value": "Bottle"
    },
    {
      "field": "First_level_ingredient",
      "filter_type": "contains",
      "value": "water"
    }
  ]
}
```

#### Example 4: Complex Range and List Filters

Find products in multiple stores with prices in a specific range:

```json
{
  "ref_label": "Product",
  "filters": [
    {
      "field": "store",
      "filter_type": "in",
      "value": ["eroski-01013", "makro-01013", "carrefour-01013"]
    },
    {
      "field": "price",
      "filter_type": "between",
      "value": [2.0, 10.0]
    },
    {
      "field": "Brand",
      "filter_type": "equal",
      "value": "Coca-Cola"
    }
  ]
}
```

### Product-Centric Optimization

When all filters can be resolved through the Product node, the system automatically uses an optimized query that:
1. Starts with `MATCH (p:Product)`
2. Applies filters as progressive pipeline stages
3. Minimizes graph traversal

**Conditions for Optimization**:
- Reference label connects to Product
- All filter fields connect to Product (directly or through relationships)
- Multiple filters present (2+)

**Example**:
```cypher
MATCH (p:Product)
WHERE p.store = $p0
WITH p
MATCH (p)-[:HAS_BRAND]->(b:Brand)
WHERE b.name = $p1
WITH p
WHERE p.price < $p2
RETURN DISTINCT p LIMIT 10
```

### Filter Validation

The API validates filters using the ontology:
- ✅ Field exists in ontology
- ✅ Value type matches filter type
- ✅ Path between nodes is traversable
- ❌ Returns error if validation fails

### Logical Operators

Currently, filters are combined with **AND** logic (all conditions must match).

Future support for OR logic is planned.

---

## Model Parameters & Weights

The `/compare` endpoint uses a sophisticated ML model that combines multiple similarity methods. You can customize the weights to tune the comparison for different product categories.

### Weight Configuration

#### Three Product Categories

1. **FOOD** (`food_weights`)
2. **NON-FOOD SUPERMARKET** (`non_food_super_weights`)
3. **NON-FOOD ELECTRONICS** (`non_food_elec_weights`)

Each category has its own weight configuration.

### Weight Structure

```json
{
  "graph": {
    // Graph-based similarity weights (Neo4j relationships)
  },
  "combined": {
    // Method combination weights
    "graph": 0.1,        // Weight for graph-based similarity
    "name": 0.4,         // Weight for name embedding similarity
    "description": 0.4,  // Weight for description embedding similarity
    "euclidean": 0.1     // Weight for euclidean distance similarity
  }
}
```

### FOOD Products

**Default Graph Weights**:
```json
{
  "brand": 0.300,              // Brand importance
  "format": 0.150,             // Packaging format
  "subcategory": 0.250,        // Product subcategory
  "first_ingredient": 0.300,   // Primary ingredient
  "second_ingredient": 0.100,  // Secondary ingredient
  "unit_measure": 0.100,       // Measurement unit
  "allergen": 0.020,           // Allergen information
  "country_origin": 0.050,     // Country of origin
  "other_ingredient": 0.020,   // Other ingredients
  "quantity": 0.100            // Product quantity
}
```

**Default Combined Weights**:
```json
{
  "graph": 0.10,        // 10% graph-based
  "name": 0.40,         // 40% name similarity
  "description": 0.40,  // 40% description similarity
  "euclidean": 0.10     // 10% numerical similarity
}
```

**Rationale**: For food, semantic similarity (name + description) is most important because products with similar descriptions are likely similar (e.g., "organic milk" vs "bio milk").

### NON-FOOD SUPERMARKET Products

**Default Graph Weights**:
```json
{
  "brand": 0.100,
  "format": 0.200,
  "subcategory": 0.300,
  "first_component": 0.300,
  "second_component": 0.100,
  "unit_measure": 0.150,
  "country_origin": 0.100,
  "quantity": 0.150
}
```

**Default Combined Weights**:
```json
{
  "graph": 0.25,        // 25% graph-based
  "name": 0.42,         // 42% name similarity
  "description": 0.25,  // 25% description similarity
  "euclidean": 0.08     // 8% numerical similarity
}
```

**Rationale**: Household products benefit from balanced graph and semantic similarity (e.g., "kitchen towel" across brands).

### NON-FOOD ELECTRONICS Products

**Default Graph Weights**:
```json
{
  "brand": 0.400,              // Brand is critical for electronics
  "format": 0.150,
  "subcategory": 0.300,
  "first_component": 0.250,
  "second_component": 0.150,
  "unit_measure": 0.100,
  "country_origin": 0.050,
  "quantity": 0.100
}
```

**Default Combined Weights**:
```json
{
  "graph": 0.33,        // 33% graph-based
  "name": 0.42,         // 42% name similarity
  "description": 0.17,  // 17% description similarity
  "euclidean": 0.08     // 8% numerical similarity
}
```

**Rationale**: Electronics heavily depend on brand and specifications. Graph similarity (brand, model, components) is more important.

### Quality Thresholds

Minimum scores required to include a product in results:

```json
{
  "min_graph_score": 0.5,              // Minimum graph similarity (0-1)
  "min_name_similarity": 0.5,          // Minimum name embedding similarity (0-1)
  "min_description_similarity": 0.5,   // Minimum description similarity (0-1)
  "min_euclidean_similarity": 0.0      // Minimum euclidean similarity (0-1)
}
```

**Notes**:
- Products must meet **ALL** threshold requirements
- Lower thresholds return more results but lower quality
- Higher thresholds return fewer but more accurate matches

### Customization Example

```json
{
  "store_a": "eroski-01013",
  "store_b": "makro-01013",
  "list_ids": ["12345"],
  "top_n_results": 5,
  "food_weights": {
    "graph": {
      "brand": 0.5,              // Increase brand importance
      "first_ingredient": 0.4,   // Increase ingredient importance
      "allergen": 0.05           // Increase allergen importance
    },
    "combined": {
      "graph": 0.3,    // More weight on graph
      "name": 0.35,
      "description": 0.25,
      "euclidean": 0.1
    }
  },
  "quality_thresholds": {
    "min_graph_score": 0.7,          // Stricter requirements
    "min_name_similarity": 0.6,
    "min_description_similarity": 0.6
  }
}
```

### Weight Tuning Tips

1. **High Brand Loyalty Categories** (e.g., electronics): Increase `brand` weight
2. **Ingredient-Focused** (e.g., organic food): Increase `first_ingredient` and `second_ingredient`
3. **Format-Specific** (e.g., beverages): Increase `format` and `quantity`
4. **Generic Products** (e.g., household items): Increase `name` and `description` weights
5. **Strict Matching**: Increase quality thresholds
6. **Broader Matching**: Decrease quality thresholds

---

## Response Formats

### Success Response

All successful responses include relevant data:

```json
{
  "status": "success",
  "data": { /* ... endpoint-specific data ... */ }
}
```

### Error Response

All error responses follow this format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common HTTP Status Codes

| Code | Meaning | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 400 | Bad Request | Invalid parameters or request body |
| 404 | Not Found | Resource not found |
| 422 | Unprocessable Entity | Validation error |
| 500 | Internal Server Error | Server-side error |
| 503 | Service Unavailable | Database connection failed |

### Data Types

| Type | Format | Example |
|------|--------|---------|
| String | Text | `"eroski-01013"` |
| Integer | Whole number | `5` |
| Float | Decimal number | `2.5` |
| Boolean | true/false | `true` |
| Array | List | `["item1", "item2"]` |
| Object | JSON object | `{"key": "value"}` |
| Date | ISO 8601 | `"2025-12-18T10:30:00.123456"` |

---

## Error Handling

### Database Connection Errors

**Neo4j Connection Failed**:
```json
{
  "detail": "Failed to connect to Neo4j database"
}
```
**HTTP Status**: 503

**MongoDB Connection Failed**:
```json
{
  "detail": "Failed to connect to MongoDB"
}
```
**HTTP Status**: 503

### Resource Not Found Errors

**Store Not Found**:
```json
{
  "detail": "Store 'invalid-store' not found in database"
}
```
**HTTP Status**: 404

**Product Not Found**:
```json
{
  "detail": "Product with siid '99999' not found in any collection"
}
```
**HTTP Status**: 404

### Validation Errors

**Invalid Filter Type**:
```json
{
  "detail": [
    {
      "loc": ["body", "filters", 0, "filter_type"],
      "msg": "value is not a valid enumeration member; permitted: 'equal', 'less_than', 'greater_than', 'between', 'contains', 'in'",
      "type": "type_error.enum"
    }
  ]
}
```
**HTTP Status**: 422

**Missing Required Field**:
```json
{
  "detail": [
    {
      "loc": ["body", "store_a"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```
**HTTP Status**: 422

### Processing Errors

**File Upload Error**:
```json
{
  "status": "error",
  "message": "Failed to process file: Invalid file format"
}
```
**HTTP Status**: 500

**Comparison Error**:
```json
{
  "detail": "Internal server error: Failed to compute embeddings"
}
```
**HTTP Status**: 500

### Error Troubleshooting

1. **503 Service Unavailable**:
   - Check if all databases are running
   - Verify connection credentials in `.env`
   - Use `/health` endpoint to diagnose

2. **404 Not Found**:
   - Verify resource identifiers (store names, product IDs)
   - Check database contents

3. **422 Validation Error**:
   - Review request body format
   - Ensure all required fields are present
   - Verify data types match schema

4. **500 Internal Server Error**:
   - Check server logs for detailed error messages
   - Verify all services (Azure OpenAI, databases) are accessible
   - Report persistent errors to development team

---

## Appendix: Complete Request Examples

### Example 1: Full Product Comparison with Custom Weights

```bash
curl -X POST http://localhost:8000/compare \
  -H "Content-Type: application/json" \
  -d '{
    "store_a": "eroski-01013",
    "store_b": "makro-01013",
    "list_ids": ["100001", "100002", "100003"],
    "top_n_results": 3,
    "food_weights": {
      "graph": {
        "brand": 0.4,
        "format": 0.2,
        "subcategory": 0.3,
        "first_ingredient": 0.35,
        "second_ingredient": 0.15,
        "unit_measure": 0.1,
        "allergen": 0.05,
        "country_origin": 0.1,
        "other_ingredient": 0.03,
        "quantity": 0.12
      },
      "combined": {
        "graph": 0.2,
        "name": 0.4,
        "description": 0.3,
        "euclidean": 0.1
      }
    },
    "quality_thresholds": {
      "min_graph_score": 0.6,
      "min_name_similarity": 0.55,
      "min_description_similarity": 0.5,
      "min_euclidean_similarity": 0.0
    }
  }'
```

### Example 2: Advanced Multi-Filter Product Search

```bash
curl -X POST http://localhost:8000/products/search \
  -H "Content-Type: application/json" \
  -d '{
    "filters": [
      {
        "field": "store",
        "filter_type": "in",
        "value": ["eroski-01013", "makro-01013"]
      },
      {
        "field": "Brand",
        "filter_type": "equal",
        "value": "Coca-Cola"
      },
      {
        "field": "price",
        "filter_type": "between",
        "value": [1.5, 3.5]
      },
      {
        "field": "Format",
        "filter_type": "contains",
        "value": "bottle"
      }
    ],
    "limit": 25
  }'
```

### Example 3: Python Client

```python
import requests
import json

# API base URL
BASE_URL = "http://localhost:8000"

# 1. Health check
health = requests.get(f"{BASE_URL}/health")
print("Health:", health.json())

# 2. Search products
search_payload = {
    "filters": [
        {"field": "store", "filter_type": "equal", "value": "eroski-01013"},
        {"field": "price", "filter_type": "less_than", "value": 10.0}
    ],
    "limit": 10
}
products = requests.post(f"{BASE_URL}/products/search", json=search_payload)
print("Products found:", len(products.json()["filtered_nodes"]))

# 3. Compare stores
compare_payload = {
    "store_a": "eroski-01013",
    "store_b": "makro-01013",
    "list_ids": ["12345", "67890"],
    "top_n_results": 5
}
comparison = requests.post(f"{BASE_URL}/compare", json=compare_payload)
print("Comparison results:", comparison.json())

# 4. Get product details
siid = "12345"
product = requests.get(f"{BASE_URL}/mongodb/product/{siid}")
print("Product:", product.json()["product"]["name"])

# 5. Upload file
with open("new_products.csv", "rb") as f:
    files = {"file": f}
    upload = requests.post(f"{BASE_URL}/upload", files=files)
    print("Upload status:", upload.json()["status"])
```

---

## Support & Contact

For issues, questions, or feature requests, please contact the development team or create an issue in the project repository.

**API Version**: 1.0.0  
**Last Updated**: December 18, 2025
