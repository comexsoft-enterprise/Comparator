# Product Similarity Model Documentation

## Table of Contents
1. [Overview](#overview)
2. [Model Architecture](#model-architecture)
3. [The Four Similarity Dimensions](#the-four-similarity-dimensions)
4. [Detailed Algorithm Walkthrough](#detailed-algorithm-walkthrough)
5. [Scoring & Weighting System](#scoring--weighting-system)
6. [Configuration Parameters](#configuration-parameters)
7. [Performance Optimizations](#performance-optimizations)
8. [Usage Examples](#usage-examples)
9. [Troubleshooting](#troubleshooting)

---

## Overview

### What Does the Model Do?

The **Product Similarity Model** finds similar products across different supermarket chains by analyzing them from four complementary perspectives:

1. **Graph-based Similarity** - Shared characteristics (brand, format, ingredients, category)
2. **Name Semantic Similarity** - Product name meaning using AI embeddings
3. **Description Semantic Similarity** - Product description meaning using AI embeddings
4. **Numerical Feature Similarity** - Nutritional values and measurements (Euclidean distance)

By combining these four dimensions, the model identifies products that are truly similar even when they have different names, brands, or descriptions across stores.

### Real-World Example

**Product in Store A (Eroski)**:
- Name: "Agua Mineral Natural Bezoya 1.5L"
- Brand: Bezoya
- Format: Bottle
- Category: Water
- Price: 0.60€

**Similar Product in Store B (Makro)**:
- Name: "Natural Spring Water 1.5L"
- Brand: Font Vella
- Format: Bottle
- Category: Water
- Price: 0.55€

**Why they match**:
- Graph score: 0.85 (same format, similar category, same subcategory)
- Name similarity: 0.78 (semantically similar names)
- Description similarity: 0.82 (both describe mineral water)
- Euclidean: 0.91 (similar nutritional values)
- **Combined score**: 0.84 → Strong match!

---

## Model Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                   INPUT: Products from Store A                   │
│                   (e.g., 100 products to match)                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 1: Check PostgreSQL for Existing Perfect Matches          │
│  - Query products with 100% validated matches                   │
│  - Return immediately if match exists (no reprocessing)         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 2: Graph-Based Similarity (Category Analysis)             │
│  - For each product A, query Neo4j for products in Store B      │
│  - Find products sharing characteristics (brand, format, etc.)  │
│  - Calculate weighted graph score based on shared nodes         │
│  - Filter: Keep only candidates with min_graph_score ≥ 0.5     │
│  Output: Top 300 candidates per product A                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 3: Semantic Similarity (Embedding Analysis)               │
│  - Generate or load OpenAI embeddings for names/descriptions    │
│  - Calculate cosine similarity between product A & candidates   │
│  - Filter: Keep only if name_sim ≥ 0.5 & desc_sim ≥ 0.5       │
│  Output: Candidates with semantic scores added                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 4: Numerical Similarity (Euclidean Distance)              │
│  - Load numerical features from PostgreSQL (nutrition, price)   │
│  - Calculate Euclidean distance between feature vectors         │
│  - Normalize to 0-1 scale (1 = identical, 0 = very different)  │
│  - Filter: Keep only if euclidean_sim ≥ 0.0 (configurable)    │
│  Output: Candidates with Euclidean scores added                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 5: Combine All Scores (Weighted Average)                  │
│  - Apply product-type-specific weights (FOOD vs NON-FOOD)       │
│  - Formula: combined_score = w₁·graph + w₂·name +               │
│             w₃·description + w₄·euclidean                       │
│  - Sort by combined_score DESC                                  │
│  - Return top N results (default: 3)                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  OUTPUT: Ranked Similar Products per Store A Product            │
│  {product_a_id: [similar_b_id1, similar_b_id2, similar_b_id3]} │
└─────────────────────────────────────────────────────────────────┘
```

### Component Modules

| Module | File | Purpose |
|--------|------|---------|
| **Orchestrator** | `model_variations/model.py` → `ModelCombinedSimilarity` | Main controller coordinating all steps |
| **Graph Analysis** | `category_analysis/category_analysis.py` → `CategoryAnalysis` | Neo4j queries for shared characteristics |
| **Semantic Analysis** | `embedding_analysis/embedding_analysis.py` → `SimilarityAnalysis` | OpenAI embeddings & cosine similarity |
| **Numerical Analysis** | `postgresql_analysis/euclidean_distance_calculator.py` | PostgreSQL feature extraction & Euclidean distance |
| **Entry Point** | `model_variations/get_similar_neo4j_refactored.py` → `model()` | API-facing function with parameter validation |

---

## The Four Similarity Dimensions

### 1. Graph-Based Similarity (Category Analysis)

**Concept**: Products are similar if they share the same characteristics in the Neo4j knowledge graph.

**How it works**:

1. **Product A Neighborhood Extraction**
   ```cypher
   MATCH (store_a:Store)-[sells:SELLS]->(product_a:Product)
   WHERE sells.id = 'bm-12345'
   MATCH (product_a)-[rel]-(neighbor)
   RETURN neighbor, type(rel)
   ```
   
   **Example Output**:
   - `(Brand: "Bezoya")` via `FROM_BRAND`
   - `(Format: "bottle")` via `IS_PACKED_AS`
   - `(Ingredient: "water")` via `FIRST_LEVEL_INGREDIENT`
   - `(Internal_Subcategory: "mineral_water")` via `COVERS`
   - `(Allergen: "none")` via `ALLERGENS`

2. **Product B Candidate Filtering**
   ```cypher
   MATCH (store_b:Store {name: 'makro-01013'})-[sells_b:SELLS]->(product_b:Product)
   MATCH (product_b)-[rel_b]-(neighbor_b)
   WHERE neighbor_b IN [neighbors_of_A]  // Shared neighbors
   RETURN product_b, count(neighbor_b) AS overlap
   HAVING overlap >= 2  // Minimum match threshold
   ```

3. **Weighted Scoring**
   
   Each shared node contributes a weight to the total score:
   
   | Node Type | Weight (Food) | Weight (Non-Food) | Rationale |
   |-----------|---------------|-------------------|-----------|
   | **Brand** | 0.30 | 0.10-0.40 | Critical for non-food; less for food (generics exist) |
   | **Format** | 0.15 | 0.20 | Important: bottle vs can, box vs bag |
   | **Subcategory** | 0.25 | 0.30 | Must be in same product category |
   | **First Ingredient** | 0.30 | N/A | Primary component for food |
   | **Second Ingredient** | 0.10 | N/A | Secondary component |
   | **Allergen** | 0.02 | N/A | Minor factor (many products have no allergens) |
   | **Country Origin** | 0.05 | 0.05-0.10 | Slight preference for same origin |
   | **Quantity** | 0.10 | 0.15 | Size matching (1L vs 1L) |
   
   **Formula**:
   ```
   graph_score = Σ (weight_i × match_i) + category_bonus
   
   where:
   - match_i = 1 if node i is shared, 0 otherwise
   - category_bonus = 0.30 if same Internal_Category, else 0
   ```

4. **Special Rules**

   - **Marca Blanca (Store Brand) Logic**:
     - If Product A is store brand → Match only with store brands or unknown brands
     - If Product A is national brand → Match only with national brands
     - Prevents mismatching Coca-Cola with generic cola
   
   - **Liquid Volume Matching**:
     - If unit is "L" (liters) → Require exact measure_value match
     - Prevents 1L bottle matching with 2L bottle
   
   - **Product Type Detection** (Optimization):
     - Pre-detect product type for Product A (e.g., "olive_oil", "shampoo")
     - Add pattern filters to query for Product B
     - Reduces search space by 70-90%
   
   - **Category Type Exclusion**:
     - Internal category types must match (FOOD → FOOD, NON-FOOD → NON-FOOD)
     - Prevents food matching with electronics

**Output**: List of candidate products B with `weighted_score` (0.0 to ~1.5 range)

---

### 2. Name Semantic Similarity (Embedding Analysis)

**Concept**: Products with semantically similar names are likely the same product.

**How it works**:

1. **Embedding Generation**
   
   Uses Azure OpenAI `text-embedding-3-small` model (1536 dimensions):
   
   ```python
   # Example
   product_a_name = "Agua Mineral Natural Bezoya 1.5L"
   product_b_name = "Natural Spring Water 1.5L"
   
   embedding_a = openai.Embedding.create(
       model="text-embedding-3-small",
       input=product_a_name
   )  # → [0.023, -0.015, 0.041, ..., 0.008]  (1536 values)
   
   embedding_b = openai.Embedding.create(
       model="text-embedding-3-small",
       input=product_b_name
   )  # → [0.019, -0.012, 0.038, ..., 0.011]  (1536 values)
   ```

2. **Caching System**
   
   - Embeddings are cached to disk using pickle
   - Cache key: SHA256 hash of text
   - Avoids regenerating embeddings for same products
   - Typical cache hit rate: 60-90% after first run
   - Saves ~$0.0001 per cached embedding (adds up over 10K+ products)

3. **Similarity Calculation**
   
   Uses **cosine similarity** (default metric):
   
   ```
   cosine_similarity = (A · B) / (||A|| × ||B||)
   
   where:
   - A · B = dot product of vectors
   - ||A|| = Euclidean norm of vector A
   - Result: -1 to 1 (typically 0 to 1 for product names)
   ```
   
   **Example**:
   ```
   "Agua Mineral Natural Bezoya 1.5L" vs "Natural Spring Water 1.5L"
   → cosine_similarity = 0.78
   
   "Agua Mineral Natural Bezoya 1.5L" vs "Smartphone Samsung Galaxy"
   → cosine_similarity = 0.12
   ```

4. **Alternative Distance Metrics**
   
   The model supports multiple metrics:
   
   | Metric | Formula | Use Case |
   |--------|---------|----------|
   | **Cosine** | 1 - cos(θ) | Default; best for text similarity |
   | **Euclidean** | √Σ(ai - bi)² | Magnitude-sensitive |
   | **Manhattan** | Σ\|ai - bi\| | Robust to outliers |
   | **Dot Product** | Σ(ai × bi) | Fast; unnormalized |

**Output**: `name_similarity` score (0.0 to 1.0) added to each candidate

---

### 3. Description Semantic Similarity

**Concept**: Same as name similarity but for longer product descriptions.

**How it works**:

1. **Embedding Generation** (identical to name embedding)
   
   ```python
   product_a_desc = "Natural mineral water from mountain springs, low in sodium, perfect for hydration"
   product_b_desc = "Spring water with natural minerals, ideal for daily hydration, low sodium content"
   
   # Both embedded using text-embedding-3-small
   ```

2. **Special Handling**
   
   - If description is missing → Use product name as fallback
   - If description is very short (<10 chars) → Use name
   - Descriptions are typically more detailed than names
   - Higher variance in description similarity scores

3. **Why Descriptions Matter**
   
   - Names might be abbreviated: "Jamón" vs "Jamón Serrano Gran Reserva"
   - Descriptions provide context: organic, gluten-free, sugar-free, etc.
   - Can compensate for different naming conventions across stores

**Output**: `description_similarity` score (0.0 to 1.0) added to each candidate

---

### 4. Numerical Feature Similarity (Euclidean Distance)

**Concept**: Products with similar nutritional values and measurements are more likely to be the same.

**How it works**:

1. **Feature Extraction from PostgreSQL**
   
   Extracts numerical columns from `product_vector_data` table:
   
   ```sql
   SELECT 
       price,
       energy_kcal_100g,
       fat_100g,
       saturated_fat_100g,
       carbohydrates_100g,
       sugars_100g,
       proteins_100g,
       salt_100g,
       fiber_100g,
       sodium_100g,
       measure_value,
       -- ... other numerical features
   FROM product_vector_data
   WHERE siid IN (product_a_siid, product_b_siid1, product_b_siid2, ...)
   ```

2. **Euclidean Distance Calculation**
   
   Calculates normalized distance between feature vectors:
   
   ```
   distance = √Σ((pi - qi) / pi)²
   
   where:
   - pi = feature value for product A
   - qi = feature value for candidate product B
   - Division by pi normalizes for scale (prevents price dominating)
   - Only includes features where both products have values
   ```
   
   **Example**:
   ```
   Product A (Coca-Cola 330ml):
   - energy_kcal_100g: 42
   - sugars_100g: 10.6
   - price: 0.60
   
   Product B (Pepsi 330ml):
   - energy_kcal_100g: 41
   - sugars_100g: 11.0
   - price: 0.58
   
   distance = √[((42-41)/42)² + ((10.6-11.0)/10.6)² + ((0.60-0.58)/0.60)²]
            = √[0.00057 + 0.00143 + 0.00111]
            = √0.00311 = 0.056
   
   euclidean_similarity = 1 - (0.056 / max_distance)
                        = 1 - 0.12 = 0.88
   ```

3. **Handling Missing Values**
   
   - If a feature is missing in either product → Skip that feature
   - Only compute distance for features present in both
   - Minimum 3 features required for valid calculation
   - If too few features → euclidean_similarity = 0.0

4. **Why This Matters**
   
   - Two products might have different names but identical nutrition
   - Example: Store brand vs national brand of same recipe
   - Catches repackaging: Same product, different sizes
   - Identifies price anomalies: Same product, very different prices

**Output**: `euclidean_similarity` score (0.0 to 1.0) added to each candidate

---

## Detailed Algorithm Walkthrough

### Complete Step-by-Step Process

#### STEP 0: Check Perfect Matches (Optimization)

```python
# Query PostgreSQL for existing validated matches
SELECT product_a_siid, product_b_siid, combined_score
FROM perfect_matches
WHERE store_a = 'eroski-01013'
  AND store_b = 'makro-01013'
  AND product_a_siid IN ('bm-12345', 'bm-67890', ...)
  AND combined_score = 1.0  -- Only 100% matches
```

**If found**: Return immediately, skip all model computation  
**If not found**: Proceed to STEP 1

---

#### STEP 1: Load Products from Store A

Three methods available:

1. **From Neo4j** (default):
   ```cypher
   MATCH (store:Store {name: 'eroski-01013'})-[sells:SELLS]->(product:Product)
   RETURN 
       sells.id AS id,
       product.siid AS siid,
       product.product_name AS product_name,
       product.description AS description,
       sells.url AS url
   LIMIT 1000
   ```

2. **From External File**:
   ```python
   # Read product IDs from text file
   with open('2_eroski_id.txt', 'r') as f:
       product_ids = [line.strip() for line in f]
   
   # Query Neo4j for each ID
   ```

3. **From ID List**:
   ```python
   # Use provided list directly
   product_ids = ['bm-12345', 'bm-67890', 'bm-54321']
   ```

**Output**: List of product dictionaries:
```python
[
    {
        'id': 'bm-12345',
        'siid': 'eroski_01013-01-12345',
        'product_name': 'Agua Mineral Natural 1.5L',
        'description': 'Mineral water from natural springs',
        'url': 'https://eroski.es/product/12345'
    },
    # ... more products
]
```

---

#### STEP 2: Graph-Based Similarity (Parallel Processing)

**For each product A** (parallelized with ThreadPoolExecutor):

1. **Extract Product A Neighborhood**
   ```cypher
   MATCH (product_a:Product {siid: $siid})-[rel]-(neighbor)
   OPTIONAL MATCH (neighbor)-[:ALIAS_OF*0..]->(canonical)
   WHERE canonical:Ingredient OR canonical IS NULL
   WITH collect({
       node: COALESCE(canonical, neighbor),
       relType: type(rel)
   }) AS neighbors
   ```

2. **Pre-filter Product B Candidates**
   
   **Exclusion Rules Applied**:
   - Must be in same Internal_Type (FOOD/NON-FOOD/ELECTRONICS)
   - Marca blanca matching rules
   - Liquid volume matching (if unit = 'L')
   - Product type pattern matching (if detected)

3. **Calculate Overlap**
   ```cypher
   MATCH (product_b:Product)-[rel_b]-(neighbor_b)
   WHERE neighbor_b IN neighbors_of_A
     AND type(rel_b) = neighbor_a.relType
   WITH product_b, count(neighbor_b) AS overlap
   WHERE overlap >= 2  -- min_matches threshold
   ```

4. **Score Calculation**
   ```cypher
   WITH product_b, overlap,
        reduce(score = 0.0, neighbor IN shared_neighbors |
            score + weight_for_node_type(neighbor)
        ) AS weighted_score
   ORDER BY weighted_score DESC
   LIMIT 300  -- Top candidates only
   ```

5. **Quality Filtering**
   ```python
   # Keep only candidates meeting threshold
   candidates = [
       c for c in candidates 
       if c['weighted_score'] >= min_graph_score
   ]
   ```

**Progress Logging**:
```
[1/100] Finding similar products for ID: bm-12345
  Detected product_type for bm-12345: water
  ✨ Added 3 pattern filters: ['water', 'agua', 'eau']
  Found 87 candidates with min_matches >= 2
  After filtering: 43 candidates with score >= 0.5
```

**Output After Step 2**:
```python
{
    'bm-12345': {
        'product_a': {...},
        'similar_products_b': [
            {
                'id': 'mk-98765',
                'siid': 'makro_01013-01-98765',
                'product_name': 'Spring Water 1.5L',
                'weighted_score': 0.85,
                'overlap': 5,
                'shared_nodes_details': [
                    'Format: bottle',
                    'Internal_Subcategory: mineral_water',
                    'First_level_ingredient: water',
                    'Quantity: 1.5'
                ]
            },
            # ... 42 more candidates
        ]
    },
    # ... 99 more products
}
```

---

#### STEP 3: Embedding-Based Similarity (Parallel Processing)

**For each product A and its candidates** (parallelized):

1. **Collect All Texts to Embed**
   ```python
   texts_to_embed = []
   
   # Product A name and description
   texts_to_embed.append(product_a['product_name'])
   texts_to_embed.append(product_a['description'] or product_a['product_name'])
   
   # All candidate product B names and descriptions
   for candidate in candidates_b:
       texts_to_embed.append(candidate['product_name'])
       texts_to_embed.append(candidate['description'] or candidate['product_name'])
   ```

2. **Check Cache & Generate Missing Embeddings**
   ```python
   embeddings = []
   for text in texts_to_embed:
       cache_key = sha256(text).hexdigest()
       if cache_key in cache:
           embeddings.append(cache[cache_key])  # Cache hit
       else:
           emb = openai.Embedding.create(input=text)  # Cache miss
           cache[cache_key] = emb
           embeddings.append(emb)
   ```
   
   **Parallelized with 300 workers**:
   ```
   🤖 Generating 847 new OpenAI embeddings with 300 workers...
   🔄 Generating embeddings: [==================================================] 847/847 (100.0%) ✅
   ✅ Generated 847 OpenAI embeddings in 12.34s (68.6 embeddings/s)
   📊 Cache stats: 1253 hits, 847 misses (59.7% hit rate)
   ```

3. **Calculate Similarities**
   ```python
   product_a_name_emb = embeddings[0]
   product_a_desc_emb = embeddings[1]
   
   for i, candidate in enumerate(candidates_b):
       candidate_name_emb = embeddings[2 + i*2]
       candidate_desc_emb = embeddings[2 + i*2 + 1]
       
       name_sim = cosine_similarity(product_a_name_emb, candidate_name_emb)
       desc_sim = cosine_similarity(product_a_desc_emb, candidate_desc_emb)
       
       candidate['name_similarity'] = name_sim
       candidate['description_similarity'] = desc_sim
   ```

4. **Quality Filtering**
   ```python
   # Remove candidates not meeting thresholds
   candidates_b = [
       c for c in candidates_b
       if c['name_similarity'] >= min_name_similarity
       and c['description_similarity'] >= min_description_similarity
   ]
   ```

**Output After Step 3**:
```python
{
    'bm-12345': {
        'product_a': {...},
        'similar_products_b': [
            {
                'id': 'mk-98765',
                'weighted_score': 0.85,
                'name_similarity': 0.78,           # NEW
                'description_similarity': 0.82,   # NEW
                # ... other fields
            },
            # ... 28 candidates (filtered from 43)
        ]
    }
}
```

---

#### STEP 4: Euclidean Distance Calculation

**For each product A and its remaining candidates**:

1. **Load Numerical Features from PostgreSQL**
   ```python
   siids = [product_a['siid']] + [c['siid'] for c in candidates_b]
   
   query = """
   SELECT siid, 
          price, energy_kcal_100g, fat_100g, carbohydrates_100g, 
          proteins_100g, sugars_100g, salt_100g, measure_value
   FROM product_vector_data
   WHERE siid = ANY(%s)
   """
   
   features = execute_query(query, [siids])
   ```

2. **Calculate Euclidean Distance**
   ```python
   product_a_features = features[product_a['siid']]
   
   for candidate in candidates_b:
       candidate_features = features[candidate['siid']]
       
       # Calculate normalized distance
       distance = euclidean_distance_normalized(
           product_a_features,
           candidate_features
       )
       
       # Convert to similarity (1 = identical, 0 = very different)
       candidate['euclidean_similarity'] = 1 - (distance / max_distance)
   ```

3. **Quality Filtering**
   ```python
   candidates_b = [
       c for c in candidates_b
       if c.get('euclidean_similarity', 0) >= min_euclidean_similarity
   ]
   ```

**Output After Step 4**:
```python
{
    'bm-12345': {
        'product_a': {...},
        'similar_products_b': [
            {
                'id': 'mk-98765',
                'weighted_score': 0.85,
                'name_similarity': 0.78,
                'description_similarity': 0.82,
                'euclidean_similarity': 0.91,     # NEW
                # ... other fields
            },
            # ... 25 candidates (filtered from 28)
        ]
    }
}
```

---

#### STEP 5: Combine All Scores

**Final Score Calculation**:

1. **Determine Product Type**
   ```python
   # Based on Internal_Type from Neo4j
   if product_type == 'FOOD':
       weights = food_weights
   elif product_type == 'NON_FOOD_SUPER':
       weights = non_food_super_weights
   elif product_type == 'NON_FOOD_ELEC':
       weights = non_food_elec_weights
   ```

2. **Apply Weighted Formula**
   ```python
   combined_score = (
       weights['graph'] * candidate['weighted_score'] +
       weights['name'] * candidate['name_similarity'] +
       weights['description'] * candidate['description_similarity'] +
       weights['euclidean'] * candidate['euclidean_similarity']
   )
   
   candidate['combined_score'] = combined_score
   ```
   
   **Example Calculation (FOOD product)**:
   ```
   Weights: {graph: 0.10, name: 0.40, description: 0.40, euclidean: 0.10}
   
   Scores:  {graph: 0.85, name: 0.78, description: 0.82, euclidean: 0.91}
   
   Combined = 0.10×0.85 + 0.40×0.78 + 0.40×0.82 + 0.10×0.91
            = 0.085 + 0.312 + 0.328 + 0.091
            = 0.816
   ```

3. **Sort & Limit**
   ```python
   candidates_b.sort(key=lambda x: x['combined_score'], reverse=True)
   candidates_b = candidates_b[:top_n]  # Keep top 3
   ```

**Final Output**:
```python
{
    'bm-12345': [
        'mk-98765',  # Combined score: 0.816
        'mk-54321',  # Combined score: 0.779
        'mk-11111'   # Combined score: 0.745
    ],
    'bm-67890': [
        'mk-22222',  # Combined score: 0.891
        'mk-33333',  # Combined score: 0.834
        'mk-44444'   # Combined score: 0.812
    ],
    # ... 98 more products
}
```

---

## Scoring & Weighting System

### Weight Configuration by Product Type

#### FOOD Products

**Graph Weights** (Characteristics):
```python
{
    'Brand': 0.30,                    # Important but not critical (generics exist)
    'Format': 0.15,                   # Bottle, can, box, bag
    'Internal_Subcategory': 0.25,     # Must be similar category
    'First_level_ingredient': 0.30,   # PRIMARY ingredient (water, milk, flour)
    'Second_level_ingredient': 0.10,  # Secondary ingredient (sugar, salt)
    'Unit_measure': 0.10,             # ml, g, L, kg
    'Allergen': 0.02,                 # Gluten, nuts, dairy
    'Country_of_Origin': 0.05,        # Spain, Italy, France
    'Other_Ingredient': 0.02,         # Minor ingredients
    'Quantity': 0.10                  # Amount (1.5L, 500g)
}
# Total: 1.39 (can exceed 1.0 for bonus)
```

**Combined Weights** (Similarity Methods):
```python
{
    'graph': 0.10,          # 10% - Graph structure
    'name': 0.40,           # 40% - Product name meaning
    'description': 0.40,    # 40% - Description meaning
    'euclidean': 0.10       # 10% - Nutritional values
}
# Total: 1.00
```

**Rationale**: 
- Food products have detailed ingredients → High name/description weight
- Nutritional values help distinguish similar products
- Brand less important (many generic/store brands)

---

#### NON-FOOD SUPERMARKET Products (Cleaning, Personal Care)

**Graph Weights**:
```python
{
    'Brand': 0.10,                    # Less important for cleaning products
    'Format': 0.20,                   # Spray, liquid, powder
    'Internal_Subcategory': 0.30,     # Detergent, shampoo, soap
    'First_component': 0.30,          # Main chemical/ingredient
    'Second_component': 0.10,         # Secondary component
    'Unit_measure': 0.15,             # ml, L
    'Country_of_Origin': 0.10,        # Manufacturing country
    'Quantity': 0.15                  # Volume/weight
}
```

**Combined Weights**:
```python
{
    'graph': 0.25,          # 25% - Graph structure (more important)
    'name': 0.42,           # 42% - Product name
    'description': 0.25,    # 25% - Description
    'euclidean': 0.08       # 8% - Physical properties (less data available)
}
```

**Rationale**:
- Fewer numerical features available (no nutrition data)
- Graph structure more important (format, category)
- Brand still not critical (many alternatives)

---

#### NON-FOOD ELECTRONICS Products

**Graph Weights**:
```python
{
    'Brand': 0.40,                    # CRITICAL for electronics
    'Format': 0.15,                   # Phone, laptop, tablet
    'Internal_Subcategory': 0.30,     # Audio, computing, mobile
    'First_component': 0.25,          # Processor, screen type
    'Second_component': 0.15,         # RAM, storage
    'Unit_measure': 0.10,             # inches, GB, GHz
    'Country_of_Origin': 0.05,        # Less important
    'Quantity': 0.10                  # Screen size, storage
}
```

**Combined Weights**:
```python
{
    'graph': 0.33,          # 33% - Graph structure (brand critical)
    'name': 0.42,           # 42% - Product name (model numbers)
    'description': 0.17,    # 17% - Description (less detailed)
    'euclidean': 0.08       # 8% - Technical specs (limited data)
}
```

**Rationale**:
- Brand is CRITICAL (Samsung vs Apple vs Xiaomi)
- Model names/numbers highly specific
- Less detailed descriptions typically
- Graph structure very important (brand + subcategory)

---

### Quality Thresholds

Minimum scores required for a candidate to be considered:

```python
{
    'min_graph_score': 0.5,              # At least 50% graph similarity
    'min_name_similarity': 0.5,          # At least 50% name similarity
    'min_description_similarity': 0.5,   # At least 50% description similarity
    'min_euclidean_similarity': 0.0      # No minimum (optional feature)
}
```

**Effect**:
- Higher thresholds → Fewer but higher-quality matches
- Lower thresholds → More matches but potentially less accurate
- Adjustable via API parameters

**Recommended Settings**:

| Use Case | min_graph | min_name | min_desc | min_euclidean |
|----------|-----------|----------|----------|---------------|
| **High Precision** | 0.7 | 0.7 | 0.7 | 0.5 |
| **Balanced** (default) | 0.5 | 0.5 | 0.5 | 0.0 |
| **High Recall** | 0.3 | 0.3 | 0.3 | 0.0 |

---

## Configuration Parameters

### ModelParameters (Pydantic Model)

```python
class ModelParameters(BaseModel):
    # Required
    store_a: str                                  # Source store name
    store_b: str                                  # Target store name
    list_ids: List[str]                           # Product IDs to analyze
    
    # Optional
    top_n_results: int = 3                        # Number of matches per product
    food_weights: FoodWeights = FoodWeights()     # FOOD product weights
    non_food_super_weights: NonFoodSuperWeights = NonFoodSuperWeights()
    non_food_elec_weights: NonFoodElecWeights = NonFoodElecWeights()
    quality_thresholds: QualityThresholds = QualityThresholds()
```

### Usage Example

```python
from src.models.model_variations.get_similar_neo4j_refactored import (
    model, ModelParameters, FoodWeights, CombinedWeights
)

# Custom configuration
params = ModelParameters(
    store_a="eroski-01013",
    store_b="makro-01013",
    list_ids=["bm-12345", "bm-67890"],
    top_n_results=5,
    
    # Custom FOOD weights
    food_weights=FoodWeights(
        combined=CombinedWeights(
            graph=0.15,        # Increase graph importance
            name=0.35,         # Decrease name
            description=0.35,  # Decrease description
            euclidean=0.15     # Increase numerical features
        )
    ),
    
    # Custom quality thresholds
    quality_thresholds=QualityThresholds(
        min_graph_score=0.7,           # Stricter
        min_name_similarity=0.6,       # Stricter
        min_description_similarity=0.6,
        min_euclidean_similarity=0.3
    )
)

# Run model
results = model(params)
```

---

## Performance Optimizations

### 1. Caching System

**Embedding Cache**:
- **File**: `data/processed/embeddings_cache.pkl`
- **Format**: Pickle (10x faster than JSON)
- **Key**: SHA256 hash of text
- **Size**: ~50MB for 10K products
- **Hit Rate**: 60-90% after first run
- **Savings**: ~$100 for 100K products

**Implementation**:
```python
def _text_to_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

# Check cache before API call
cache_key = _text_to_cache_key(product_name)
if cache_key in embeddings_cache:
    return embeddings_cache[cache_key]  # Cache hit
else:
    embedding = openai.Embedding.create(input=product_name)
    embeddings_cache[cache_key] = embedding
    return embedding
```

---

### 2. Parallel Processing

**ThreadPoolExecutor Usage**:

| Stage | Workers | Parallelization Target |
|-------|---------|------------------------|
| **Graph Analysis** | 200 | Products A |
| **Embedding Generation** | 300 | API calls |
| **Euclidean Calculation** | 10 | Products A |

**Example**:
```python
with ThreadPoolExecutor(max_workers=200) as executor:
    futures = {
        executor.submit(find_matches, product_a): product_a
        for product_a in products_a
    }
    
    for future in as_completed(futures):
        result = future.result()
        # Process result
```

**Performance Gain**:
- Sequential: ~5 minutes for 100 products
- Parallel (200 workers): ~30 seconds
- **Speedup**: 10x

---

### 3. Query Optimization

**Neo4j Optimizations**:

1. **Single Query Per Product** (instead of multiple queries)
   - Before: 3 queries per product A (neighbors, candidates, scoring)
   - After: 1 combined query
   - Speedup: 3x

2. **Product Type Pre-Detection**
   - Detect product type ONCE for product A
   - Add pattern filters to query for product B
   - Reduces candidates by 70-90%
   - Speedup: 5-10x

3. **Index Usage**
   ```cypher
   CREATE INDEX product_siid IF NOT EXISTS FOR (p:Product) ON (p.siid);
   CREATE INDEX store_name IF NOT EXISTS FOR (s:Store) ON (s.name);
   CREATE INDEX brand_name IF NOT EXISTS FOR (b:Brand) ON (b.name);
   ```

**PostgreSQL Optimizations**:

1. **Batch Loading**
   ```python
   # Load all products in one query instead of N queries
   siids = [product_a_siid] + [c['siid'] for c in candidates]
   features = load_features_batch(siids)  # Single query
   ```

2. **Connection Pooling**
   ```python
   from src.connectors.postgresql_connector import get_pooled_connection
   conn = get_pooled_connection()  # Reuses connections
   ```

---

### 4. Early Filtering

**Progressive Filtering**:

```
Start: 10,000 products in Store B
    ↓
After graph analysis: 300 candidates per product A
    ↓ (min_graph_score >= 0.5)
After filtering: ~120 candidates
    ↓
After embedding similarity: ~80 candidates
    ↓ (min_name_sim >= 0.5, min_desc_sim >= 0.5)
After filtering: ~40 candidates
    ↓
After Euclidean distance: ~25 candidates
    ↓ (min_euclidean_sim >= 0.0)
Final: Top 3 results
```

**Benefits**:
- Avoid computing embeddings for poor matches
- Avoid Euclidean distance for semantically dissimilar products
- Saves ~80% of computation time

---

### 5. Perfect Match Caching

**PostgreSQL Table**: `perfect_matches`

```sql
CREATE TABLE perfect_matches (
    id SERIAL PRIMARY KEY,
    store_a VARCHAR(50),
    store_b VARCHAR(50),
    product_a_siid VARCHAR(100),
    product_b_siid VARCHAR(100),
    combined_score FLOAT,
    validated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_perfect_matches_stores 
ON perfect_matches(store_a, store_b, product_a_siid);
```

**Usage**:
```python
# Check for existing matches BEFORE running model
existing = query_perfect_matches(store_a, store_b, product_siids)

if existing:
    return existing  # Skip model computation entirely
```

**Savings**: 100% computation time for products with existing matches

---

## Usage Examples

### Example 1: Basic Usage (API)

```bash
curl -X POST http://localhost:8000/compare \
  -H "Content-Type: application/json" \
  -d '{
    "store_a": "eroski-01013",
    "store_b": "makro-01013",
    "list_ids": ["bm-12345", "bm-67890"],
    "top_n_results": 3
  }'
```

**Response**:
```json
{
  "bm-12345": ["mk-98765", "mk-54321", "mk-11111"],
  "bm-67890": ["mk-22222", "mk-33333", "mk-44444"]
}
```

---

### Example 2: Custom Weights (Python)

```python
from src.models.model_variations.get_similar_neo4j_refactored import (
    model, ModelParameters, FoodWeights, FoodGraphWeights, CombinedWeights
)

params = ModelParameters(
    store_a="eroski-01013",
    store_b="makro-01013",
    list_ids=["bm-12345"],
    top_n_results=5,
    
    food_weights=FoodWeights(
        graph=FoodGraphWeights(
            brand=0.40,              # Increase brand importance
            format=0.20,
            subcategory=0.30,
            first_ingredient=0.25,
            second_ingredient=0.05
        ),
        combined=CombinedWeights(
            graph=0.30,              # More weight on graph
            name=0.30,
            description=0.30,
            euclidean=0.10
        )
    )
)

results = model(params)
print(results)
```

---

### Example 3: High Precision Mode

```python
params = ModelParameters(
    store_a="eroski-01013",
    store_b="makro-01013",
    list_ids=product_ids,
    top_n_results=1,  # Only best match
    
    quality_thresholds=QualityThresholds(
        min_graph_score=0.8,           # Very strict
        min_name_similarity=0.8,
        min_description_similarity=0.7,
        min_euclidean_similarity=0.5
    )
)

results = model(params)
# Fewer results, but high confidence
```

---

### Example 4: Processing Large Batch

```python
# Process 1000 products in batches of 100
product_ids = load_all_product_ids()  # 1000 IDs
batch_size = 100

all_results = {}

for i in range(0, len(product_ids), batch_size):
    batch = product_ids[i:i+batch_size]
    
    params = ModelParameters(
        store_a="eroski-01013",
        store_b="makro-01013",
        list_ids=batch,
        top_n_results=3
    )
    
    batch_results = model(params)
    all_results.update(batch_results)
    
    print(f"Processed {i+len(batch)}/{len(product_ids)} products")

# Save to file
with open('similarity_results.json', 'w') as f:
    json.dump(all_results, f, indent=2)
```

---

## Troubleshooting

### Issue 1: Low Match Quality

**Symptoms**:
- Too many irrelevant matches
- Low combined scores (<0.6)

**Possible Causes**:
1. Thresholds too low
2. Wrong product type detected
3. Missing graph relationships

**Solutions**:
```python
# 1. Increase thresholds
quality_thresholds=QualityThresholds(
    min_graph_score=0.7,        # Increase from 0.5
    min_name_similarity=0.7,
    min_description_similarity=0.6
)

# 2. Check product type in Neo4j
MATCH (p:Product {siid: 'bm-12345'})-[:COVERS]->(sc:Internal_Subcategory)
      -[:HAS_INTERNAL_SUBCATEGORY]->(c:Internal_Category)
      -[:HAS_INTERNAL_CATEGORY]->(t:Internal_Type)
RETURN t.name AS type, c.name AS category

# 3. Verify graph relationships exist
MATCH (p:Product {siid: 'bm-12345'})-[r]-(n)
RETURN type(r) AS relationship, labels(n) AS node_type, COUNT(*) AS count
```

---

### Issue 2: No Matches Found

**Symptoms**:
- Most products return empty results
- `similar_products_b: []`

**Possible Causes**:
1. Thresholds too strict
2. Store B not in Neo4j
3. Products not ingested properly

**Solutions**:
```python
# 1. Lower thresholds temporarily
quality_thresholds=QualityThresholds(
    min_graph_score=0.3,
    min_name_similarity=0.3,
    min_description_similarity=0.3
)

# 2. Verify Store B exists
MATCH (s:Store {name: 'makro-01013'}) RETURN s;
MATCH (s:Store {name: 'makro-01013'})-[:SELLS]->(p:Product)
RETURN COUNT(p) AS product_count;

# 3. Check product ingestion
MATCH (p:Product {siid: 'mk-98765'})
RETURN p.product_name, p.description;
```

---

### Issue 3: Slow Performance

**Symptoms**:
- Model takes >5 minutes for 100 products
- High memory usage

**Possible Causes**:
1. Too many workers (memory overflow)
2. No embeddings cache
3. Neo4j queries not optimized

**Solutions**:
```python
# 1. Reduce workers
model = ModelCombinedSimilarity()
results = model.cross_store_similarity_analysis(
    # ...
    max_workers=50,             # Reduce from 200
    max_embedding_workers=100   # Reduce from 300
)

# 2. Check cache exists
ls -lh data/processed/embeddings_cache.pkl
# If missing, first run will be slow (cache building)

# 3. Create Neo4j indexes
CREATE INDEX product_siid IF NOT EXISTS FOR (p:Product) ON (p.siid);
CREATE INDEX store_name IF NOT EXISTS FOR (s:Store) ON (s.name);
CREATE INDEX brand_name IF NOT EXISTS FOR (b:Brand) ON (b.name);
CALL db.indexes();  // Verify indexes exist
```

---

### Issue 4: Embedding Errors

**Symptoms**:
- `❌ Error calculating embeddings`
- API quota exceeded errors

**Possible Causes**:
1. Azure OpenAI quota exceeded
2. Invalid API credentials
3. Network issues

**Solutions**:
```python
# 1. Check Azure OpenAI quota
# Visit: Azure Portal → OpenAI Resource → Quotas

# 2. Verify credentials
from config.settings import AZURE_OPENAI_CONFIG
print(f"API Key: {AZURE_OPENAI_CONFIG['api_key'][:10]}...")
print(f"Endpoint: {AZURE_OPENAI_CONFIG['api_base']}")

# Test connection
from openai import OpenAI
client = OpenAI(
    api_key=AZURE_OPENAI_CONFIG['api_key'],
    base_url=AZURE_OPENAI_CONFIG['api_base']
)
response = client.embeddings.create(
    model="text-embedding-3-small",
    input="test"
)
print(f"Embedding dim: {len(response.data[0].embedding)}")

# 3. Reduce parallel workers
max_embedding_workers=50  # Reduce from 300 to avoid rate limits
```

---

### Issue 5: Memory Errors

**Symptoms**:
- `MemoryError: unable to allocate array`
- System freezes during execution

**Possible Causes**:
1. Too many embeddings loaded at once
2. Large batch size
3. No garbage collection

**Solutions**:
```python
# 1. Process in smaller batches
batch_size = 50  # Reduce from 100
for i in range(0, len(products), batch_size):
    batch = products[i:i+batch_size]
    # Process batch
    
# 2. Force garbage collection
import gc
results = model(params)
gc.collect()  # Free memory

# 3. Use generator for large datasets
def product_generator(product_ids, batch_size=50):
    for i in range(0, len(product_ids), batch_size):
        yield product_ids[i:i+batch_size]
```

---

## Appendix: Score Interpretation Guide

### Combined Score Ranges

| Score Range | Interpretation | Action |
|-------------|----------------|--------|
| **0.90 - 1.00** | Almost identical products | High confidence match |
| **0.80 - 0.89** | Very similar products | Good match, verify visually |
| **0.70 - 0.79** | Similar products | Acceptable match, may differ slightly |
| **0.60 - 0.69** | Moderately similar | Requires verification |
| **0.50 - 0.59** | Possibly similar | Low confidence, verify carefully |
| **< 0.50** | Different products | Likely not a match |

### Individual Score Interpretation

**Graph Score**:
- `> 0.8`: Shares most characteristics (brand, format, category, ingredients)
- `0.5 - 0.8`: Some shared characteristics
- `< 0.5`: Few shared characteristics

**Name Similarity**:
- `> 0.9`: Nearly identical names
- `0.7 - 0.9`: Similar names with minor differences
- `0.5 - 0.7`: Somewhat related names
- `< 0.5`: Different names

**Description Similarity**:
- `> 0.8`: Very similar descriptions
- `0.6 - 0.8`: Related descriptions
- `< 0.6`: Different descriptions

**Euclidean Similarity**:
- `> 0.9`: Nearly identical nutritional/numerical values
- `0.7 - 0.9`: Similar values
- `< 0.7`: Different values

---

**Version**: 1.0.0  
**Last Updated**: December 18, 2025  
**Authors**: AI Consumer Goods Team
