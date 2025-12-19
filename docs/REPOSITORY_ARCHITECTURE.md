# Repository Architecture & Troubleshooting Guide

## Table of Contents
1. [Repository Overview](#repository-overview)
2. [Directory Structure](#directory-structure)
3. [Module Dependencies](#module-dependencies)
4. [Process Flows & File Mapping](#process-flows--file-mapping)
5. [Critical Files Reference](#critical-files-reference)
6. [Troubleshooting Guide](#troubleshooting-guide)
7. [Development Workflow](#development-workflow)

---

## Repository Overview

This repository implements a **multi-store supermarket product intelligence system** that:
- Ingests product data from multiple supermarket chains
- Processes and enriches data using LLMs (Azure OpenAI)
- Stores data across three databases (MongoDB, PostgreSQL, Neo4j)
- Enables cross-store product similarity search using ML embeddings
- Provides a REST API for product comparison and data management

**Tech Stack**:
- **Language**: Python 3.10+
- **API Framework**: FastAPI
- **Databases**: MongoDB, PostgreSQL, Neo4j
- **ML/AI**: Azure OpenAI (GPT-5-nano for enrichment, text-embedding-3-small for vectors)
- **Data Processing**: Pandas, NumPy
- **Graph Database**: Neo4j with Cypher queries

---

## Directory Structure

```
ai_consumer_goods/
├── config/                          # Configuration files
│   ├── __init__.py
│   ├── settings.py                  # ⚙️ Central configuration (DB credentials, API keys)
│   ├── logs.py                      # Logging configuration
│   └── product_type_patterns.py     # Product classification patterns
│
├── data/                            # Data storage and schemas
│   ├── schemas/                     # Data structure definitions
│   │   ├── column_registry.json     # 📋 Master taxonomy definition
│   │   ├── taxonomy.py              # Taxonomy data classes
│   │   ├── categories.py            # Product categorization enums
│   │   └── neo4j_ontology.py        # Neo4j graph structure definition
│   ├── raw/                         # Uploaded raw files (csv, xlsx, xls)
│   ├── processed/                   # Processed files at each pipeline stage
│   │   ├── validated/               # Stage 1 output
│   │   ├── verified/                # Stage 2 output
│   │   ├── translated/              # Stage 3 output
│   │   ├── enriched/                # Stage 4 output
│   │   └── fixed/                   # Stage 5 output (final)
│   └── grid_search_results/         # ML model tuning results
│
├── src/                             # Source code
│   ├── api/                         # 🌐 REST API
│   │   ├── main.py                  # FastAPI application & endpoints
│   │   └── api_utils/
│   │       ├── upload_file.py       # 🔥 Pipeline orchestration
│   │       ├── neo4j_queries.py     # Dynamic Neo4j query builder
│   │       └── product_files.py     # Product file generation
│   │
│   ├── connectors/                  # 🔌 Database connectors
│   │   ├── neo4j_connector.py       # Neo4j connection management
│   │   ├── mongodb_connector.py     # MongoDB connection management
│   │   └── postgresql_connector.py  # PostgreSQL connection management
│   │
│   ├── preprocessors/               # 📝 Data preprocessing modules
│   │   ├── data_formatting/         # Stage 1: Validation & formatting
│   │   │   ├── column_validation.py # Column standardization
│   │   │   ├── product_verification.py  # 🔥 Stage 2: MongoDB sync
│   │   │   └── path_handling.py     # File path utilities
│   │   ├── text_translation/        # Stage 3: Translation
│   │   │   └── translator.py        # 🔥 Multi-language to English
│   │   └── data_autocompletion/     # Stages 4-5: Enrichment & fixing
│   │       ├── llm_completion.py    # 🔥 LLM-based field completion
│   │       ├── csv_fixing.py        # 🔥 Data cleaning & validation
│   │       └── stopwords.py         # Allergen/ingredient stopwords
│   │
│   ├── ingestion/                   # 💾 Database ingestion
│   │   ├── neo4j_ingest.py          # 🔥 Stage 7: Neo4j CSV ingestion
│   │   ├── nodes_relationships.py   # 🔥 Neo4j graph construction
│   │   └── neo4j_embeddings.py      # 🔥 Stage 8: Embedding generation
│   │
│   ├── numeric_variables_postgres/  # Stage 6: PostgreSQL
│   │   └── postgresql_data_extraction.py  # 🔥 Numerical data extraction
│   │
│   ├── models/                      # 🤖 ML models
│   │   ├── model_variations/        # Product similarity models
│   │   │   ├── get_similar_neo4j_refactored.py  # 🔥 Main similarity model
│   │   │   └── model.py             # Combined similarity logic
│   │   ├── category_analysis/       # Graph-based similarity
│   │   │   └── category_analysis.py
│   │   └── embedding_analysis/      # Embedding-based similarity
│   │       └── embedding_analysis.py
│   │
│   ├── model_exports/               # Model output utilities
│   │   └── export_utils.py          # CSV/Excel export functions
│   │
│   ├── utils/                       # General utilities
│   │   └── [various helper modules]
│   │
│   ├── validate_match/              # Product matching validation
│   └── relate_similars_terms/       # Term relationship analysis
│
├── tests/                           # 🧪 Test suite
│   ├── csv_comparison.py
│   ├── mongodb_ingestion.py
│   ├── neo4j_ingestion.py
│   └── test_data_extraction.py
│
├── scripts/                         # Standalone scripts
│
├── docs/                            # 📚 Documentation
│   ├── API_DOCUMENTATION.md         # REST API reference
│   ├── DATA_INGESTION_PIPELINE.md   # Pipeline detailed guide
│   └── REPOSITORY_ARCHITECTURE.md   # This file
│
├── venv10/                          # Python virtual environment
├── stack/                           # Docker infrastructure
│   ├── docker-compose.yml           # Database services
│   ├── mongodb/                     # MongoDB data & config
│   ├── neo4j/                       # Neo4j data & config
│   ├── postgres/                    # PostgreSQL data
│   └── pgadmin/                     # PostgreSQL admin interface
│
├── requirements.txt                 # Python dependencies
├── .env                             # 🔒 Environment variables (gitignored)
└── README.md                        # Project overview
```

🔥 = Critical file (modification requires careful testing)  
⚙️ = Configuration file (affects entire system)  
📋 = Schema/taxonomy file (changes cascade through system)

---

## Module Dependencies

### Configuration Layer
```
config/settings.py
    ↓ (imported by)
├── All API modules
├── All connector modules
├── All preprocessing modules
└── All ingestion modules
```

**What it does**: Centralizes all configuration (database credentials, API keys, paths)  
**Breaking changes**: Incorrect credentials or paths will break database connections

---

### Data Schema Layer
```
data/schemas/column_registry.json
    ↓ (loaded by)
├── taxonomy.py (parses JSON into Python classes)
    ↓ (imported by)
├── column_validation.py (validation rules)
├── product_verification.py (uptable columns)
├── translator.py (translatable columns)
├── llm_completion.py (LLM-fillable columns)
├── nodes_relationships.py (Neo4j mapping)
└── neo4j_queries.py (query generation)
```

**What it does**: Defines the master taxonomy - which columns exist, their roles, data types  
**Breaking changes**: Incorrect registry will cause ingestion failures, missing data, or incorrect graph structure

---

### Database Connector Layer
```
src/connectors/
├── neo4j_connector.py ────> Neo4j database
├── mongodb_connector.py ──> MongoDB database
└── postgresql_connector.py ─> PostgreSQL database
    ↑ (used by)
All modules that need database access
```

**What it does**: Manages database connections and sessions  
**Breaking changes**: Connection failures cascade to all dependent modules

---

### API Layer
```
src/api/main.py
    ├── Endpoints: /upload, /compare, /nodes_by_label, etc.
    ↓ (delegates to)
src/api/api_utils/upload_file.py
    ↓ (orchestrates)
Complete 8-stage pipeline
```

**What it does**: Exposes REST endpoints and orchestrates the pipeline  
**Breaking changes**: API endpoint changes require client updates

---

### Pipeline Flow Dependencies

```
upload_file.py (orchestrator)
    ↓
Stage 1: column_validation.py
    ↓
Stage 2: product_verification.py ──> MongoDB (CRUD operations)
    ↓
Stage 3: translator.py ──> Azure OpenAI (translation)
    ↓
Stage 4: llm_completion.py ──> Azure OpenAI (enrichment)
    ↓
Stage 5: csv_fixing.py
    ↓
Stage 6: postgresql_data_extraction.py ──> PostgreSQL (INSERT/UPDATE)
    ↓
Stage 7: neo4j_ingest.py
    ↓        └──> nodes_relationships.py ──> Neo4j (CREATE nodes/rels)
    ↓
Stage 8: neo4j_embeddings.py ──> Azure OpenAI (embeddings)
                              └──> Neo4j (UPDATE properties)
```

---

## Process Flows & File Mapping

### 1. Data Ingestion Pipeline (POST /upload)

| Stage | Files Involved | Purpose | Output |
|-------|----------------|---------|--------|
| **1. Validation** | `column_validation.py`<br>`path_handling.py`<br>`taxonomy.py` | Standardize columns, add required fields, clean data | `validated/*.csv` |
| **2. Verification** | `product_verification.py`<br>`mongodb_connector.py` | Compare with MongoDB, determine new/modified/identical | `verified/*.csv`<br>MongoDB updated |
| **3. Translation** | `translator.py`<br>Azure OpenAI API | Translate text fields to English | `translated/*.csv` |
| **4. Enrichment** | `llm_completion.py`<br>Azure OpenAI API | Fill missing fields using LLM | `enriched/*.csv` |
| **5. Fixing** | `csv_fixing.py`<br>`stopwords.py` | Clean ingredients, validate categorization | `fixed/*.csv` |
| **6. PostgreSQL** | `postgresql_data_extraction.py`<br>`postgresql_connector.py` | Extract numerical data, store for analytics | PostgreSQL table |
| **7. Neo4j Graph** | `neo4j_ingest.py`<br>`nodes_relationships.py`<br>`neo4j_connector.py` | Create graph structure (nodes + relationships) | Neo4j database |
| **8. Embeddings** | `neo4j_embeddings.py`<br>Azure OpenAI API<br>`neo4j_connector.py` | Generate semantic embeddings | Neo4j properties |

**Orchestrator**: `src/api/api_utils/upload_file.py` → `process_uploaded_file()`

---

### 2. Product Similarity Search (POST /compare)

| Component | Files Involved | Purpose |
|-----------|----------------|---------|
| **API Endpoint** | `main.py` → `/compare` endpoint | Receives comparison request |
| **Model Orchestrator** | `get_similar_neo4j_refactored.py` → `model()` | Coordinates similarity analysis |
| **Graph Analysis** | `category_analysis.py` | Calculates graph-based similarity (shared attributes) |
| **Semantic Analysis** | `embedding_analysis.py` | Calculates embedding-based similarity |
| **Combined Scoring** | `model.py` | Combines scores using weighted formula |
| **Results Export** | `export_utils.py` | Formats results to CSV/JSON |

**Flow**:
```
POST /compare
    ↓
main.py validates stores exist in Neo4j
    ↓
model() called with parameters
    ↓
CategoryAnalysis: Loads products from Neo4j, calculates graph similarity
    ↓
SimilarityAnalysis: Uses embeddings, calculates semantic similarity
    ↓
ModelCombinedSimilarity: Combines scores
    ↓
Returns: {product_id_a: [similar_product_ids_from_b]}
```

---

### 3. Neo4j Dynamic Querying (POST /nodes/filter, /products/search)

| Component | Files Involved | Purpose |
|-----------|----------------|---------|
| **API Endpoints** | `main.py` | Receives filter requests |
| **Query Builder** | `neo4j_queries.py` | Dynamically constructs Cypher queries |
| **Ontology** | `neo4j_ontology.py` | Defines graph schema |
| **Filter System** | `neo4j_queries.py` → `FilterTriplets` | Validates and applies filters |

**Key Functions**:
- `get_nodes_by_label()` - Simple label-based query
- `generate_query_for_filter_triplets()` - Complex filtering with multiple conditions
- `find_shortest_path_in_schema()` - BFS path finding between node types

---

### 4. Product File Generation (GET /mongodb/product-file/{siid})

| Component | Files Involved | Purpose |
|-----------|----------------|---------|
| **API Endpoint** | `main.py` → `/mongodb/product-file/{siid}` | Receives product request |
| **File Builder** | `product_files.py` → `get_product_file_by_siid()` | Builds comprehensive product file |
| **Data Sources** | MongoDB (product data)<br>Neo4j (matched products)<br>PostgreSQL (price history) | Aggregates from multiple databases |
| **Formatter** | Azure OpenAI API (optional) | Formats product info for readability |

---

## Critical Files Reference

### Configuration Files

#### `config/settings.py`
**Purpose**: Central configuration for entire system

**Contains**:
- Database connection strings (Neo4j, MongoDB, PostgreSQL)
- Azure OpenAI API credentials
- Project root path
- Logging configuration

**⚠️ Breaking if**:
- Database credentials incorrect → All database operations fail
- API keys invalid → Translation, enrichment, embeddings fail
- Paths wrong → File operations fail

**Troubleshooting**:
```python
# Verify database connections
from config.settings import NEO4J_CONFIG, MONGO_CONFIG, POSTGRES_CONFIG
print(f"Neo4j URI: {NEO4J_CONFIG['uri']}")
print(f"MongoDB URI: {MONGO_CONFIG['uri']}")
print(f"Postgres Host: {POSTGRES_CONFIG['host']}")
```

---

#### `data/schemas/column_registry.json`
**Purpose**: Master taxonomy defining all columns and their properties

**Contains**:
- Column definitions with roles (node, node_property, relationship_property)
- Translatable flags
- Uptable flags
- LLM completion status
- Data types

**⚠️ Breaking if**:
- Node/relationship mappings incorrect → Neo4j graph structure broken
- Uptable columns misconfigured → Unnecessary re-ingestion
- Translatable flags wrong → Translation skipped or applied incorrectly

**Troubleshooting**:
```python
from data.schemas.taxonomy import ColumnRegistry
registry = ColumnRegistry.load_from_json("data/schemas/column_registry.json")

# Check uptable columns
print("Uptable columns:", [col.name for col in registry.get_uptable_columns()])

# Check translatable columns
print("Translatable:", [col.name for col in registry.get_translatable_columns()])

# Check node columns
print("Node columns:", [(col.name, col.belongs_to) for col in registry.get_node_columns()])
```

---

### Pipeline Orchestration

#### `src/api/api_utils/upload_file.py`
**Purpose**: Orchestrates the entire 8-stage ingestion pipeline

**Key Function**: `process_uploaded_file(file, base_path)`

**Flow**:
1. Saves uploaded file to `data/raw/`
2. Calls Stage 1: Validation
3. Calls Stage 2: Verification
4. Early exit if no new/modified products
5. Calls Stage 3: Translation
6. Calls Stage 4: Enrichment
7. Calls Stage 5: Fixing
8. Calls Stage 6: PostgreSQL ingestion
9. Calls Stage 7: Neo4j ingestion
10. Calls Stage 8: Embeddings generation

**⚠️ Breaking if**:
- Stage sequence changed → Data inconsistencies
- Early exit logic modified incorrectly → Unnecessary processing
- Error handling removed → Pipeline crashes on failures

**Troubleshooting**:
- Check logs for stage where pipeline stopped
- Verify intermediate files exist in `data/processed/`
- Test each stage independently using stage-specific scripts

---

### Data Processing Modules

#### `src/preprocessors/data_formatting/column_validation.py`
**Purpose**: Stage 1 - Validates and standardizes data

**Key Functions**:
- `load_data_from_organized_structure()` - Loads CSV/Excel files
- `process_dataframe_standards()` - Main processing function
- `check_and_split_category_paths()` - Processes hierarchical categories
- `normalize_dataframe()` - Normalizes nutritional values

**⚠️ Breaking if**:
- Column name mapping changes → Downstream modules can't find columns
- Required columns not added → Database ingestion fails
- Normalization logic incorrect → Nutritional data wrong

**Troubleshooting**:
```bash
# Check validated file
cat data/processed/validated/<filename>_validated_*.csv | head

# Verify columns
python -c "import pandas as pd; df = pd.read_csv('data/processed/validated/<file>.csv', sep=';'); print(df.columns.tolist())"
```

---

#### `src/preprocessors/data_formatting/product_verification.py`
**Purpose**: Stage 2 - Compares with MongoDB, manages state

**Key Class**: `ProductVerifier`

**Key Methods**:
- `verify_csv()` - Main verification loop
- `compare_products()` - Compares CSV vs MongoDB products
- `delete_modified_products_from_neo4j()` - Syncs Neo4j with changes

**⚠️ Breaking if**:
- Uptable column list wrong → Unnecessary re-ingestion or missed updates
- Comparison logic flawed → Products incorrectly classified as new/modified/identical
- Neo4j deletion fails → Stale data in graph

**Troubleshooting**:
```python
# Check verification statistics
from src.preprocessors.data_formatting.product_verification import ProductVerifier
verifier = ProductVerifier()
# After running verify_csv:
verifier.print_summary()
# Shows: new_products, modified_products, identical_products, neo4j_deleted
```

---

#### `src/preprocessors/text_translation/translator.py`
**Purpose**: Stage 3 - Translates text to English

**Key Class**: `TranslatorOpenAI`

**Key Methods**:
- `translate_csv()` - Main entry point
- `should_translate_column()` - Determines if column needs translation
- `translate_text_batch_multithreaded()` - Parallel translation

**⚠️ Breaking if**:
- Language detection fails → Wrong source language used
- API rate limits exceeded → Translation incomplete
- Non-translatable patterns too broad → Important text skipped

**Troubleshooting**:
```python
# Test translation on single text
from src.preprocessors.text_translation.translator import TranslatorOpenAI
translator = TranslatorOpenAI(
    llm_api_key="your-key",
    llm_base_url="your-endpoint",
    predetermined_languages=['es', 'en']
)
result = translator.translate_text_with_llm("agua mineral", source_lang="es", target_lang="en")
print(result)  # Should output: "mineral water"
```

---

#### `src/preprocessors/data_autocompletion/llm_completion.py`
**Purpose**: Stage 4 - Fills missing fields using LLM

**Key Class**: `CSVCompleter`

**Key Methods**:
- `process_csv()` - Main entry point
- `create_completion_prompt_chunk()` - Builds LLM prompt
- `complete_chunk()` - Processes chunk of rows

**⚠️ Breaking if**:
- Prompt template incorrect → LLM returns invalid responses
- JSON parsing fails → Completion skipped
- Validation too strict → Valid completions rejected

**Troubleshooting**:
```python
# Test single row completion
from src.preprocessors.data_autocompletion.llm_completion import CSVCompleter
completer = CSVCompleter(
    api_key="your-key",
    deployment_name="gpt-5-nano",
    api_base="your-endpoint"
)
# Process single row
df = pd.DataFrame([{"product_name": "water bottle", "brand": "bezoya"}])
result = completer.complete_chunk(df)
print(result)
```

---

#### `src/preprocessors/data_autocompletion/csv_fixing.py`
**Purpose**: Stage 5 - Cleans and validates data

**Key Class**: `CSVFixer`

**Key Methods**:
- `fix_csv()` - Main entry point
- `process_ingredients_column()` - Cleans ingredient lists
- `process_allergens_column()` - Validates allergens
- `validate_and_fix_categorization()` - Fixes invalid categories

**⚠️ Breaking if**:
- Stopwords list incomplete → Valid ingredients removed
- Categorization validation too strict → Valid products rejected
- Measure field restoration fails → Numerical data lost

**Troubleshooting**:
```python
# Check allergen processing
from src.preprocessors.data_autocompletion.csv_fixing import CSVFixer
fixer = CSVFixer()
df = pd.DataFrame({"allergens": ["contains gluten, may contain nuts"]})
result = fixer.process_allergens_column(df)
print(result["allergens"][0])  # Should be: "gluten, nuts"
```

---

### Database Ingestion Modules

#### `src/numeric_variables_postgres/postgresql_data_extraction.py`
**Purpose**: Stage 6 - Extracts numerical data to PostgreSQL

**Key Class**: `VectorDataExtractor`

**Key Methods**:
- `process_input()` - Main processing function
- `extract_columns_from_csv()` - Selects relevant columns
- `insert_data()` - Batch inserts/updates

**⚠️ Breaking if**:
- Column type detection wrong → Data stored as wrong type
- Upsert logic fails → Duplicate records or lost updates
- Price history not maintained → Historical data lost

**Troubleshooting**:
```sql
-- Check PostgreSQL data
SELECT COUNT(*) FROM product_vector_data;
SELECT * FROM product_vector_data WHERE siid = 'bm-12345';
SELECT DISTINCT columns FROM information_schema.columns WHERE table_name = 'product_vector_data';
```

---

#### `src/ingestion/nodes_relationships.py`
**Purpose**: Stage 7 - Creates Neo4j graph structure

**Key Class**: `Neo4jNodesRelationshipsManager`

**Key Methods**:
- `process_products_batch()` - Main batch processing
- `_prepare_nodes_batch()` - Prepares node data
- `_create_nodes_batch()` - Executes MERGE queries
- `_prepare_relationships_batch()` - Prepares relationship data
- `_create_relationships_batch()` - Executes relationship MERGE

**⚠️ Breaking if**:
- Node identifier logic changed → Duplicate nodes created
- Property mapping incorrect → Missing node/relationship properties
- Batch UNWIND queries malformed → Ingestion fails

**Troubleshooting**:
```cypher
// Check Neo4j nodes created
MATCH (p:Product) RETURN COUNT(p);
MATCH (b:Brand) RETURN COUNT(b);
MATCH (p:Product {siid: 'bm-12345'}) RETURN p;

// Check relationships
MATCH (p:Product {siid: 'bm-12345'})-[r]->() RETURN type(r), COUNT(r);

// Find orphaned products (no relationships)
MATCH (p:Product) WHERE NOT (p)-[]->() RETURN p.siid;
```

---

#### `src/ingestion/neo4j_embeddings.py`
**Purpose**: Stage 8 - Generates semantic embeddings

**Key Functions**:
- `get_embedder()` - Initializes Azure OpenAI embedder
- `generate_embeddings_multithreaded()` - Parallel embedding generation
- `create_vector_index()` - Creates vector indexes

**⚠️ Breaking if**:
- Embedding dimension mismatch → Index creation fails
- API quota exceeded → Products missing embeddings
- Vector index not created → Similarity search unavailable

**Troubleshooting**:
```cypher
// Check embeddings created
MATCH (p:Product) 
WHERE p.name_embedding IS NOT NULL 
RETURN COUNT(p);

// Check embedding dimensions
MATCH (p:Product) 
WHERE p.name_embedding IS NOT NULL 
RETURN size(p.name_embedding) AS dimension 
LIMIT 1;

// List vector indexes
SHOW INDEXES YIELD name, type 
WHERE type = 'VECTOR';
```

---

### Model & Similarity Search

#### `src/models/model_variations/get_similar_neo4j_refactored.py`
**Purpose**: Main similarity model for product comparison

**Key Function**: `model(params: ModelParameters)`

**Dependencies**:
- `category_analysis.py` - Graph-based similarity
- `embedding_analysis.py` - Semantic similarity
- `model.py` - Score combination

**⚠️ Breaking if**:
- Weight configurations incorrect → Poor similarity results
- Threshold too strict → No matches found
- Score normalization wrong → Incorrect ranking

**Troubleshooting**:
```python
# Test similarity model
from src.models.model_variations.get_similar_neo4j_refactored import model, ModelParameters

params = ModelParameters(
    store_a="eroski-01013",
    store_b="makro-01013",
    list_ids=["product-id-1"],
    top_n_results=5
)

results = model(params)
print(results)
# Expected: {"product-id-1": ["similar-id-1", "similar-id-2", ...]}
```

---

## Troubleshooting Guide

### Common Issues & Solutions

---

### 🔴 Issue: Pipeline Fails at Stage 1 (Validation)

**Symptoms**:
- Error: "Invalid file format"
- Error: "Column not found"
- Validated CSV not created

**Possible Causes**:
1. File encoding issues (not UTF-8)
2. Delimiter incorrect (not semicolon or comma)
3. Required columns missing in source file
4. Excel file corrupted

**Troubleshooting Steps**:
```bash
# 1. Check file encoding
file -i data/raw/csv/yourfile.csv

# 2. Inspect first few lines
head -n 5 data/raw/csv/yourfile.csv

# 3. Check delimiter
python -c "import pandas as pd; df = pd.read_csv('data/raw/csv/yourfile.csv', sep=';', nrows=5); print(df.columns)"

# 4. Validate against registry
python -c "
from data.schemas.taxonomy import ColumnRegistry
registry = ColumnRegistry.load_from_json('data/schemas/column_registry.json')
required = [col.name for col in registry.get_required_columns()]
print('Required columns:', required)
"
```

**Solutions**:
- Convert file to UTF-8: `iconv -f ISO-8859-1 -t UTF-8 input.csv > output.csv`
- Fix delimiter: Ensure semicolon or comma used consistently
- Add missing columns: Manually add required columns with empty values

---

### 🔴 Issue: Pipeline Fails at Stage 2 (Verification)

**Symptoms**:
- Error: "Failed to connect to MongoDB"
- Error: "Verification failed"
- Verified CSV empty when it shouldn't be

**Possible Causes**:
1. MongoDB not running
2. MongoDB credentials incorrect
3. Uptable columns misconfigured
4. Neo4j deletion failed

**Troubleshooting Steps**:
```bash
# 1. Check MongoDB status
docker ps | grep mongo
# Or if running locally:
systemctl status mongod

# 2. Test MongoDB connection
python -c "
from src.connectors.mongodb_connector import get_mongo_client
db = get_mongo_client()
print('Connected:', db is not None)
print('Collections:', db.list_collection_names() if db else 'None')
"

# 3. Check uptable configuration
python -c "
from data.schemas.taxonomy import ColumnRegistry
registry = ColumnRegistry.load_from_json('data/schemas/column_registry.json')
uptable = [col.name for col in registry.get_uptable_columns()]
print('Uptable columns:', uptable)
"

# 4. Check Neo4j connection for deletion
python -c "
from src.connectors.neo4j_connector import Neo4jConnector
connector = Neo4jConnector()
connected = connector.connect_to_neo4j()
print('Neo4j connected:', connected)
"
```

**Solutions**:
- Start MongoDB: `docker-compose -f stack/docker-compose.yml up -d mongodb`
- Fix credentials in `.env` file
- Review uptable column list in `column_registry.json`
- Check Neo4j logs for deletion errors

---

### 🔴 Issue: Pipeline Fails at Stage 3 (Translation)

**Symptoms**:
- Error: "Azure OpenAI API error"
- Error: "Rate limit exceeded"
- Translation incomplete or skipped

**Possible Causes**:
1. Azure OpenAI API key invalid
2. Rate limits exceeded
3. Network connectivity issues
4. Language detection failures

**Troubleshooting Steps**:
```bash
# 1. Verify API credentials
python -c "
from config.settings import AZURE_OPENAI_CONFIG
print('API Key:', AZURE_OPENAI_CONFIG['api_key'][:10] + '...')
print('Endpoint:', AZURE_OPENAI_CONFIG['api_base'])
"

# 2. Test API connection
python -c "
from openai import OpenAI
from config.settings import AZURE_OPENAI_CONFIG
client = OpenAI(
    api_key=AZURE_OPENAI_CONFIG['api_key'],
    base_url=AZURE_OPENAI_CONFIG['api_base']
)
# Try simple request
response = client.chat.completions.create(
    model='gpt-5-nano',
    messages=[{'role': 'user', 'content': 'Hello'}],
    max_tokens=10
)
print('API working:', response.choices[0].message.content)
"

# 3. Check rate limits
curl -H "api-key: YOUR_API_KEY" \
  "https://YOUR_RESOURCE.openai.azure.com/openai/deployments/gpt-5-nano/chat/completions?api-version=2024-02-15-preview"
```

**Solutions**:
- Regenerate API key in Azure Portal
- Wait for rate limit reset (usually 1 minute)
- Reduce `max_workers` in translator (from 1000 to 100)
- Check Azure OpenAI service health status

---

### 🔴 Issue: Pipeline Fails at Stage 4 (Enrichment)

**Symptoms**:
- Error: "LLM completion failed"
- Error: "Invalid JSON response"
- Missing fields not filled

**Possible Causes**:
1. LLM prompt malformed
2. JSON parsing failed
3. Validation too strict
4. API timeout

**Troubleshooting Steps**:
```bash
# 1. Test single row enrichment
python -c "
from src.preprocessors.data_autocompletion.llm_completion import CSVCompleter
from config.settings import AZURE_OPENAI_CONFIG
import pandas as pd

completer = CSVCompleter(
    api_key=AZURE_OPENAI_CONFIG['api_key'],
    deployment_name=AZURE_OPENAI_CONFIG['deployment_name'],
    api_base=AZURE_OPENAI_CONFIG['api_base']
)

df = pd.DataFrame([{
    'product_name': 'natural mineral water',
    'brand': 'bezoya',
    'format': 'bottle'
}])

result = completer.complete_chunk(df)
print(result)
"

# 2. Check prompt generation
# Add debug print in llm_completion.py:create_completion_prompt_chunk()
# Print generated prompt before API call
```

**Solutions**:
- Review prompt template in `llm_completion.py`
- Add retry logic with exponential backoff
- Relax validation rules if too strict
- Increase API timeout (currently not configurable)

---

### 🔴 Issue: Pipeline Fails at Stage 6 (PostgreSQL)

**Symptoms**:
- Error: "Failed to connect to PostgreSQL"
- Error: "Column does not exist"
- Data not inserted

**Possible Causes**:
1. PostgreSQL not running
2. Credentials incorrect
3. Table schema mismatch
4. Data type conversion error

**Troubleshooting Steps**:
```bash
# 1. Check PostgreSQL status
docker ps | grep postgres
# Or locally:
systemctl status postgresql

# 2. Test connection
psql -h localhost -U postgres -d supermarket_analytics -c "SELECT 1;"

# 3. Check table schema
psql -h localhost -U postgres -d supermarket_analytics -c "\d product_vector_data"

# 4. Test data insertion
python -c "
from src.numeric_variables_postgres.postgresql_data_extraction import VectorDataExtractor
extractor = VectorDataExtractor()
connected = extractor.connect_db()
print('Connected:', connected)
if connected:
    print('Table exists:', extractor.table_exists())
"
```

**Solutions**:
- Start PostgreSQL: `docker-compose -f stack/docker-compose.yml up -d postgres`
- Fix credentials in `.env`
- Drop and recreate table: `DROP TABLE product_vector_data; -- Will be auto-recreated`
- Check data type conversion in `postgresql_data_extraction.py`

---

### 🔴 Issue: Pipeline Fails at Stage 7 (Neo4j Ingestion)

**Symptoms**:
- Error: "Failed to connect to Neo4j"
- Error: "Node creation failed"
- Products not appearing in Neo4j

**Possible Causes**:
1. Neo4j not running
2. Memory limits exceeded
3. Cypher query syntax error
4. Product hash missing

**Troubleshooting Steps**:
```bash
# 1. Check Neo4j status
docker ps | grep neo4j
# Or check Neo4j Browser: http://localhost:7474

# 2. Test connection
python -c "
from src.connectors.neo4j_connector import Neo4jConnector
connector = Neo4jConnector()
connected = connector.connect_to_neo4j()
print('Connected:', connected)
if connected:
    result = connector.test_connection()
    print('Node count:', result.get('node_count'))
"

# 3. Check product_hash in CSV
python -c "
import pandas as pd
df = pd.read_csv('data/processed/fixed/<file>_fixed_*.csv', sep=';')
print('Has product_hash:', 'product_hash' in df.columns)
print('Empty product_hash:', df['product_hash'].isna().sum())
"

# 4. Check Neo4j logs
docker logs stack_neo4j_1 --tail 100
```

**Solutions**:
- Start Neo4j: `docker-compose -f stack/docker-compose.yml up -d neo4j`
- Increase heap size in `stack/neo4j/conf/neo4j.conf`:
  ```
  dbms.memory.heap.initial_size=2G
  dbms.memory.heap.max_size=4G
  ```
- Verify Cypher syntax in `nodes_relationships.py`
- Regenerate product_hash in validation stage

---

### 🔴 Issue: Pipeline Fails at Stage 8 (Embeddings)

**Symptoms**:
- Error: "Embedding generation failed"
- Products missing `name_embedding` property
- Vector index not created

**Possible Causes**:
1. Azure OpenAI quota exceeded
2. Text too long for embedding model
3. Vector index configuration error
4. Neo4j out of memory

**Troubleshooting Steps**:
```bash
# 1. Check embeddings in Neo4j
cypher-shell -u neo4j -p your-password
# Then run:
MATCH (p:Product) WHERE p.name_embedding IS NOT NULL RETURN COUNT(p);

# 2. Test embedding generation
python -c "
from openai import OpenAI
from config.settings import AZURE_OPENAI_CONFIG

client = OpenAI(
    api_key=AZURE_OPENAI_CONFIG['api_key'],
    base_url=AZURE_OPENAI_CONFIG['api_base']
)

response = client.embeddings.create(
    model='text-embedding-3-small',
    input='test product'
)
print('Embedding dimension:', len(response.data[0].embedding))
"

# 3. Check vector indexes
SHOW INDEXES YIELD name, type WHERE type = 'VECTOR';
```

**Solutions**:
- Wait for API quota reset
- Truncate long texts before embedding (max 8191 tokens)
- Manually create vector index:
  ```cypher
  CREATE VECTOR INDEX product_name_embedding IF NOT EXISTS
  FOR (p:Product) ON (p.name_embedding)
  OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}
  ```
- Increase Neo4j memory allocation

---

### 🔴 Issue: Product Similarity Search Returns No Results

**Symptoms**:
- `/compare` endpoint returns empty results
- "No similar products found"

**Possible Causes**:
1. Quality thresholds too strict
2. Embeddings missing
3. Graph relationships incomplete
4. Stores don't exist in Neo4j

**Troubleshooting Steps**:
```bash
# 1. Verify stores exist
cypher-shell -u neo4j -p your-password
MATCH (s:Store) RETURN s.name;

# 2. Check products in both stores
MATCH (p:Product)-[:SOLD_IN]->(s:Store {name: 'eroski-01013'}) RETURN COUNT(p);
MATCH (p:Product)-[:SOLD_IN]->(s:Store {name: 'makro-01013'}) RETURN COUNT(p);

# 3. Check embeddings
MATCH (p:Product)-[:SOLD_IN]->(:Store {name: 'eroski-01013'})
WHERE p.name_embedding IS NOT NULL
RETURN COUNT(p);

# 4. Test with relaxed thresholds
curl -X POST http://localhost:8000/compare \
  -H "Content-Type: application/json" \
  -d '{
    "store_a": "eroski-01013",
    "store_b": "makro-01013",
    "list_ids": ["product-id"],
    "top_n_results": 10,
    "quality_thresholds": {
      "min_graph_score": 0.1,
      "min_name_similarity": 0.1,
      "min_description_similarity": 0.1
    }
  }'
```

**Solutions**:
- Lower quality thresholds in request
- Verify embeddings generated for both stores
- Check graph relationships are complete
- Ensure products from both stores have common categories

---

### 🔴 Issue: Duplicate Products in Neo4j

**Symptoms**:
- Same product appears multiple times in Neo4j
- Count in Neo4j > count in CSV

**Possible Causes**:
1. `product_hash` not used as identifier
2. Multiple ingestions without cleanup
3. `siid` duplicates in CSV

**Troubleshooting Steps**:
```cypher
// Find duplicate products by name
MATCH (p:Product)
WITH p.name AS name, COUNT(p) AS count
WHERE count > 1
RETURN name, count
ORDER BY count DESC;

// Find products with duplicate siid
MATCH (p:Product)
WITH p.siid AS siid, COUNT(p) AS count
WHERE count > 1
RETURN siid, count;

// Check identifier used
MATCH (p:Product) RETURN p.product_hash, p.siid LIMIT 10;
```

**Solutions**:
- Ensure `product_hash` column exists and is populated in CSV
- Delete duplicates:
  ```cypher
  // Keep first occurrence, delete rest
  MATCH (p:Product)
  WITH p.siid AS siid, COLLECT(p) AS products
  WHERE SIZE(products) > 1
  FOREACH (product IN TAIL(products) | DETACH DELETE product)
  ```
- Clear Neo4j and re-ingest:
  ```cypher
  MATCH (n) DETACH DELETE n;
  ```

---

## Development Workflow

### Setting Up Development Environment

1. **Clone Repository**
   ```bash
   git clone <repository-url>
   cd ai_consumer_goods
   ```

2. **Create Virtual Environment**
   ```bash
   python3.10 -m venv venv10
   source venv10/bin/activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

5. **Start Database Services**
   ```bash
   cd stack
   docker-compose up -d
   ```

6. **Verify Services Running**
   ```bash
   docker ps
   # Should see: mongodb, neo4j, postgres, pgadmin
   ```

7. **Start API Server**
   ```bash
   cd src/api
   python main.py
   # Or: uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

---

### Testing Changes

#### Test Individual Pipeline Stages

**Stage 1 - Validation**:
```bash
python -c "
from src.preprocessors.data_formatting.column_validation import process_dataframe_standards
import pandas as pd

df = pd.read_csv('data/raw/csv/test.csv', sep=';')
result = process_dataframe_standards(df, null_replacement='', filename='test.csv')
result.to_csv('test_validated.csv', sep=';', index=False)
print('Validated columns:', result.columns.tolist())
"
```

**Stage 2 - Verification**:
```bash
python -c "
from src.preprocessors.data_formatting.product_verification import verify_products

success = verify_products(
    'data/processed/validated/test_validated.csv',
    'data/processed/verified/test_verified.csv'
)
print('Verification success:', success)
"
```

**Stage 3 - Translation**:
```bash
python -c "
from src.preprocessors.text_translation.translator import TranslatorOpenAI
from config.settings import AZURE_OPENAI_CONFIG

translator = TranslatorOpenAI(
    llm_api_key=AZURE_OPENAI_CONFIG['api_key'],
    llm_base_url=AZURE_OPENAI_CONFIG['api_base'],
    predetermined_languages=['es', 'en']
)

results = translator.translate_csv(
    'data/processed/verified/test_verified.csv',
    'data/processed/translated/test_translated.csv'
)
print('Translation results:', results)
"
```

---

#### Test API Endpoints

**Upload File**:
```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@data/raw/csv/test.csv"
```

**Product Comparison**:
```bash
curl -X POST http://localhost:8000/compare \
  -H "Content-Type: application/json" \
  -d '{
    "store_a": "eroski-01013",
    "store_b": "makro-01013",
    "list_ids": ["bm-12345"],
    "top_n_results": 5
  }'
```

**Search Products**:
```bash
curl -X POST http://localhost:8000/products/search \
  -H "Content-Type: application/json" \
  -d '{
    "filters": [
      {"field": "store", "filter_type": "equal", "value": "eroski-01013"},
      {"field": "price", "filter_type": "less_than", "value": 5.0}
    ],
    "limit": 10
  }'
```

---

### Making Changes Safely

#### Before Modifying Critical Files:

1. **Create a Git branch**
   ```bash
   git checkout -b feature/your-change
   ```

2. **Backup current data** (if testing with real data)
   ```bash
   # Backup MongoDB
   docker exec stack_mongodb_1 mongodump --out /tmp/backup

   # Backup Neo4j (stop Neo4j first)
   docker exec stack_neo4j_1 neo4j-admin dump --database=neo4j --to=/tmp/neo4j-backup.dump

   # Backup PostgreSQL
   docker exec stack_postgres_1 pg_dump -U postgres supermarket_analytics > postgres_backup.sql
   ```

3. **Test with small dataset first**
   - Use 10-20 row CSV file
   - Verify output at each stage
   - Check database state after ingestion

4. **Run tests**
   ```bash
   cd tests
   python test_data_extraction.py
   python mongodb_ingestion.py
   python neo4j_ingestion.py
   ```

5. **Review logs carefully**
   - Check for warnings
   - Verify no data loss
   - Confirm expected behavior

---

### Common Modification Scenarios

#### Adding a New Column to Taxonomy

**Files to modify**:
1. `data/schemas/column_registry.json` - Add column definition
2. `data/schemas/taxonomy.py` - Update NodeTypes or RelationshipTypes enum if needed
3. `data/schemas/categories.py` - Update if new category/subcategory

**Testing**:
```bash
# Validate JSON
python -c "
from data.schemas.taxonomy import ColumnRegistry
registry = ColumnRegistry.load_from_json('data/schemas/column_registry.json')
print('Valid:', registry is not None)
"

# Test end-to-end with small file
curl -X POST http://localhost:8000/upload -F "file=@test.csv"
```

---

#### Modifying Translation Logic

**Files to modify**:
1. `src/preprocessors/text_translation/translator.py`

**Testing**:
```python
# Test translation independently
from src.preprocessors.text_translation.translator import TranslatorOpenAI

translator = TranslatorOpenAI(...)
result = translator.translate_text_with_llm("test text", "es", "en")
assert result != "test text"  # Should be translated
```

---

#### Changing Neo4j Graph Structure

**Files to modify**:
1. `data/schemas/column_registry.json` - Update node/relationship mappings
2. `src/ingestion/nodes_relationships.py` - Update logic if needed
3. `data/schemas/neo4j_ontology.py` - Update schema definition

**Testing**:
```cypher
// Before change - document current structure
CALL db.schema.visualization();

// After change - verify new structure
MATCH (p:Product)-[r]->()
RETURN DISTINCT type(r), labels(p);

// Verify no orphaned nodes
MATCH (n) WHERE NOT (n)-[]-()
RETURN labels(n), COUNT(n);
```

---

## Performance Monitoring

### Key Metrics to Track

1. **Pipeline Processing Time**
   - Track time per stage
   - Identify bottlenecks
   - Target: < 10 minutes for 400 products

2. **Database Performance**
   - Neo4j query times
   - MongoDB query times
   - PostgreSQL insert times

3. **API Costs**
   - Azure OpenAI token usage
   - Translation costs
   - Embedding generation costs

4. **Data Quality**
   - Products with missing embeddings
   - Failed translations
   - Invalid categorizations

### Monitoring Tools

**Neo4j Monitoring**:
```cypher
// Query performance
PROFILE MATCH (p:Product)-[:HAS_BRAND]->(b:Brand {name: 'Coca-Cola'}) RETURN p;

// Memory usage
CALL dbms.listQueries() YIELD query, elapsedTimeMillis, allocatedBytes;

// Index usage
CALL db.indexes() YIELD name, state, populationPercent;
```

**MongoDB Monitoring**:
```bash
# Connection info
mongosh --eval "db.serverStatus()"

# Collection stats
mongosh supermarket_db --eval "db.bm_products.stats()"
```

**PostgreSQL Monitoring**:
```sql
-- Table sizes
SELECT
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public';

-- Query performance
SELECT * FROM pg_stat_user_tables WHERE relname = 'product_vector_data';
```

---

## Appendix: File Change Impact Matrix

| File Modified | Requires Testing | May Break | Impact Level |
|---------------|------------------|-----------|--------------|
| `config/settings.py` | All endpoints | All database operations | 🔴 CRITICAL |
| `column_registry.json` | Full pipeline | Validation, Neo4j, all stages | 🔴 CRITICAL |
| `upload_file.py` | `/upload` endpoint | Complete pipeline flow | 🔴 CRITICAL |
| `product_verification.py` | Stage 2 | MongoDB sync, Neo4j sync | 🟠 HIGH |
| `nodes_relationships.py` | Stage 7 | Neo4j graph structure | 🟠 HIGH |
| `translator.py` | Stage 3 | Translation quality | 🟡 MEDIUM |
| `llm_completion.py` | Stage 4 | Data enrichment | 🟡 MEDIUM |
| `csv_fixing.py` | Stage 5 | Data quality | 🟡 MEDIUM |
| `neo4j_queries.py` | Filter endpoints | Product search | 🟡 MEDIUM |
| `get_similar_neo4j_refactored.py` | `/compare` | Similarity search | 🟡 MEDIUM |
| `export_utils.py` | Result export | Output formatting | 🟢 LOW |

---

## Support & Maintenance

### Regular Maintenance Tasks

**Daily**:
- Monitor API logs for errors
- Check database disk usage
- Verify service health (`/health` endpoint)

**Weekly**:
- Review failed ingestions
- Clean up old processed files
- Check embedding generation coverage

**Monthly**:
- Update dependencies (`pip list --outdated`)
- Review and optimize slow queries
- Archive old data

**Quarterly**:
- Review and update taxonomy
- Audit data quality metrics
- Optimize database indexes

---

**Version**: 1.0.0  
**Last Updated**: December 18, 2025  
**Maintained By**: AI Consumer Goods Team
