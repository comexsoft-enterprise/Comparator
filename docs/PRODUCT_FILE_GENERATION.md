# Product File Generation Documentation

## Table of Contents
1. [Overview](#overview)
2. [Architecture & Data Flow](#architecture--data-flow)
3. [Field Merging Strategy](#field-merging-strategy)
4. [The Build Process](#the-build-process)
5. [LLM Formatting Feature](#llm-formatting-feature)
6. [API Usage](#api-usage)
7. [Output Structure](#output-structure)
8. [Use Cases & Examples](#use-cases--examples)
9. [Troubleshooting](#troubleshooting)

---

## Overview

### What is Product File Generation?

The **Product File Generation** system creates comprehensive, deduplicated product information files by aggregating data from matched products across multiple supermarket chains. When products are identified as similar (via the similarity model), this system intelligently merges their information to provide a unified, accurate product representation.

### Key Features

- ✅ **Smart Field Merging**: Automatically determines the most accurate value for common fields (brand, format, nutritional info)
- ✅ **Multi-Store Aggregation**: Combines data from products matched across different stores
- ✅ **Null Value Cleaning**: Removes empty, null, and invalid data for clean output
- ✅ **LLM-Powered Formatting**: Optional AI-generated summaries for human-readable product files
- ✅ **Store-Specific Details**: Preserves unique information per store (prices, URLs, availability)

### Why It Matters

**Problem**: Same product exists in multiple stores with:
- Different names ("Agua Mineral Bezoya" vs "Bezoya Natural Water")
- Inconsistent data (one has EAN, another doesn't)
- Varying nutritional information quality
- Store-specific details (prices, URLs)

**Solution**: Product files merge the best data from all sources into a single authoritative record while preserving store-specific differences.

---

## Architecture & Data Flow

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    API Endpoint                                  │
│         GET /mongodb/product-file/{siid}                        │
│              ?format=true&lang=es                               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              get_product_file_by_siid()                         │
│         Main orchestration function                             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  build_product_file()                           │
│         Core aggregation logic                                  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ STEP 1: Find Reference Product in MongoDB               │   │
│  │ - Search across all collections                         │   │
│  │ - Find by siid                                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ STEP 2: Get Matched Product SIIDs from PostgreSQL       │   │
│  │ - Query product_match_validation table                  │   │
│  │ - Find all products linked to reference SIID            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ STEP 3: Load All Matched Products from MongoDB          │   │
│  │ - Fetch complete documents for each SIID                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ STEP 4: Merge Common Fields                             │   │
│  │ - Apply most-common-value logic                         │   │
│  │ - Handle brand, format, EAN, nutrition                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ STEP 5: Organize Store-Specific Fields                  │   │
│  │ - Create matched_products array                         │   │
│  │ - Preserve unique per-store data                        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ STEP 6: Clean Null Values                               │   │
│  │ - Remove None, "", "null"                               │   │
│  │ - Recursive cleaning                                    │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                   ┌─────────┴─────────┐
                   │  format=true?     │
                   └─────────┬─────────┘
                             │
                ┌────────────┼────────────┐
                │ Yes                     │ No
                ▼                         ▼
┌──────────────────────────┐   ┌──────────────────────┐
│ format_product_file_     │   │ Return Raw Product   │
│ with_llm()               │   │ File                 │
│                          │   └──────────────────────┘
│ - Generate prompt        │
│ - Call Azure OpenAI      │
│ - Parse formatted text   │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ Return {                 │
│   formatted_summary,     │
│   raw_data               │
│ }                        │
└──────────────────────────┘
```

### Database Interactions

**PostgreSQL** (`product_match_validation` table):
- Stores validated product matches
- Structure: `(product_a_siid, product_b_siid, combined_score, validated)`
- Used to find all products matched to the reference SIID

**MongoDB** (multiple collections):
- Stores complete product information per store
- Collections: `bm_products`, `eroski_01013_products`, `makro_01013_products`, etc.
- Used to retrieve full product documents

**Azure OpenAI** (optional):
- Model: `gpt-5-nano`
- Used only when `format=true`
- Generates human-readable summaries

---

## Field Merging Strategy

### Common Fields (Merged)

Fields that are **merged across all matched products** using most-common-value logic:

```python
COMMON_FIELDS = [
    'brand',              # Product brand (Coca-Cola, Bezoya, etc.)
    'format',             # Package format (bottle, can, box, bag)
    'measure_value',      # Numerical value (1.5, 500, 2)
    'unit_measure',       # Unit (L, ml, g, kg)
    'model',              # Product model (for electronics)
    'ean',                # European Article Number (barcode)
    # + All fields containing 'nutri' (case-insensitive)
]
```

**Nutritional Fields** (automatically detected):
- `energy_kcal_100g`
- `fat_100g`
- `saturated_fat_100g`
- `carbohydrates_100g`
- `sugars_100g`
- `proteins_100g`
- `salt_100g`
- `fiber_100g`
- `sodium_100g`
- Any other field with "nutri" in the name

### Non-Common Fields (Store-Specific)

Fields that are **preserved per store** in the `matched_products` array:

- `siid` - Store-specific product identifier
- `store` - Store name
- `price` - Current price (varies by store)
- `url` - Product URL (store-specific)
- `product_name` - May vary slightly by store
- `description` - Store's product description
- `availability` - Stock status
- `category_path` - Store's categorization
- `ingredients` - May have different languages/formats
- `allergens` - Allergen information
- All other taxonomy fields not in COMMON_FIELDS

---

## The Build Process

### Step 1: Find Reference Product

**Function**: `get_product_from_mongodb_by_siid(siid)`

```python
# Search all MongoDB collections for the SIID
db = get_mongo_client()
collections = db.list_collection_names()

for coll_name in collections:
    coll = db[coll_name]
    doc = coll.find_one({"siid": siid})
    if doc:
        return doc
```

**Example**:
```python
siid = "eroski_01013-01-12345"

# Searches:
# - bm_products
# - eroski_01013_products  ← Found here!
# - makro_01013_products
# - ...

reference_product = {
    "siid": "eroski_01013-01-12345",
    "product_name": "Agua Mineral Natural Bezoya 1.5L",
    "brand": "Bezoya",
    "price": 0.60,
    "ean": "8410161910050",
    # ... more fields
}
```

---

### Step 2: Get Matched Product SIIDs

**Function**: `get_matched_product_siids(siid)`

```sql
SELECT DISTINCT product_a_siid, product_b_siid 
FROM product_match_validation 
WHERE product_a_siid = 'eroski_01013-01-12345' 
   OR product_b_siid = 'eroski_01013-01-12345'
```

**Logic**:
- Query looks in **both directions** (product_a and product_b)
- Collects all unique SIIDs connected to the reference
- Includes the reference SIID itself

**Example Result**:
```python
matched_siids = [
    "eroski_01013-01-12345",   # Reference product
    "makro_01013-01-98765",    # Match 1
    "aldi_01013-01-54321"      # Match 2
]
```

---

### Step 3: Load All Matched Products

**Function**: `get_products_from_mongodb_by_siids(siids)`

```python
products = []
for siid in matched_siids:
    product = get_product_from_mongodb_by_siid(siid)
    if product:
        products.append(product)

return products
```

**Example Result**:
```python
matched_products = [
    {
        "siid": "eroski_01013-01-12345",
        "store": "eroski-01013",
        "product_name": "Agua Mineral Natural Bezoya 1.5L",
        "brand": "Bezoya",
        "ean": "8410161910050",
        "price": 0.60,
        "energy_kcal_100g": 0,
        # ...
    },
    {
        "siid": "makro_01013-01-98765",
        "store": "makro-01013",
        "product_name": "Bezoya Natural Water 1.5L",
        "brand": "Bezoya",
        "ean": "8410161910050",  # Same EAN
        "price": 0.55,           # Different price
        "energy_kcal_100g": 0,
        # ...
    },
    {
        "siid": "aldi_01013-01-54321",
        "store": "aldi-01013",
        "product_name": "Agua Bezoya 1.5L",
        "brand": "Bezoya",
        "ean": "",               # Missing EAN
        "price": 0.58,
        "energy_kcal_100g": 0,
        # ...
    }
]
```

---

### Step 4: Merge Common Fields

**Function**: `merge_common_fields(products, reference_siid)`

**Algorithm**: Most Common Value with Tie-Breaking

```python
def get_most_common_value(values, reference_value):
    # 1. Filter out None, "", "null"
    valid_values = [v for v in values if v and str(v).lower() != 'null']
    
    if not valid_values:
        return reference_value
    
    # 2. Count occurrences
    counter = Counter(valid_values)
    most_common = counter.most_common(2)
    
    # 3. If only one unique value, return it
    if len(most_common) == 1:
        return most_common[0][0]
    
    # 4. If tie, use reference value
    if most_common[0][1] == most_common[1][1]:
        return reference_value
    
    # 5. Return most common
    return most_common[0][0]
```

**Example**:

```python
# Brand field
values = ["Bezoya", "Bezoya", "Bezoya"]
result = "Bezoya"  # All agree

# EAN field
values = ["8410161910050", "8410161910050", ""]
result = "8410161910050"  # Most common (2/3)

# Price field (NOT common field, won't be merged)
# This will be preserved per-store in matched_products

# Energy field
values = [0, 0, 0]
result = 0  # All agree

merged_common_fields = {
    "brand": "Bezoya",
    "format": "bottle",
    "measure_value": 1.5,
    "unit_measure": "L",
    "ean": "8410161910050",
    "energy_kcal_100g": 0,
    "fat_100g": 0,
    "carbohydrates_100g": 0,
    # ... other common fields
}
```

---

### Step 5: Organize Store-Specific Fields

**Logic**: Create separate entries for each matched product containing only non-common fields

```python
matched_products_info = []

for product in matched_products:
    product_info = {
        'siid': product['siid'],
        'store': product['store'],
    }
    
    # Add non-common fields
    for field, value in product.items():
        if field.startswith('_'):  # Skip MongoDB internals
            continue
        
        is_common = (
            field.lower() in COMMON_FIELDS or 
            is_nutritional_field(field)
        )
        
        if not is_common and field not in ['siid', 'store']:
            product_info[field] = value
    
    matched_products_info.append(product_info)
```

**Example Result**:
```python
matched_products_info = [
    {
        "siid": "eroski_01013-01-12345",
        "store": "eroski-01013",
        "product_name": "Agua Mineral Natural Bezoya 1.5L",
        "price": 0.60,
        "url": "https://eroski.es/product/12345",
        "description": "Agua mineral natural de manantial...",
        "availability": "in_stock"
    },
    {
        "siid": "makro_01013-01-98765",
        "store": "makro-01013",
        "product_name": "Bezoya Natural Water 1.5L",
        "price": 0.55,
        "url": "https://makro.es/product/98765",
        "description": "Natural spring water from Bezoya...",
        "availability": "in_stock"
    },
    {
        "siid": "aldi_01013-01-54321",
        "store": "aldi-01013",
        "product_name": "Agua Bezoya 1.5L",
        "price": 0.58,
        "url": "https://aldi.es/product/54321",
        "description": "Agua Bezoya 1.5 litros",
        "availability": "limited_stock"
    }
]
```

---

### Step 6: Clean Null Values

**Function**: `clean_null_values(obj)`

**Recursive Cleaning**:
```python
def clean_null_values(obj):
    if isinstance(obj, dict):
        cleaned = {}
        for key, value in obj.items():
            # Skip None
            if value is None:
                continue
            # Skip empty strings or "null" text
            if isinstance(value, str) and (value.strip() == "" or value.strip().lower() == "null"):
                continue
            # Recurse for nested structures
            if isinstance(value, (dict, list)):
                cleaned_value = clean_null_values(value)
                if cleaned_value:  # Only add if not empty
                    cleaned[key] = cleaned_value
            else:
                cleaned[key] = value
        return cleaned
    
    elif isinstance(obj, list):
        # Similar logic for lists
        # ...
    
    return obj
```

**Before Cleaning**:
```json
{
    "brand": "Bezoya",
    "model": null,
    "ean": "8410161910050",
    "format": "",
    "description": "null",
    "measure_value": 1.5
}
```

**After Cleaning**:
```json
{
    "brand": "Bezoya",
    "ean": "8410161910050",
    "measure_value": 1.5
}
```

---

## LLM Formatting Feature

### Overview

When `format=true`, the product file is processed by Azure OpenAI to generate a human-readable summary in markdown format.

### Implementation

**Function**: `format_product_file_with_llm(product_file, lang="es")`

```python
client = OpenAI(
    api_key=AZURE_OPENAI_CONFIG["api_key"],
    base_url=AZURE_OPENAI_CONFIG["api_base"]
)

response = client.responses.create(
    model="gpt-5-nano",
    input=[
        {"role": "system", "content": system_context},
        {"role": "user", "content": product_data_prompt}
    ],
    reasoning={"effort": "low"},
    text={"verbosity": "low"}
)

formatted_summary = response.output[1].content[0].text
```

### System Context (Spanish)

```text
Eres un especialista en información de productos. Tu tarea es crear resúmenes 
claros y bien estructurados para usuarios finales.

Crea un resumen completo del producto con las siguientes secciones:

1. **Resumen del Producto**: Nombre del producto: descripción breve con marca
2. **Atributos Principales**: Marca, formato, medidas, modelo, EAN, descripción
3. **Información Nutricional**: Todos los campos nutricionales si están disponibles
4. **Resumen de Productos Similares Encontrados**: Cuántos productos se encontraron 
   y de qué tiendas
5. **Detalles Específicos por Tienda**: Diferencias clave entre los productos 
   encontrados en diferentes tiendas. Evita repetir información.

Incluye solo los campos si la información está disponible. Usa viñetas para mayor 
claridad. Formatea la respuesta en markdown claro con encabezados y viñetas 
apropiados. Sé conciso pero informativo.
```

### Example Formatted Output

**Input**: Raw product file JSON

**Output**:
```markdown
# Agua Mineral Natural Bezoya 1.5L

## Resumen del Producto
Agua mineral natural de manantial de la marca Bezoya, presentada en formato 
de botella de 1.5 litros.

## Atributos Principales
- **Marca**: Bezoya
- **Formato**: Botella
- **Medidas**: 1.5 L
- **EAN**: 8410161910050
- **Descripción**: Agua mineral natural de manantial con bajo contenido en sodio

## Información Nutricional
- **Energía**: 0 kcal/100g
- **Grasas**: 0 g/100g
- **Carbohidratos**: 0 g/100g
- **Proteínas**: 0 g/100g
- **Sal**: 0 g/100g
- **Sodio**: 11.6 mg/L

## Resumen de Productos Similares
Se encontraron 3 productos equivalentes en las siguientes tiendas:
- Eroski (01013)
- Makro (01013)
- Aldi (01013)

## Detalles Específicos por Tienda

### Eroski (01013)
- **Precio**: 0.60€
- **Nombre**: Agua Mineral Natural Bezoya 1.5L
- **Disponibilidad**: En stock
- **URL**: https://eroski.es/product/12345

### Makro (01013)
- **Precio**: 0.55€ ⭐ (Mejor precio)
- **Nombre**: Bezoya Natural Water 1.5L
- **Disponibilidad**: En stock
- **URL**: https://makro.es/product/98765

### Aldi (01013)
- **Precio**: 0.58€
- **Nombre**: Agua Bezoya 1.5L
- **Disponibilidad**: Stock limitado
- **URL**: https://aldi.es/product/54321
```

### Error Handling

If LLM formatting fails:
```json
{
    "formatted_summary": null,
    "raw_data": { /* complete product file */ },
    "formatting_error": "API timeout after 30 seconds"
}
```

---

## API Usage

### Endpoint Definition

```python
GET /mongodb/product-file/{siid}
```

**Parameters**:
- `siid` (path parameter, required): Product SIID to retrieve
- `format` (query parameter, optional): Enable LLM formatting (default: `true`)
- `lang` (query parameter, optional): Language for formatted output (default: `"es"`)

**Response**:
- Status 200: Success
- Status 404: Product not found
- Status 500: Internal server error

---

### Example 1: Basic Usage (Raw Data)

**Request**:
```bash
curl -X GET "http://localhost:8000/mongodb/product-file/eroski_01013-01-12345?format=false"
```

**Response**:
```json
{
    "siid": "eroski_01013-01-12345",
    "product_hash": "a1b2c3d4e5f6...",
    "brand": "Bezoya",
    "format": "bottle",
    "measure_value": 1.5,
    "unit_measure": "L",
    "ean": "8410161910050",
    "energy_kcal_100g": 0,
    "fat_100g": 0,
    "carbohydrates_100g": 0,
    "proteins_100g": 0,
    "salt_100g": 0,
    "matched_products_count": 3,
    "matched_products": [
        {
            "siid": "eroski_01013-01-12345",
            "store": "eroski-01013",
            "product_name": "Agua Mineral Natural Bezoya 1.5L",
            "price": 0.60,
            "url": "https://eroski.es/product/12345",
            "description": "Agua mineral natural de manantial..."
        },
        {
            "siid": "makro_01013-01-98765",
            "store": "makro-01013",
            "product_name": "Bezoya Natural Water 1.5L",
            "price": 0.55,
            "url": "https://makro.es/product/98765",
            "description": "Natural spring water from Bezoya..."
        },
        {
            "siid": "aldi_01013-01-54321",
            "store": "aldi-01013",
            "product_name": "Agua Bezoya 1.5L",
            "price": 0.58,
            "url": "https://aldi.es/product/54321",
            "description": "Agua Bezoya 1.5 litros"
        }
    ]
}
```

---

### Example 2: Formatted Output (LLM Summary)

**Request**:
```bash
curl -X GET "http://localhost:8000/mongodb/product-file/eroski_01013-01-12345?format=true&lang=es"
```

**Response**:
```json
{
    "formatted_summary": "# Agua Mineral Natural Bezoya 1.5L\n\n## Resumen del Producto\n...",
    "raw_data": {
        "siid": "eroski_01013-01-12345",
        "brand": "Bezoya",
        "matched_products": [...]
    }
}
```

---

### Example 3: Python Client

```python
import requests

def get_product_file(siid: str, formatted: bool = False, lang: str = "es"):
    """Get product file from API"""
    url = f"http://localhost:8000/mongodb/product-file/{siid}"
    params = {"format": formatted, "lang": lang}
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

# Get raw data
raw_product = get_product_file("eroski_01013-01-12345", formatted=False)
print(f"Brand: {raw_product['brand']}")
print(f"Matched products: {raw_product['matched_products_count']}")

# Get formatted summary
formatted_product = get_product_file("eroski_01013-01-12345", formatted=True)
print(formatted_product['formatted_summary'])
```

---

## Output Structure

### Raw Product File Structure

```json
{
    // Identification
    "siid": "string",                    // Reference product SIID
    "product_hash": "string",            // Product hash (dedupe identifier)
    
    // Common Fields (Merged)
    "brand": "string",                   // Most common brand value
    "format": "string",                  // Most common format
    "measure_value": number,             // Most common measure
    "unit_measure": "string",            // Most common unit
    "model": "string",                   // Most common model
    "ean": "string",                     // Most common EAN
    
    // Nutritional Fields (Merged)
    "energy_kcal_100g": number,          // Most common energy value
    "fat_100g": number,                  // Most common fat value
    "saturated_fat_100g": number,
    "carbohydrates_100g": number,
    "sugars_100g": number,
    "proteins_100g": number,
    "salt_100g": number,
    "fiber_100g": number,
    "sodium_100g": number,
    // ... any other nutritional fields
    
    // Match Metadata
    "matched_products_count": number,    // Total matched products
    
    // Store-Specific Data
    "matched_products": [
        {
            "siid": "string",            // Product SIID
            "store": "string",           // Store name
            "product_name": "string",    // Store-specific name
            "price": number,             // Store price
            "url": "string",             // Product URL
            "description": "string",     // Store description
            "availability": "string",    // Stock status
            "ingredients": "string",     // Ingredients list
            "allergens": "string",       // Allergens
            "category_path": "string",   // Store categorization
            // ... other non-common fields
        },
        // ... more matched products
    ]
}
```

### Formatted Product File Structure

```json
{
    "formatted_summary": "string",       // Markdown-formatted summary (LLM-generated)
    "raw_data": {                        // Complete raw product file
        // ... all fields from raw structure above
    }
}
```

---

## Use Cases & Examples

### Use Case 1: Price Comparison Dashboard

**Scenario**: Display same product with prices across multiple stores

```python
product_file = get_product_file("eroski_01013-01-12345", formatted=False)

# Extract price information
prices = [
    {
        "store": p["store"],
        "price": p["price"],
        "url": p["url"]
    }
    for p in product_file["matched_products"]
]

# Sort by price
prices.sort(key=lambda x: x["price"])

print(f"Product: {product_file['brand']} {product_file['measure_value']}{product_file['unit_measure']}")
print("\nPrices across stores:")
for price_info in prices:
    print(f"  {price_info['store']}: €{price_info['price']:.2f} - {price_info['url']}")
```

**Output**:
```
Product: Bezoya 1.5L

Prices across stores:
  makro-01013: €0.55 - https://makro.es/product/98765
  aldi-01013: €0.58 - https://aldi.es/product/54321
  eroski-01013: €0.60 - https://eroski.es/product/12345
```

---

### Use Case 2: Product Catalog Generation

**Scenario**: Generate user-facing product catalog with unified information

```python
def generate_catalog_entry(siid: str) -> dict:
    """Generate catalog entry with merged data"""
    product = get_product_file(siid, formatted=True, lang="es")
    
    return {
        "title": f"{product['raw_data']['brand']} {product['raw_data']['measure_value']}{product['raw_data']['unit_measure']}",
        "description": product['formatted_summary'],
        "ean": product['raw_data'].get('ean'),
        "availability": any(
            p.get('availability') == 'in_stock' 
            for p in product['raw_data']['matched_products']
        ),
        "min_price": min(
            p['price'] 
            for p in product['raw_data']['matched_products']
        ),
        "stores": [
            {
                "name": p['store'],
                "price": p['price'],
                "link": p['url']
            }
            for p in product['raw_data']['matched_products']
        ]
    }
```

---

### Use Case 3: Data Quality Analysis

**Scenario**: Identify products with incomplete nutritional information

```python
def analyze_data_completeness(siid: str):
    """Analyze data completeness across matched products"""
    product = get_product_file(siid, formatted=False)
    
    # Check nutritional fields
    nutritional_fields = [
        'energy_kcal_100g', 'fat_100g', 'carbohydrates_100g',
        'proteins_100g', 'salt_100g'
    ]
    
    completeness = {}
    for field in nutritional_fields:
        has_value = product.get(field) is not None
        completeness[field] = has_value
    
    # Check per-store data completeness
    store_completeness = {}
    for matched_product in product['matched_products']:
        store = matched_product['store']
        has_description = bool(matched_product.get('description'))
        has_ingredients = bool(matched_product.get('ingredients'))
        has_allergens = bool(matched_product.get('allergens'))
        
        store_completeness[store] = {
            'description': has_description,
            'ingredients': has_ingredients,
            'allergens': has_allergens
        }
    
    return {
        'nutritional_completeness': completeness,
        'store_data_completeness': store_completeness
    }
```

---

### Use Case 4: Multi-Language Product Export

**Scenario**: Generate product summaries in different languages

```python
def get_multilingual_product(siid: str):
    """Get product file in multiple languages"""
    languages = ['es', 'en', 'fr']
    
    result = {
        'raw_data': None,
        'summaries': {}
    }
    
    for lang in languages:
        product = get_product_file(siid, formatted=True, lang=lang)
        
        # Store raw data once
        if result['raw_data'] is None:
            result['raw_data'] = product['raw_data']
        
        # Store summary per language
        result['summaries'][lang] = product['formatted_summary']
    
    return result
```

---

## Troubleshooting

### Issue 1: Product Not Found

**Symptoms**:
```json
{
    "error": "Product with siid eroski_01013-01-12345 not found"
}
```

**Possible Causes**:
1. SIID doesn't exist in MongoDB
2. SIID format incorrect
3. Product not yet ingested

**Solutions**:
```bash
# 1. Check if product exists in MongoDB
curl "http://localhost:8000/mongodb/product/eroski_01013-01-12345"

# 2. Verify SIID format (should be: store_storeId-storeId-productId)
# Correct: "eroski_01013-01-12345"
# Wrong: "eroski-12345" or "eroski_01013-12345"

# 3. Check ingestion logs
tail -f logs/ingestion.log | grep "eroski_01013-01-12345"
```

---

### Issue 2: No Matched Products

**Symptoms**:
```json
{
    "siid": "eroski_01013-01-12345",
    "brand": "Bezoya",
    "matched_products_count": 1,
    "matched_products": [
        {
            "siid": "eroski_01013-01-12345",
            "store": "eroski-01013"
        }
    ]
}
```

**Possible Causes**:
1. Product hasn't been matched yet (similarity model not run)
2. No similar products exist in other stores
3. Matches not in `product_match_validation` table

**Solutions**:
```sql
-- Check PostgreSQL for matches
SELECT * FROM product_match_validation 
WHERE product_a_siid = 'eroski_01013-01-12345' 
   OR product_b_siid = 'eroski_01013-01-12345';

-- If no results, run similarity model
```

```python
# Run similarity model for the product
from src.models.model_variations.get_similar_neo4j_refactored import model, ModelParameters

params = ModelParameters(
    store_a="eroski-01013",
    store_b="makro-01013",
    list_ids=["eroski_01013-01-12345"],
    top_n_results=3
)

results = model(params)
```

---

### Issue 3: Common Fields Not Merged

**Symptoms**:
```json
{
    "brand": "Bezoya",  // Correct
    "price": 0.60,      // Should be in matched_products, not here!
    "matched_products": [...]
}
```

**Possible Causes**:
1. Field incorrectly classified as common
2. Field name case mismatch

**Solutions**:
```python
# Check COMMON_FIELDS definition in product_files.py
COMMON_FIELDS = [
    'brand',
    'format',
    'measure_value',
    'unit_measure',
    'model',
    'ean',
]

# Verify field is NOT in COMMON_FIELDS
assert 'price' not in COMMON_FIELDS  # Should be True

# If field name has different casing in MongoDB:
# "Price" vs "price" → Will be treated differently
# Solution: Normalize field names in preprocessing
```

---

### Issue 4: LLM Formatting Fails

**Symptoms**:
```json
{
    "formatted_summary": null,
    "raw_data": {...},
    "formatting_error": "The server had an error processing your request"
}
```

**Possible Causes**:
1. Azure OpenAI API quota exceeded
2. API timeout
3. Invalid credentials
4. Product data too large

**Solutions**:
```python
# 1. Check Azure OpenAI quota
# Azure Portal → OpenAI → Quotas

# 2. Verify credentials
from config.settings import AZURE_OPENAI_CONFIG
print(f"API Key: {AZURE_OPENAI_CONFIG['api_key'][:10]}...")
print(f"Endpoint: {AZURE_OPENAI_CONFIG['api_base']}")

# 3. Test with smaller product
# Use format=false to get raw data first
# Then manually test formatting with subset of data

# 4. Fallback: Use raw data
product = get_product_file(siid, formatted=False)
# Process raw_data manually without LLM
```

---

### Issue 5: Missing Nutritional Data

**Symptoms**:
```json
{
    "brand": "Bezoya",
    "matched_products": [...],
    // No energy_kcal_100g, fat_100g, etc.
}
```

**Possible Causes**:
1. Products don't have nutritional data in MongoDB
2. Nutritional fields not detected (don't contain "nutri")
3. All values are null (cleaned out)

**Solutions**:
```python
# 1. Check raw MongoDB data
from src.connectors.mongodb_connector import get_mongo_client
db = get_mongo_client()
product = db.eroski_01013_products.find_one({"siid": "eroski_01013-01-12345"})

# Check for nutritional fields
nutritional_fields = [k for k in product.keys() if 'nutri' in k.lower()]
print(f"Nutritional fields: {nutritional_fields}")

# 2. Verify field names
# Expected: energy_kcal_100g, fat_100g, etc.
# If named differently: energy, calories → Won't be detected as nutritional

# 3. Check if values are null
print(f"Energy value: {product.get('energy_kcal_100g')}")
# If None or "" → Will be filtered out
```

---

## Performance Considerations

### Query Optimization

**Current Performance**:
- **MongoDB lookups**: ~50ms per SIID
- **PostgreSQL match query**: ~20ms
- **Field merging**: ~5ms
- **LLM formatting**: ~2-5 seconds

**Total Time**:
- Without LLM: ~150ms (3 matched products)
- With LLM: ~2-5 seconds

### Caching Strategy

**Recommended**:
```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def get_product_file_cached(siid: str, formatted: bool = False):
    """Cached version of get_product_file"""
    return get_product_file_by_siid(siid, format_with_llm=formatted)

# Cache expires after 1 hour
# Refresh when product data changes
```

### Batch Processing

For multiple products:
```python
from concurrent.futures import ThreadPoolExecutor

def get_product_files_batch(siids: List[str], formatted: bool = False):
    """Get multiple product files in parallel"""
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(get_product_file_by_siid, siid, formatted): siid
            for siid in siids
        }
        
        results = {}
        for future in as_completed(futures):
            siid = futures[future]
            try:
                results[siid] = future.result()
            except Exception as e:
                results[siid] = {"error": str(e)}
        
        return results
```

---

**Version**: 1.0.0  
**Last Updated**: December 18, 2025  
**Module**: `src/api/api_utils/product_files.py`  
**API Endpoint**: `GET /mongodb/product-file/{siid}`
