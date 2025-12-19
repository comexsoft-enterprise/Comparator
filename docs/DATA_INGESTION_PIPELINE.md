# Data Ingestion Pipeline Documentation

## Overview

This document describes the complete data ingestion pipeline accessible through the API endpoint `POST /upload`. The pipeline processes supermarket product data files (CSV, XLSX, XLS) and ingests them into three databases: **MongoDB**, **PostgreSQL**, and **Neo4j**, with semantic embeddings for similarity search.

**API Endpoint**: `POST http://localhost:8000/upload`

**Supported File Formats**: CSV, XLSX, XLS

---

## Pipeline Architecture

The pipeline consists of 8 sequential stages that transform raw product data into a fully integrated, searchable dataset:

```
Raw File Upload
    ↓
1. File Validation & Standardization
    ↓
2. Product Verification & MongoDB Ingestion
    ↓
3. Translation (Multi-language → English)
    ↓
4. Data Enrichment (LLM-based completion)
    ↓
5. Data Fixing & Validation
    ↓
6. PostgreSQL Ingestion (Numerical data)
    ↓
7. Neo4j Ingestion (Graph structure)
    ↓
8. Embeddings Generation (Semantic search)
```

---

## Stage 1: File Validation & Standardization

### Purpose
Validate the uploaded file format, clean column names, add required columns, and prepare data for downstream processing.

### Implementation
**Module**: `src/preprocessors/data_formatting/column_validation.py`

**Functions**:
- `load_data_from_organized_structure()` - Loads and parses CSV/Excel files
- `process_dataframe_standards()` - Applies standardization rules

### Process

1. **File Type Detection**
   - Identifies file extension (csv, xlsx, xls)
   - Validates file can be read
   - Moves file to organized structure: `data/raw/<file_type>/`

2. **Column Name Standardization**
   - Converts all column names to lowercase
   - Removes special characters and spaces
   - Ensures consistency with taxonomy

3. **Required Columns Addition**
   - Adds missing required columns defined in `column_registry.json`
   - Columns include: `uuid`, `siid`, `product_hash`, `store`, `country`, etc.

4. **Category Path Processing**
   - Splits hierarchical category paths (e.g., `beverages/water/still water`)
   - Extracts: `category`, `internalcategory`, `internalsubcategory`
   - Normalizes category values to match taxonomy

5. **Data Cleaning**
   - Replaces `None`, `NaN`, `null` strings with empty strings
   - Cleans cell characters (converts line breaks to `\n`)
   - Removes duplicate rows

6. **Product Hash Generation**
   - Creates SHA256 hash from non-uptable columns
   - Used as unique identifier across databases
   - Excludes columns marked as `uptable` in registry (e.g., `price`, `offer`)

7. **Price Calculations**
   - Calculates `price_without_vat` if missing
   - Normalizes VAT percentages
   - Validates price consistency

8. **Nutritional Value Normalization**
   - Standardizes units for nutritional values
   - Converts all values to base units (g, mg, kcal)
   - Ensures consistency for vector embeddings

### Output
**File**: `data/processed/validated/<filename>_validated_<timestamp>.csv`

**Result**: Clean, standardized CSV with all required columns and normalized values

---

## Stage 2: Product Verification & MongoDB Ingestion

### Purpose
Compare products with existing MongoDB data to determine if they are new, modified, or identical. Manage MongoDB state and track changes for Neo4j synchronization.

### Implementation
**Module**: `src/preprocessors/data_formatting/product_verification.py`

**Class**: `ProductVerifier`

### Process

1. **MongoDB Connection**
   - Connects to MongoDB database
   - Derives collection name from filename (e.g., `bm_products`)
   - Loads existing products by `product_hash`

2. **Uptable Columns Loading**
   - Loads columns marked as `uptable` from `column_registry.json`
   - These columns can change without triggering re-ingestion
   - Examples: `price`, `offer`, `price_with_offer`, `shipping_cost`

3. **Batch Product Loading**
   - Loads all existing products from MongoDB in single query (optimized)
   - Creates hash map: `product_hash` → product document
   - Avoids N+1 query problem

4. **Product Comparison Logic**

   For each product in CSV:

   **Case A: New Product**
   - `product_hash` not found in MongoDB
   - **Action**: Insert into MongoDB, keep in output CSV
   - **Reason**: Needs to be ingested into Neo4j

   **Case B: Identical Product**
   - `product_hash` exists in MongoDB
   - All non-uptable columns match exactly
   - **Action**: Skip (exclude from output CSV)
   - **Reason**: Already in both MongoDB and Neo4j

   **Case C: Modified Product**
   - `product_hash` exists in MongoDB
   - At least one non-uptable column differs
   - **Action**: Replace in MongoDB, track for Neo4j deletion, keep in output CSV
   - **Reason**: Needs to be re-ingested into Neo4j with updated data

