"""
The model stores the matches between products in the postgresql database. 
This file aims to retrieve a product and its associated matches from the postgres database.
In the case there are other products matched in the database, they will be retrieved as well.
Then, the product file is formed in the following way:
1) Common information between stores
- Brand
- Format
- Measure value
- Unit measure
- Model
- EAN
- Any column that contains the lemma nutri
2) Non-common information between stores
The rest in the taxonomy.
This information that appears in the product file comes from the information stores in MongoDB. 
To obtain the common information, we implement a method that checks if the information is empty, and returns the most common value between the matched products. In the case there is not a most common value, it returns the information from the product it is being identified by siid.
The product is reached from the api endpoint /mongodb/product/{siid}
"""

import logging
import json
from typing import Dict, Any, List, Optional
from collections import Counter

from config.settings import AZURE_OPENAI_CONFIG
from src.connectors.postgresql_connector import get_postgresql_connection
from src.connectors.mongodb_connector import get_mongo_client
from openai import OpenAI

logger = logging.getLogger(__name__)

# Define common fields that should be merged across stores
COMMON_FIELDS = [
    'brand',
    'format',
    'measure_value',
    'unit_measure',
    'model',
    'ean',
]


def is_nutritional_field(field_name: str) -> bool:
    """
    Check if a field name contains 'nutri' (case-insensitive).
    
    Args:
        field_name: The field name to check
        
    Returns:
        True if field contains 'nutri', False otherwise
    """
    return 'nutri' in field_name.lower()


def get_matched_product_siids(siid: str) -> List[str]:
    """
    Retrieve all product siids that are matched with the given siid from PostgreSQL.
    This includes the original siid and all its matches from product_match_validation table.
    
    Args:
        siid: The product siid to find matches for
        
    Returns:
        List of siid values including the original and all matches
    """
    try:
        conn = get_postgresql_connection()
        if not conn:
            logger.error("Failed to connect to PostgreSQL")
            return [siid]
        
        cursor = conn.cursor()
        
        # Query to get all matches from product_match_validation table
        # Looking in both directions (product_a_siid and product_b_siid)
        query = """
            SELECT DISTINCT product_a_siid, product_b_siid 
            FROM product_match_validation 
            WHERE product_a_siid = %s OR product_b_siid = %s
        """
        
        cursor.execute(query, (siid, siid))
        results = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Collect all unique siids
        matched_siids = {siid}
        for row in results:
            if row[0]:  # product_a_siid
                matched_siids.add(row[0])
            if row[1]:  # product_b_siid
                matched_siids.add(row[1])
        
        logger.info(f"Found {len(matched_siids)} matched products for siid {siid}")
        return list(matched_siids)
        
    except Exception as e:
        logger.error(f"Error retrieving matched products: {e}", exc_info=True)
        return [siid]


