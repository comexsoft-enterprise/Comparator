'''
Función para calcular distancias euclidianas entre productos basándose en características numéricas.
OPTIMIZED: Uses batched DB fetching and NumPy vectorization for performance.

Uso:
    from euclidean_distance_calculator import calculate_distances_for_product:
    
    results = calculate_distances_for_product(
        product_to_match_siid='eroski_01013-01-26794016',
        products_founded_siids=['makro_01013-01-189736', 'makro_01013-01-5984']
    )
'''

import os
import logging
import sys
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Añadir el directorio raíz al path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.connectors.postgresql_connector import get_pooled_connection, return_pooled_connection


# Cache global para evitar consultar information_schema repetidamente
_CACHED_NUMERIC_COLS = None
_CACHED_SELECT_QUERY_PART = None

def get_db_connection():
    """Establece conexión con PostgreSQL usando connection pool."""
    return get_pooled_connection()


def get_numeric_columns(conn) -> List[str]:
    """Obtiene las columnas numéricas de la tabla product_vector_data (Cached)."""
    global _CACHED_NUMERIC_COLS
    
    if _CACHED_NUMERIC_COLS is not None:
        return _CACHED_NUMERIC_COLS
        
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'product_vector_data'
        ORDER BY ordinal_position;
    """)
    
    columns = cur.fetchall()
    numeric_cols = []
    
    # Tipos numéricos de PostgreSQL
    numeric_types = ['integer', 'bigint', 'smallint', 'numeric', 'real', 'double precision', 'decimal']
    
    # Filtrar solo columnas numéricas, excluyendo identificadores, VAT y columnas de histórico
    for col_name, col_type in columns:
        if col_name in ['id', 'uuid', 'product_hash', 'siid', 'created_at', 'vat']:
            continue
        # Exclude any explicit history columns like price_history or columns ending with _history
        if col_name.lower().endswith('_history') or col_name.lower() == 'price_history':
            continue
        elif col_type in numeric_types:
            numeric_cols.append(col_name)
    
    cur.close()
    
    # Guardar en cache
    _CACHED_NUMERIC_COLS = numeric_cols
    logging.info(f"✅ Euclidean Calculator: Cached {len(numeric_cols)} numeric columns.")
    return numeric_cols


def get_products_data_batch_raw(conn, siids: List[str], cols: List[str]) -> Dict[str, Tuple]:
    """
    Fetch specific numeric columns for multiple SIIDs efficiently using a single query.
    Returns raw tuples instead of dicts for performance.
    """
    if not siids or not cols:
        return {}
    
    global _CACHED_SELECT_QUERY_PART
    
    # Cache the SELECT part of the query string to avoid string manipulation overhead
    if _CACHED_SELECT_QUERY_PART is None:
        cols_quoted = [f'"{c}"' for c in cols]
        _CACHED_SELECT_QUERY_PART = ", ".join(cols_quoted)
    
    query = f'SELECT siid, {_CACHED_SELECT_QUERY_PART} FROM product_vector_data WHERE siid = ANY(%s)'
    
    cur = conn.cursor()
    try:
        # Use set to avoid duplicate SIIDs in query
        cur.execute(query, (list(set(siids)),))
        rows = cur.fetchall()
        
        # Return dict of siid -> raw_values_tuple (skipping siid at index 0 in values)
        # This keeps the order consistent with 'cols'
        return {row[0]: row[1:] for row in rows}
            
    except Exception as e:
        logging.error(f"Error fetching batch data: {e}")
        return {}
    finally:
        cur.close()


def calculate_distances_for_product(product_to_match_siid: str, products_founded_siids: List[str]) -> List[Dict]:
    """
    Calcula las distancias euclidianas entre un producto y una lista de productos candidatos.
    OPTIMIZED: Uses batch fetching, caching, and direct tuple-to-numpy conversion.
    
    Args:
        product_to_match_siid: SIID del producto de referencia
        products_founded_siids: Lista de SIIDs de productos candidatos
        
    Returns:
        Lista de diccionarios con resultados ordenados por distancia.
    """
    conn = None
    try:
        conn = get_db_connection()
        
        # 1. Start setup (Cached)
        numeric_cols = get_numeric_columns(conn)
        if not numeric_cols:
            logging.warning("No numeric columns found for distance calculation.")
            return []
            
        all_to_fetch = [product_to_match_siid] + products_founded_siids
        
        # 2. Batch fetch raw data (Tuples, no internal dicts)
        products_data_raw = get_products_data_batch_raw(conn, all_to_fetch, numeric_cols)
        
        # 3. Get vector for Product A
        product_to_match_values = products_data_raw.get(product_to_match_siid)
        if product_to_match_values is None:
            return []
            
        # Helper NO LONGER NEEDED inside loop if we trust basic types, but kept for None handling
        # Using a list comprehension is faster than map with lambda
        def clean_val(x):
            if x is None: return np.nan
            try: return float(x)
            except: return np.nan

        # Create Vector A (original) directly from tuple
        vec_a_list = [clean_val(x) for x in product_to_match_values]
        vec_a = np.array(vec_a_list, dtype=np.float64)
        
        # 4. Create Matrix B (candidates)
        valid_candidates = [s for s in products_founded_siids if s in products_data_raw]
        if not valid_candidates:
            return []
            
        # Build matrix directly from raw tuples
        matrix_b_list = []
        for siid in valid_candidates:
            # Much faster: direct tuple access, same order as cols
            raw_vals = products_data_raw[siid]
            vector = [clean_val(x) for x in raw_vals]
            matrix_b_list.append(vector)
            
        mat_b = np.array(matrix_b_list, dtype=np.float64) # Shape: (Num_Candidates, Num_Cols)
        
        
        # 5. Vectorized Calculation
        # vec_a shape: (Num_Cols,)
        # mat_b shape: (Num_Candidates, Num_Cols)
        
        # Mask: Valid where both A and B are not NaN
        # Broadcasting vec_a to match mat_b rows
        mask = ~np.isnan(vec_a) & ~np.isnan(mat_b)
        
        # Relative Squared Difference: ((a - b) / (a + epsilon))^2
        epsilon = 0.000001
        
        # Prepare A for division (prevent div by zero)
        denom = vec_a + epsilon 
        
        # Operations (Broadcasting)
        diff = (vec_a - mat_b) / denom
        sq_diff = diff ** 2
        
        # Apply mask: Set invalid entries to 0 to ignore them in sum
        sq_diff[~mask] = 0.0
        
        # Sum of squared diffs per candidate (axis 1 = columns)
        sum_sq_diff = np.sum(sq_diff, axis=1)
        
        # Count columns used per candidate
        cols_used = np.sum(mask, axis=1)
        
        # Euclidean Dist = sqrt(sum)
        distances = np.sqrt(sum_sq_diff)
        
        # 6. Format results
        results = []
        for i, siid in enumerate(valid_candidates):
            results.append({
                'product_to_match_siid': product_to_match_siid,
                'product_founded_siid': siid,
                'distance': float(distances[i]),
                'columns_used': int(cols_used[i])
            })
        
        # Ordenar por distancia (menor distancia = más similar)
        results.sort(key=lambda x: x['distance'])
        return results
        
    except Exception as e:
        logging.error(f"❌ Error calculating distances for {product_to_match_siid}: {e}")
        return []
    finally:
        if conn is not None:
            return_pooled_connection(conn)


def process_product_euclidean(product_a_id, product_a, similar_products_b, min_euclidean_similarity=0.5):
    """Calculate euclidean distances for one product."""
    try:
        # Get SIID for product A
        siid_a = product_a.get('siid')
        
        if not siid_a:
            logging.warning(f"⚠️ Product {product_a_id} has no SIID, skipping euclidean calculation")
            # Set euclidean similarity to 0 for all matches
            for product_b in similar_products_b:
                product_b['euclidean_similarity'] = 0.0
            return similar_products_b
        
        # Get all SIIDs from similar products B
        siids_b = [p.get('siid') for p in similar_products_b if p.get('siid')]
        
        if not siids_b:
            logging.warning(f"⚠️ No valid SIIDs found for product {product_a_id} matches")
            # Set euclidean similarity to 0
            for product_b in similar_products_b:
                product_b['euclidean_similarity'] = 0.0
            return similar_products_b
        
        # Calculate euclidean distances using PostgreSQL
        euclidean_results = calculate_distances_for_product(siid_a, siids_b)
        
        # Create mapping: siid_b -> normalized_similarity
        euclidean_map = {}
        if euclidean_results:
            # Normalize distances to 0-1 similarity (lower distance = higher similarity)
            distances = [r['distance'] for r in euclidean_results if r.get('distance') is not None]
            
            if distances:
                max_dist = 10
                min_dist =0
                
                # Normalize: similarity = 1 - (distance - min) / (max - min)
                # This gives 1.0 for minimum distance, 0.0 for maximum distance
                for result in euclidean_results:
                    siid_b = result['product_founded_siid']
                    dist = result.get('distance')
                    
                    if dist is not None and max_dist > min_dist:
                        normalized_sim = 1.0 - ((dist - min_dist) / (max_dist - min_dist))
                    elif dist is not None:
                        normalized_sim = 1.0  # All distances are the same
                    else:
                        normalized_sim = 0.0
                    
                    euclidean_map[siid_b] = normalized_sim
        
        # Add euclidean similarity to each product
        for product_b in similar_products_b:
            siid_b = product_b.get('siid')
            euclidean_sim = euclidean_map.get(siid_b, 0.0)
            product_b['euclidean_similarity'] = float(euclidean_sim)
        
        # Filter products: keep only those with euclidean_similarity >= threshold
        similar_products_b = [
            prod for prod in similar_products_b
            if prod.get('euclidean_similarity', 0.0) >= min_euclidean_similarity
        ]
        
        return similar_products_b
        
    except Exception as e:
        logging.error(f"❌ Error calculating euclidean for product {product_a_id}: {e}")
        # Fallback: set euclidean to 0
        for product_b in similar_products_b:
            product_b['euclidean_similarity'] = 0.0
        return similar_products_b



if __name__ == "__main__":
    # Ejemplo de uso
    product_to_match = 'eroski_01013-01-10125193'
    candidates = [
        'makro_01013-01-214196',
        'makro_01013-01-573196',
        'makro_01013-01-573196',
        'makro_01013-01-117686'
    ]
    
    print(f"🔍 Calculando distancias para: {product_to_match}")
    print(f"📋 Candidatos: {len(candidates)}\n")
    
    results = calculate_distances_for_product(
        product_to_match_siid=product_to_match,
        products_founded_siids=candidates
    )
    
    if results:
        print("="*80)
        print("📊 RESULTADOS (ordenados por distancia)")
        print("="*80)
        for i, result in enumerate(results, 1):
            print(f"\n{i}. {result['product_founded_siid']}")
            print(f"   Distancia: {result['distance']:.6f}")
            print(f"   Columnas usadas: {result['columns_used']}")
        print(f"\n{'='*80}")
        print(f"✅ Total: {len(results)} productos procesados")
    else:
        print("❌ No se encontraron resultados")