5. **MongoDB Operations**
   - **INSERT**: New products inserted with all fields
   - **REPLACE**: Modified products replaced completely (not just updated fields)
   - **TIMESTAMP**: Adds `updated_at` timestamp to all documents

6. **Neo4j Deletion Tracking**
   - Modified products tracked in `modified_product_hashes` list
   - Batch deletion executed at end of verification
   - Cypher query: `MATCH (p:Product {product_hash: $hash}) DETACH DELETE p`

7. **Value Normalization**
   - Handles various null representations: `None`, `NaN`, `'null'`, `''`
   - Ensures accurate comparison between CSV and MongoDB
   - Float precision handling for numerical values

### Statistics Tracked
- `total_products`: Total products processed
- `new_products`: Products inserted into MongoDB
- `modified_products`: Products replaced in MongoDB and deleted from Neo4j
- `identical_products`: Products skipped (no changes)
- `errors`: Products with processing errors
- `neo4j_deleted`: Products successfully deleted from Neo4j

### Output
**File**: `data/processed/verified/<filename>_verified_<timestamp>.csv`

**Result**: CSV containing only new and modified products that need Neo4j ingestion

**MongoDB State**: Contains ALL products (both new and existing)

**Neo4j State**: Modified products removed, ready for re-ingestion

---

## Stage 3: Translation

### Purpose
Translate product data from source languages (Spanish, Catalan, etc.) to English for consistency and improved LLM processing.

### Implementation
**Module**: `src/preprocessors/text_translation/translator.py`

**Class**: `TranslatorOpenAI`

### Process

1. **Translatable Columns Identification**
   - Loads `column_registry.json`
   - Identifies columns marked as `translatable=true`
   - Examples: `product_name`, `description`, `ingredients`, `category`

2. **Language Detection**
   - Uses `langdetect` library
   - Detects source language per cell
   - Skips already-English content

3. **Non-Translatable Content Filtering**

   Skips translation for:
   - Short strings (< 4 characters)
   - Product codes (alphanumeric with numbers)
   - URLs
   - Email addresses
   - Phone numbers
   - Pure numerical values
   - Empty/null values

4. **Translation Strategy**

   **Multi-threaded Translation**:
   - Processes multiple texts in parallel (default: 1000 workers)
   - Uses Azure OpenAI API (GPT-5-nano model)
   - Batches texts for efficiency

   **LLM Prompting**:
   ```
   Translate the following text from {source_lang} to English.
   Only return the translation, nothing else.
   Text: {text}
   ```

5. **Column-by-Column Processing**
   - Detects predominant language in each column
   - Translates only if column is not predominantly English
   - Preserves original value if translation fails

6. **Quality Checks**
   - Validates translation output
   - Falls back to original text on errors
   - Logs translation failures for review

7. **Progress Tracking**
   - Shows progress bar for each column
   - Reports translation statistics
   - Tracks API usage and costs

### Translation Examples

| Original (Spanish) | Translated (English) |
|-------------------|----------------------|
| agua mineral natural | natural mineral water |
| botella 1.5 l | bottle 1.5 l |
| bebida refrescante de naranja | orange soft drink |
| ingredientes: agua, azúcar | ingredients: water, sugar |

### Output
**File**: `data/processed/translated/<filename>_translated_<timestamp>.csv`

**Result**: Fully translated CSV with English content for LLM processing

---

## Stage 4: Data Enrichment (LLM-based Completion)

### Purpose
Use Azure OpenAI LLM to intelligently fill missing or incomplete product information based on available context.

### Implementation
**Module**: `src/preprocessors/data_autocompletion/llm_completion.py`

**Class**: `CSVCompleter`

### Process

1. **Empty Field Detection**
   - Scans each row for empty/null fields
   - Identifies fillable columns from `column_registry.json`
   - Columns marked with `llm_status="must_fill"` or `llm_status="can_fill"`

2. **Chunk-based Processing**
   - Processes rows in chunks (default: 10 rows)
   - Reduces API calls and improves efficiency
   - Each chunk processed independently