def get_product_from_mongodb_by_siid(siid: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve product information from MongoDB by siid.
    Searches across all collections.
    Also retrieves price_history from PostgreSQL if product_hash is available.
    
    Args:
        siid: The product siid to search for
        
    Returns:
        Product document from MongoDB with price_history or None if not found
    """
    try:
        db = get_mongo_client()
        if db is None:
            logger.error("Failed to connect to MongoDB")
            return None
        
        collections = db.list_collection_names()
        
        for coll_name in collections:
            coll = db[coll_name]
            doc = coll.find_one({"siid": siid})
            
            if doc:
                logger.debug(f"Found product with siid {siid} in collection {coll_name}")
                
                # If product has a product_hash, try to fetch price_history from PostgreSQL
                product_hash = None
                try:
                    product_hash = doc.get('product_hash') or doc.get('Product_Hash')
                except Exception:
                    product_hash = None

                if product_hash:
                    try:
                        from src.connectors.postgresql_connector import get_postgresql_connection
                        pg_conn = get_postgresql_connection()
                        if pg_conn:
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
                                doc['price_history'] = ph
                            cur.close()
                            pg_conn.close()
                    except Exception as e:
                        logger.warning(f"Could not fetch price_history from PostgreSQL for product_hash {product_hash}: {e}")
                        doc['price_history'] = None
                
                return doc
        
        logger.warning(f"Product with siid {siid} not found in MongoDB")
        return None
        
    except Exception as e:
        logger.error(f"Error retrieving product from MongoDB: {e}", exc_info=True)
        return None


def get_products_from_mongodb_by_siids(siids: List[str]) -> List[Dict[str, Any]]:
    """
    Retrieve multiple products from MongoDB by their siids.
    
    Args:
        siids: List of product siids to retrieve
        
    Returns:
        List of product documents
    """
    products = []
    for siid in siids:
        product = get_product_from_mongodb_by_siid(siid)
        if product:
            products.append(product)
    
    return products


def clean_null_values(obj):
    """
    Recursively remove keys with None, empty string, or 'null' (case-insensitive) values.
    
    Args:
        obj: Object to clean (dict, list, or other)
        
    Returns:
        Cleaned object with null values removed
    """
    if isinstance(obj, dict):
        cleaned = {}
        for key, value in obj.items():
            # Skip None values
            if value is None:
                continue
            # Skip empty strings or 'null' text
            if isinstance(value, str) and (value.strip() == "" or value.strip().lower() == "null"):
                continue
            # Recurse for nested structures
            if isinstance(value, (dict, list)):
                cleaned_value = clean_null_values(value)
                # Only add if the cleaned value is not empty
                if cleaned_value or cleaned_value == 0 or cleaned_value is False:
                    cleaned[key] = cleaned_value
            else:
                cleaned[key] = value
        return cleaned
    elif isinstance(obj, list):
        cleaned_list = []
        for item in obj:
            # Skip None values
            if item is None:
                continue
            # Skip empty strings or 'null' text
            if isinstance(item, str) and (item.strip() == "" or item.strip().lower() == "null"):
                continue
            # Recurse for nested structures
            if isinstance(item, (dict, list)):
                cleaned_list.append(clean_null_values(item))
            else:
                cleaned_list.append(item)
        return cleaned_list
    else:
        return obj


def format_product_file_with_llm(product_file: Dict[str, Any], lang: str = "es") -> Dict[str, Any]:
    """
    Format product file data into a user-friendly summary using an LLM.
    
    Args:
        product_file: Raw product file data
        
    Returns:
        Dictionary containing both raw data and formatted summary
    """
    try:
        # Initialize Azure OpenAI client (using OpenAI client like llm_completion)
        
        
        client = OpenAI(
            api_key=AZURE_OPENAI_CONFIG["api_key"],
            base_url=AZURE_OPENAI_CONFIG["api_base"]
        )
        
        # System context for the LLM
        context = """Eres un especialista en información de productos. Tu tarea es crear resúmenes claros y bien estructurados para usuarios finales.

Crea un resumen completo del producto con las siguientes secciones:

1. **Resumen del Producto**: Nombre del producto: descripción breve con marca
2. **Atributos Principales**: Marca, formato, medidas, modelo, EAN, descripción
3. **Información Nutricional**: Todos los campos nutricionales si están disponibles
4. **Resumen de Productos Similares Encontrados**: Cuántos productos se encontraron y de qué tiendas
5. **Detalles Específicos por Tienda**: Diferencias clave entre los productos encontrados en diferentes tiendas. Evita repetir información.

Incluye solo los campos si la información está disponible. Usa viñetas para mayor claridad.
Formatea la respuesta en markdown claro con encabezados y viñetas apropiados. Sé conciso pero informativo. Si falta alguna información, no la menciones.
No añadas comentarios adicionales fuera del resumen solicitado.
"""
        
        # Create user prompt with the product data
        prompt = f"""Dada la siguiente información del producto, crea un resumen claro y bien estructurado:

Datos del Producto:
{json.dumps(product_file, indent=2, ensure_ascii=False)}
"""
        
        # Make the LLM call using responses.create (for gpt-5-nano compatibility)
        response = client.responses.create(
            model=AZURE_OPENAI_CONFIG.get("deployment_name", "gpt-5-nano"),
            input=[
                {"role": "system", "content": context},
                {"role": "user", "content": prompt}
            ],
            reasoning={
                "effort": "low"
            },
            text={
                "verbosity": "low"
            }
        )
        
        logger.info("LLM API call completed successfully. Processing response...")
        
        # Extract the formatted summary from the response
        formatted_summary = response.output[1].content[0].text.strip()
        
        # Return both raw data and formatted summary
        return {
            "formatted_summary": formatted_summary,
            "raw_data": product_file
        }
        
    except Exception as e:
        logger.error(f"Error formatting product file with LLM: {e}", exc_info=True)
        # If LLM formatting fails, return raw data only
        return {
            "formatted_summary": None,
            "raw_data": product_file,
            "formatting_error": str(e)
        }


def get_most_common_value(values: List[Any], reference_value: Any = None) -> Any:
    """
    Get the most common non-empty value from a list.
    If there's no clear winner, return the reference_value.
    If reference_value is None, return the first most common value.
    
    Args:
        values: List of values to analyze
        reference_value: The reference value to return if there's a tie
        
    Returns:
        Most common value or reference_value
    """
    # Filter out None, empty strings, and 'null' values
    valid_values = [
        v for v in values 
        if v is not None and v != '' and str(v).lower() != 'null'
    ]
    
    if not valid_values:
        return reference_value
    
    # Count occurrences
    counter = Counter(valid_values)
    most_common = counter.most_common(2)
    
    # If there's only one unique value, return it
    if len(most_common) == 1:
        return most_common[0][0]
    
    # If the two most common have the same count, it's a tie
    if len(most_common) >= 2 and most_common[0][1] == most_common[1][1]:
        # Return reference value if available, otherwise first value
        return reference_value if reference_value else most_common[0][0]
    
    # Return the most common value
    return most_common[0][0]


def merge_common_fields(products: List[Dict[str, Any]], reference_siid: str) -> Dict[str, Any]:
    """
    Merge common fields from multiple products using most common value logic.
    
    Args:
        products: List of product documents from MongoDB
        reference_siid: The siid of the reference product
        
    Returns:
        Dictionary with merged common field values
    """
    merged = {}
    
    # Find the reference product
    reference_product = None
    for product in products:
        if product.get('siid') == reference_siid:
            reference_product = product
            break
    
    # Get all field names across all products
    all_fields = set()
    for product in products:
        all_fields.update(product.keys())
    
    # Process each field
    for field in all_fields:
        # Skip internal MongoDB fields
        if field.startswith('_'):
            continue
        
        # Check if this is a common field
        is_common = (
            field.lower() in COMMON_FIELDS or 
            is_nutritional_field(field)
        )
        
        if is_common:
            # Collect values from all products
            values = [product.get(field) for product in products]
            reference_value = reference_product.get(field) if reference_product else None
            
            # Get the most common value
            merged[field] = get_most_common_value(values, reference_value)
    
    return merged


def build_product_file(siid: str) -> Dict[str, Any]:
    """
    Build a complete product file for the given siid.
    This includes:
    1. Common fields (merged from all matched products)
    2. Non-common fields (from the specific product)
    
    Args:
        siid: The product siid to build the file for
        
    Returns:
        Complete product file dictionary
    """
    try:
        # Step 1: Get the reference product from MongoDB by siid
        reference_product = None
        db = get_mongo_client()
        if db is None:
            logger.error("Failed to connect to MongoDB")
            return {"error": "Database connection failed"}
        
        collections = db.list_collection_names()
        for coll_name in collections:
            coll = db[coll_name]
            doc = coll.find_one({"siid": siid})
            if doc:
                reference_product = doc
                break
        
        if not reference_product:
            logger.error(f"Product with siid {siid} not found in MongoDB")
            return {"error": f"Product with siid {siid} not found"}
        
        # Step 2: Get all matched product siids from PostgreSQL
        matched_siids = get_matched_product_siids(siid)
        
        # Step 3: Get all matched products from MongoDB
        matched_products = get_products_from_mongodb_by_siids(matched_siids)
        
        if not matched_products:
            logger.warning(f"No matched products found for {siid}, returning reference product")
            return reference_product
        
        # Step 4: Merge common fields
        common_data = merge_common_fields(matched_products, siid)
        
        # Step 5: Create product file with common and non-common sections
        product_file = {}
        
        # Add common fields (información común entre todos los productos)
        product_file.update(common_data)
        
        # Add reference siid
        product_file['siid'] = siid
        
        # Add product_hash if available
        product_hash = reference_product.get('product_hash') or reference_product.get('Product_Hash')
        if product_hash:
            product_file['product_hash'] = product_hash
        
        # Step 6: Add non-common fields for each matched product
        # Para cada producto matcheado, crear una entrada con su store y atributos únicos
        matched_products_info = []
        
        for product in matched_products:
            product_info = {
                'siid': product.get('siid'),
                'store': product.get('store'),
            }
            
            # Add all non-common fields for this product
            for field, value in product.items():
                # Skip internal MongoDB fields
                if field.startswith('_'):
                    continue
                
                # Skip common fields (ya están en la sección común)
                is_common = (
                    field.lower() in COMMON_FIELDS or 
                    is_nutritional_field(field)
                )
                
                # Skip siid and store (ya los agregamos arriba)
                if field in ['siid', 'store']:
                    continue
                
                if not is_common:
                    product_info[field] = value
            
            matched_products_info.append(product_info)
        
        # Add the matched products section to the product file
        product_file['matched_products'] = matched_products_info
        product_file['matched_products_count'] = len(matched_products)
        
        # Clean null values from the entire product file
        product_file = clean_null_values(product_file)
        
        logger.info(f"Successfully built product file for siid {siid} with {len(matched_products)} matched products")
        return product_file
        
    except Exception as e:
        logger.error(f"Error building product file for siid {siid}: {e}", exc_info=True)
        return {"error": str(e)}


def get_product_file_by_siid(siid: str, format_with_llm: bool = False, lang: str = "es") -> Dict[str, Any]:
    """
    Main function to get a complete product file by siid.
    This is the function that should be called from the API endpoint.
    
    Args:
        siid: Product siid identifier
        format_with_llm: If True, format the product file using LLM for better readability
        
    Returns:
        Complete product file with merged common fields and specific non-common fields.
        If format_with_llm=True, returns both formatted_summary and raw_data.
    """
    product_file = build_product_file(siid)
    
    # Check if there was an error building the product file
    if "error" in product_file:
        return product_file
    
    # If LLM formatting is requested, format the product file
    if format_with_llm:
        return format_product_file_with_llm(product_file, lang=lang)
    
    return product_file