3. **Prompt Generation**

   **Input Columns** (provided as context):
   - `product_name`, `description`, `category`
   - `brand`, `format`, `quantity`, `measure_value`
   - `ingredients`, `allergens`, `components`

   **Output Columns** (to be filled):
   - `internaltype` (FOOD or NON-FOOD)
   - `internalcategory` (specific category)
   - `internalsubcategory` (granular subcategory)
   - Other missing taxonomy fields

   **Prompt Template**:
   ```
   You are an expert in product categorization for supermarkets.
   
   VALID INTERNAL TYPES:
   - FOOD
   - NON-FOOD
   
   VALID CATEGORIES FOR FOOD:
   - water and soft drinks
   - dairy products
   - meat and fish
   [... full list ...]
   
   VALID SUBCATEGORIES FOR "water and soft drinks":
   - water
   - cola soft drink
   - orange and lemon soft drink
   [... full list ...]
   
   INPUT DATA:
   Row 1:
     product_name: natural mineral water bottle
     brand: bezoya
     format: bottle
     measure_value: 1.5
     unit_measure: l
     
   TASK: Fill missing fields with appropriate values.
   Return ONLY valid JSON matching the schema.
   ```

4. **LLM Processing**
   - Uses Azure OpenAI (GPT-5-nano or configured model)
   - Structured JSON output enforced
   - Temperature: 0.0 (deterministic)
   - Max tokens: 2000

5. **Response Parsing**
   - Parses JSON response from LLM
   - Validates against taxonomy constraints
   - Rejects invalid values (falls back to empty)

6. **Validation & Quality Control**
   - Ensures `internaltype` matches `internalcategory`
     - FOOD → must be food category
     - NON-FOOD → must be non-food category
   - Validates `internalsubcategory` is valid for `internalcategory`
   - Checks all values against allowed enums

7. **Multi-threaded Execution**
   - Processes chunks in parallel (default: 1000 workers)
   - Progress bar with real-time updates
   - Automatic retry on failures (max 3 retries)

8. **Cost Tracking**
   - Tracks total tokens used (input + output)
   - Calculates cost based on model pricing
   - Reports summary at completion

### Output
**File**: `data/processed/enriched/<filename>_enriched_<timestamp>.csv`

**Result**: CSV with LLM-filled missing fields and validated taxonomy

**Typical Completion Rate**: 85-95% of empty fields successfully filled

---

## Stage 5: Data Fixing & Validation

### Purpose
Clean, normalize, and validate data quality. Fix categorization errors, process ingredients/allergens, and merge with translated data.

### Implementation
**Module**: `src/preprocessors/data_autocompletion/csv_fixing.py`

**Class**: `CSVFixer`

### Process

1. **Ingredient Processing**

   **Extraction**:
   - Parses ingredient lists (comma/semicolon separated)
   - Extracts ingredient name and extra info
   - Removes stopwords ("contains", "may contain", etc.)

   **Separation**:
   - `ingredients` → Clean ingredient names
   - `ingredients_extra_info` → Percentages, E-numbers, additives
   - `first_level_components` → Primary ingredients
   - `second_level_components` → Secondary ingredients

   **Example**:
   ```
   Input:  "water (80%), sugar, natural flavoring (E330)"
   Output:
     ingredients: "water, sugar, natural flavoring"
     ingredients_extra_info: "80%, E330"
     first_level_components: "water"
     second_level_components: "sugar"
   ```

2. **Allergen Processing**

   **Cleaning**:
   - Removes stopwords and noise words
   - Normalizes allergen names
   - Identifies unknown allergens

   **Validation**:
   - Checks against known allergen list
   - Flags unknown allergens for review
   - Formats as comma-separated list

   **Common Allergens**:
   - gluten, milk, eggs, fish, shellfish
   - nuts, peanuts, soy, sesame
   - sulfites, celery, mustard, lupin

3. **Categorization Validation & Fixing**

   **Validation Rules**:
   - `internaltype` must be valid (FOOD or NON-FOOD)
   - `internalcategory` must be valid and match type
   - `internalsubcategory` must be valid for the category

   **LLM-based Correction**:
   - For invalid categorizations, uses LLM to suggest corrections
   - Provides product context and valid options
   - Multi-threaded processing for efficiency

   **Prompt Template**:
   ```
   Product has invalid categorization:
   - product_name: orange juice
   - Current internaltype: NON-FOOD (INVALID)
   - Current internalcategory: electronics (INVALID)
   
   Valid types: FOOD, NON-FOOD
   Valid food categories: [list]
   
   Suggest correct categorization.
   ```

4. **Measure Field Restoration**
   - Restores `measure_value`, `unit_measure` from translated file
   - Translation sometimes corrupts numerical values
   - Ensures data integrity for these critical fields

5. **Data Comparison & Gap Filling**
   - Compares enriched and translated files
   - Fills gaps from translated file if enriched has empties
   - Prioritizes enriched data when both present

6. **Dataframe Normalization**
   - Ensures all required columns present
   - Adds `store` and `postcode` if provided
   - Reorders columns to standard schema

7. **Final Validation**
   - Validates all taxonomy constraints
   - Ensures data quality before database ingestion
   - Generates quality report

### Output
**File**: `data/processed/fixed/<filename>_fixed_<timestamp>.csv`

**Result**: Fully validated, clean CSV ready for database ingestion

---

## Stage 6: PostgreSQL Ingestion

### Purpose
Extract and store numerical and analytical data (prices, nutritional values) in PostgreSQL for vector embedding generation and analytics.

### Implementation
**Module**: `src/numeric_variables_postgres/postgresql_data_extraction.py`

**Class**: `VectorDataExtractor`

**Function**: `ingest_verified_csv()`

### Process

1. **Column Selection**
   - Extracts specific columns needed for vector embeddings:
     - **Identifiers**: `uuid`, `siid`, `product_hash`
     - **Nutritional**: All columns containing `nutri` (case-insensitive)
     - **Measures**: `measure_value`, `unit_measure`
     - **Prices**: All columns containing `price`

2. **Table Schema**
   
   **Table**: `product_vector_data`
   
   **Core Columns**:
   ```sql
   uuid TEXT PRIMARY KEY
   siid TEXT
   product_hash TEXT
   measure_value NUMERIC(10,2)
   unit_measure TEXT
   price NUMERIC(10,2)
   price_without_vat NUMERIC(10,2)
   vat INTEGER
   ```

   **Nutritional Columns** (dynamic):
   ```sql
   nutri_energy_kcal_100g NUMERIC(10,2)
   nutri_energy_kj_100g NUMERIC(10,2)
   nutri_fat_g_100g NUMERIC(10,2)
   nutri_saturated_fat_g_100g NUMERIC(10,2)
   nutri_carbohydrates_g_100g NUMERIC(10,2)
   nutri_sugar_g_100g NUMERIC(10,2)
   nutri_protein_g_100g NUMERIC(10,2)
   nutri_salt_g_100g NUMERIC(10,2)
   [... other nutritional values ...]
   ```

   **Price History Column**:
   ```sql
   price_history JSONB
   ```

3. **Dynamic Schema Management**
   - Automatically creates table if not exists
   - Adds new columns dynamically when found in CSV
   - Determines appropriate data types:
     - `INTEGER` for VAT
     - `NUMERIC(10,2)` for prices, nutritional values, measures
     - `TEXT` for identifiers and units
     - `JSONB` for price history

4. **Data Transformation**
   - Converts string values to appropriate numeric types
   - Handles missing values (stored as NULL)
   - Normalizes numerical precision

5. **Price History Tracking**
   - Maintains historical price data in JSONB format
   - Structure:
   ```json
   {
     "history": [
       {
         "date": "2025-12-18",
         "price": 2.5,
         "store": "eroski-01013",
         "source": "csv_ingestion"
       }
     ],
     "last_updated": "2025-12-18T10:30:00"
   }
   ```

6. **Upsert Logic**
   - Uses `uuid` as primary key
   - Updates existing records if `uuid` matches
   - Inserts new records if `uuid` not found
   - Preserves price history during updates

7. **Batch Processing**
   - Processes records in batches (default: 500 rows)
   - Optimizes database performance
   - Reduces connection overhead

8. **Index Creation**
   - Creates indexes on key columns:
     - `uuid` (primary key)
     - `siid`
     - `product_hash`
   - Optimizes query performance

### Output
**Database**: PostgreSQL `product_vector_data` table

**Records**: Numerical and analytical data for vector embedding generation

**Use Cases**:
- Vector similarity search (nutritional profiles)
- Price analytics and tracking
- Product comparison by numerical attributes

---

## Stage 7: Neo4j Ingestion (Graph Database)

### Purpose
Create a rich graph structure representing products, brands, categories, ingredients, and their relationships for semantic search and product discovery.

### Implementation
**Module**: `src/ingestion/nodes_relationships.py`

**Class**: `Neo4jNodesRelationshipsManager`

### Graph Schema

#### Node Types
- **Product** - Individual product items
- **Brand** - Product brands (Coca-Cola, Nestlé, etc.)
- **Store** - Supermarket chains (Eroski, Carrefour, etc.)
- **Format** - Package formats (Bottle, Can, Box, etc.)
- **Internal_Category** - Product categories
- **Internal_Subcategory** - Product subcategories
- **Unit_Measure** - Measurement units (L, kg, g, etc.)
- **First_Level_Ingredient/Component** - Primary ingredients
- **Second_Level_Ingredient/Component** - Secondary ingredients
- **Other_Ingredient/Component** - Additional ingredients
- **Allergen** - Allergen information
- **Country** - Country of origin

#### Relationship Types
- `HAS_BRAND` - Product → Brand
- `SOLD_IN` - Product → Store
- `HAS_FORMAT` - Product → Format
- `HAS_INTERNAL_CATEGORY` - Product → Internal_Category
- `HAS_INTERNAL_SUBCATEGORY` - Product → Internal_Subcategory
- `HAS_UNIT_MEASURE` - Product → Unit_Measure
- `FIRST_LEVEL_INGREDIENT` - Product → First_Level_Ingredient
- `SECOND_LEVEL_INGREDIENT` - Product → Second_Level_Ingredient
- `ALLERGENS` - Product → Allergen
- `MANUFACTURED_IN` - Product → Country

### Process

1. **Column Registry Loading**
   - Loads `column_registry.json` taxonomy
   - Identifies node source columns (`representation: "node"`)
   - Identifies node property columns (`representation: "node_property"`)
   - Identifies relationship property columns (`representation: "relationship_property"`)

2. **Product Identifier Selection**
   - Prefers `product_hash` as unique identifier
   - Falls back to `siid` if `product_hash` missing
   - `product_hash` ensures global uniqueness across stores

3. **Node Preparation (Batch Processing)**

   For each product:
   
   **Product Node**:
   - Identifier: `product_hash` or `siid`
   - Properties: All node properties from registry
   - Example properties: `name`, `description`, `price`, `quantity`

   **Related Nodes**:
   - Extracts values from source columns
   - Handles comma-separated values (e.g., multiple brands)
   - Creates separate nodes for each unique value

   **Duplicate Detection**:
   - Uses sets to track seen identifiers per node type
   - Prevents duplicate node creation within batch
   - Example: "Coca-Cola" brand created once even if in 100 products

4. **Node Creation (UNWIND Batch Cypher)**

   Uses optimized Cypher queries:
   ```cypher
   UNWIND $batch AS node
   MERGE (n:Brand {name: node.identifier})
   SET n.description = node.properties.description,
       n.country = node.properties.country
   ```

   **Benefits**:
   - Single database roundtrip per node type
   - Automatic deduplication via `MERGE`
   - Efficient property updates

5. **Relationship Preparation**

   For each product-to-node connection:
   - Identifies source and target node identifiers
   - Collects relationship properties from registry
   - Handles special relationships:
     - Ingredient lists → Multiple relationships
     - Allergen lists → Multiple relationships

6. **Relationship Creation (UNWIND Batch Cypher)**

   ```cypher
   UNWIND $batch AS rel
   MATCH (s:Product {product_hash: rel.source_id})
   MATCH (t:Brand {name: rel.target_id})
   MERGE (s)-[r:HAS_BRAND]->(t)
   SET r.confidence = rel.properties.confidence
   ```

7. **Ingredient/Component Handling**

   **FOOD Products**:
   - `ingredients` → Parsed and split
   - `first_level_ingredients` → Primary ingredients
   - `second_level_ingredients` → Secondary ingredients
   - Creates multiple `FIRST_LEVEL_INGREDIENT` relationships

   **NON-FOOD Products**:
   - `components` → Parsed and split
   - `first_level_components` → Primary components
   - `second_level_components` → Secondary components
   - Creates multiple `FIRST_LEVEL_COMPONENT` relationships

8. **Allergen Processing**
   - Parses comma/semicolon separated allergen lists
   - Creates `Allergen` nodes for each unique allergen
   - Creates `ALLERGENS` relationships from Product

9. **Index Creation**
   - Creates indexes on all node identifiers:
     - `Product.product_hash`
     - `Brand.name`
     - `Store.name`
     - etc.
   - Optimizes query performance

### Output
**Database**: Neo4j graph database

**Structure**: Rich graph with products connected to all related entities

**Query Examples**:
```cypher
// Find all products from a brand
MATCH (p:Product)-[:HAS_BRAND]->(b:Brand {name: 'Coca-Cola'})
RETURN p

// Find products with specific allergen
MATCH (p:Product)-[:ALLERGENS]->(a:Allergen {name: 'gluten'})
RETURN p

// Find similar products (same category and format)
MATCH (p1:Product {siid: 'bm-12345'})-[:HAS_INTERNAL_CATEGORY]->(c)
MATCH (p2:Product)-[:HAS_INTERNAL_CATEGORY]->(c)
MATCH (p1)-[:HAS_FORMAT]->(f)
MATCH (p2)-[:HAS_FORMAT]->(f)
WHERE p1 <> p2
RETURN p2
```

---

## Stage 8: Embeddings Generation

### Purpose
Generate semantic embeddings for product names and descriptions to enable similarity search and product matching across stores.

### Implementation
**Module**: `src/ingestion/neo4j_embeddings.py`

**Functions**:
- `get_embedder()` - Gets embedding model (Azure OpenAI)
- `generate_embeddings_multithreaded()` - Generates embeddings in parallel
- `create_vector_index()` - Creates Neo4j vector indexes

### Process

1. **Embedder Initialization**
   - Uses Azure OpenAI Embeddings API
   - Model: `text-embedding-3-small` (default)
   - Dimension: 1536 (configurable)

2. **Product Query**
   - Retrieves all products from Neo4j
   - Excludes products with existing embeddings (optimization)
   - Query:
   ```cypher
   MATCH (p:Product)
   WHERE p.name_embedding IS NULL
   RETURN p.siid AS siid, p.name AS name, p.description AS description
   ```

3. **Text Preparation**
   - Combines name and description for richer embeddings
   - Cleans text (removes special characters, extra whitespace)
   - Handles missing values (empty strings)

4. **Embedding Generation**

   **Multi-threaded Processing**:
   - Processes products in parallel (default: 300 workers)
   - Batches API calls for efficiency
   - Progress bar with real-time updates

   **API Call**:
   ```python
   response = openai.Embedding.create(
       model="text-embedding-3-small",
       input=text
   )
   embedding = response['data'][0]['embedding']
   ```

5. **Embedding Storage**

   Updates Product nodes with embeddings:
   ```cypher
   MATCH (p:Product {siid: $siid})
   SET p.name_embedding = $name_embedding,
       p.description_embedding = $description_embedding,
       p.embedding_model = 'text-embedding-3-small',
       p.embedding_dimension = 1536,
       p.embedding_generated_at = datetime()
   ```

6. **Vector Index Creation**

   Creates specialized indexes for vector similarity search:
   ```cypher
   CREATE VECTOR INDEX product_name_embedding IF NOT EXISTS
   FOR (p:Product)
   ON (p.name_embedding)
   OPTIONS {
     indexConfig: {
       `vector.dimensions`: 1536,
       `vector.similarity_function`: 'cosine'
     }
   }
   ```

   **Indexes Created**:
   - `product_name_embedding` - For name-based similarity
   - `product_description_embedding` - For description-based similarity

7. **Similarity Search Enablement**

   After indexing, enables queries like:
   ```cypher
   // Find similar products by name
   MATCH (p:Product {siid: 'bm-12345'})
   CALL db.index.vector.queryNodes(
     'product_name_embedding',
     10,
     p.name_embedding
   )
   YIELD node AS similar, score
   RETURN similar.name, similar.store, score
   ORDER BY score DESC
   ```

8. **Quality Metrics**
   - Tracks embedding generation success rate
   - Reports failed embeddings
   - Logs API usage and costs

### Output
**Neo4j Property**: `name_embedding`, `description_embedding` on Product nodes

**Neo4j Indexes**: Vector similarity indexes for semantic search

**Capability**: Enables cross-store product similarity search powered by ML

---

## Complete Pipeline Example

### Input File
```csv
uuid,id,ean,product_name,brand,price,store,category
uuid-001,12345,1234567890123,agua mineral,bezoya,1.5,bm,bebidas/agua/agua natural
uuid-002,67890,9876543210987,zumo de naranja,don simon,2.3,bm,bebidas/zumos
```

### Stage Outputs

**1. Validated** → `data/processed/validated/bm_validated_20251218_1030.csv`
- Columns lowercased
- Category split: `internalcategory: water and soft drinks`
- `product_hash` generated
- Required columns added

**2. Verified** → `data/processed/verified/bm_verified_20251218_1031.csv`
- Compared with MongoDB
- New products: 2, Modified: 0, Identical: 0
- Both products kept for Neo4j ingestion

**3. Translated** → `data/processed/translated/bm_translated_20251218_1032.csv`
```csv
product_name: natural mineral water, orange juice
brand: bezoya, don simon
category: beverages/water/still water, beverages/juices
```

**4. Enriched** → `data/processed/enriched/bm_enriched_20251218_1033.csv`
- `internaltype: FOOD` (filled by LLM)
- `internalsubcategory: water, juice` (filled by LLM)

**5. Fixed** → `data/processed/fixed/bm_fixed_20251218_1034.csv`
- Ingredients processed
- Allergens validated
- Categorization confirmed correct

**6. PostgreSQL** → `product_vector_data` table
```
uuid      | siid     | price | measure_value
uuid-001  | bm-12345 | 1.5   | 1.5
uuid-002  | bm-67890 | 2.3   | 1.0
```

**7. Neo4j** → Graph nodes and relationships
```
(:Product {siid: 'bm-12345'})-[:HAS_BRAND]->(:Brand {name: 'bezoya'})
(:Product {siid: 'bm-12345'})-[:SOLD_IN]->(:Store {name: 'bm'})
(:Product {siid: 'bm-12345'})-[:HAS_INTERNAL_CATEGORY]->(:Internal_Category {name: 'water and soft drinks'})
```

**8. Neo4j Embeddings** → Vector properties
```
Product {
  siid: 'bm-12345',
  name_embedding: [0.023, -0.045, 0.112, ...],  // 1536 dimensions
  description_embedding: [0.034, 0.021, -0.087, ...]
}
```

---

## Error Handling

### Pipeline Resilience

1. **Early Exit Conditions**
   - If verified CSV is empty → Pipeline stops (returns "skipped" status)
   - Prevents unnecessary processing of files with no changes

2. **Stage Failures**
   - Each stage logs errors independently
   - Partial failures don't crash entire pipeline
   - Failed products tracked in error logs

3. **Database Connection Failures**
   - MongoDB unavailable → Verification fails, pipeline stops
   - PostgreSQL unavailable → Numerical data not stored, continues
   - Neo4j unavailable → Graph ingestion fails, pipeline stops

4. **LLM API Failures**
   - Translation failures → Falls back to original text
   - Enrichment failures → Retries 3 times, then skips row
   - Embedding failures → Product stored without embeddings

5. **Data Quality Issues**
   - Invalid categorization → LLM attempts fix, then flags for review
   - Missing required fields → Rejected before ingestion
   - Malformed data → Logged and skipped

### Monitoring & Logging

**Log Locations**:
- API logs: Console output and `logs/api.log`
- Pipeline logs: `logs/ingestion_<timestamp>.log`
- Error details: Included in API response

**API Response Example** (Partial Failure):
```json
{
  "status": "success",
  "filename": "bm_products.csv",
  "rows_processed": 400,
  "errors": [
    "Row 245: Invalid categorization, flagged for review",
    "Row 312: Translation failed for product_name"
  ],
  "ingestion_results": {
    "mongodb": {"inserted": 398},
    "postgresql": {"inserted": 400},
    "neo4j": {"nodes_created": 398, "relationships_created": 1592}
  }
}
```

---

## Performance Optimization

### Multi-threading
- **Translation**: 1000 parallel workers
- **LLM Enrichment**: 1000 parallel workers
- **Embedding Generation**: 300 parallel workers

### Batch Processing
- **MongoDB**: Batch queries for product lookup
- **PostgreSQL**: 500 rows per batch
- **Neo4j**: UNWIND batches (all products per node type)

### Caching
- Column registry cached in memory
- MongoDB products cached per verification run
- Neo4j schema cached during ingestion

### Database Indexes
- PostgreSQL: Indexes on uuid, siid, product_hash
- Neo4j: Indexes on all node identifiers
- Neo4j: Vector indexes for embeddings

---

## API Usage

### Endpoint
```
POST http://localhost:8000/upload
```

### Request
```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@/path/to/bm_products.csv"
```

### Response (Success)
```json
{
  "status": "success",
  "filename": "bm_products.csv",
  "original_path": "/data/raw/csv/bm_products.csv",
  "processed_path": "/data/processed/fixed/bm_products_fixed_20251218_1030.csv",
  "rows_processed": 409,
  "columns_processed": 47,
  "message": "File processed successfully",
  "errors": null,
  "ingestion_results": {
    "mongodb": {
      "new_products": 150,
      "modified_products": 25,
      "identical_products": 234,
      "collection": "bm_products"
    },
    "postgresql": {
      "rows_inserted": 175,
      "table": "product_vector_data"
    },
    "neo4j": {
      "nodes_created": 1750,
      "relationships_created": 7000,
      "database": "neo4j"
    },
    "embeddings": {
      "products_embedded": 175,
      "embedding_model": "text-embedding-3-small",
      "dimension": 1536
    }
  }
}
```

### Response (Skipped - No Changes)
```json
{
  "status": "skipped",
  "filename": "bm_products.csv",
  "original_path": "/data/raw/csv/bm_products.csv",
  "processed_path": null,
  "rows_processed": 0,
  "columns_processed": 0,
  "message": "No new or modified products found; pipeline skipped.",
  "errors": null,
  "ingestion_results": null
}
```

### Response (Error)
```json
{
  "status": "error",
  "filename": "bm_products.csv",
  "message": "Failed to process file: Invalid file format",
  "errors": ["File must be CSV, XLSX, or XLS"]
}
```

---

## Configuration

### Environment Variables

Required in `.env`:
```bash
# Azure OpenAI (Translation, Enrichment, Embeddings)
AZURE_OPENAI_API_KEY=your-api-key
AZURE_OPENAI_API_BASE=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-5-nano

# Neo4j
NEO4J_URI=neo4j://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password
NEO4J_DATABASE=neo4j

# MongoDB
MONGO_URI=mongodb://localhost:27017
MONGO_DATABASE=supermarket_db

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DATABASE=supermarket_analytics
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your-password
```

### Registry Configuration

**File**: `data/schemas/column_registry.json`

Defines:
- Column roles (node, node_property, relationship_property)
- Translatable columns
- Uptable columns (can change without re-ingestion)
- LLM completion status
- Data types

---

## Troubleshooting

### Common Issues

**1. Pipeline Stops After Verification**
- **Cause**: All products are identical (no changes)
- **Solution**: Expected behavior, no re-ingestion needed

**2. MongoDB Connection Failed**
- **Cause**: MongoDB not running or wrong credentials
- **Solution**: Check MongoDB status, verify credentials in `.env`

**3. Neo4j Ingestion Fails**
- **Cause**: Neo4j not running or out of memory
- **Solution**: Check Neo4j status, increase heap size in neo4j.conf

**4. LLM Enrichment Very Slow**
- **Cause**: API rate limits or too many empty fields
- **Solution**: Reduce `max_workers`, check API quota

**5. Embeddings Not Generated**
- **Cause**: Azure OpenAI quota exceeded
- **Solution**: Check API usage, wait for quota reset

**6. Duplicate Products in Neo4j**
- **Cause**: `product_hash` not being used as identifier
- **Solution**: Ensure `product_hash` column exists and is populated

---

## Maintenance

### Regular Tasks

1. **Monitor Database Sizes**
   - MongoDB: Check collection sizes
   - PostgreSQL: Monitor table growth
   - Neo4j: Check heap usage and store size

2. **Clean Up Processed Files**
   - Archive old files in `data/processed/`
   - Retain only recent runs for debugging

3. **Update Taxonomy**
   - Add new categories to `data/schemas/categories.py`
   - Update `column_registry.json` as needed
   - Validate against existing data

4. **API Key Rotation**
   - Update Azure OpenAI keys periodically
   - Test all pipeline stages after rotation

5. **Index Maintenance**
   - Rebuild Neo4j indexes if performance degrades
   - Update PostgreSQL statistics

---

## Summary

The data ingestion pipeline is a sophisticated, 8-stage system that transforms raw supermarket product data into a fully integrated, searchable knowledge graph. Key features:

✅ **Automated**: Single API endpoint triggers entire pipeline  
✅ **Intelligent**: LLM-powered translation and enrichment  
✅ **Efficient**: Multi-threaded, batch processing  
✅ **Robust**: Error handling and partial failure recovery  
✅ **Scalable**: Handles large datasets (1000+ products)  
✅ **Maintainable**: Modular architecture, comprehensive logging

**Total Processing Time** (typical 400-product file):
- Validation: ~10 seconds
- Verification: ~30 seconds
- Translation: ~2 minutes
- Enrichment: ~3 minutes
- Fixing: ~30 seconds
- PostgreSQL: ~5 seconds
- Neo4j: ~1 minute
- Embeddings: ~2 minutes

**Total**: ~9 minutes for complete pipeline

---

**API Version**: 1.0.0  
**Last Updated**: December 18, 2025  
**Documentation**: https://github.com/your-org/ai_consumer_goods
