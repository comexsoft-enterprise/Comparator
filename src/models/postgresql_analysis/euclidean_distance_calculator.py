'''
Función para calcular distancias euclidianas entre productos basándose en características numéricas.

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


def get_db_connection():
    """Establece conexión con PostgreSQL usando connection pool."""
    return get_pooled_connection()


def get_numeric_columns(conn) -> List[str]:
    """Obtiene las columnas numéricas de la tabla product_vector_data."""
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
    return numeric_cols


def get_product_by_siid(conn, siid: str) -> Optional[Dict]:
    """Busca un producto por SIID."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_vector_data WHERE siid = %s LIMIT 1;", (siid,))
    row = cur.fetchone()
    
    if not row:
        cur.close()
        return None
    
    col_names = [desc[0] for desc in cur.description]
    product = dict(zip(col_names, row))
    cur.close()
    return product


def calculate_euclidean_distance(original: Dict, similar: Dict, numeric_cols: List[str]) -> Tuple[float, int]:
    """Calcula la distancia euclidiana entre dos productos."""
    squared_diff_sum = 0.0
    cols_used = 0
    
    for col in numeric_cols:
        val_orig = original.get(col)
        val_sim = similar.get(col)
        
        # Ignorar columna si alguno de los productos no tiene valor
        if val_orig is None or val_sim is None:
            continue
        
        try:
            val_orig = float(val_orig)
            val_sim = float(val_sim)
        except (ValueError, TypeError):
            continue
        
        # Acumular suma de diferencias al cuadrado: Σ((pi - qi)/pi)²
        squared_diff_sum += ((val_orig+0.000001 - val_sim)/(val_orig+0.000001)) ** 2
        cols_used += 1
    
    # Calcular distancia euclidiana: d = n/√(Σ(pi - qi)²)
    distance = cols_used/(np.sqrt(squared_diff_sum)) 
    return distance, cols_used


def calculate_distances_for_product(product_to_match_siid: str, products_founded_siids: List[str]) -> List[Dict]:
    """
    Calcula las distancias euclidianas entre un producto y una lista de productos candidatos.
    
    Args:
        product_to_match_siid: SIID del producto de referencia
        products_founded_siids: Lista de SIIDs de productos candidatos
        
    Returns:
        Lista de diccionarios con resultados ordenados por distancia.
        Cada diccionario contiene:
            - product_to_match_siid
            - product_founded_siid
            - distance
            - columns_used
    """
    conn = None
    try:
        conn = get_db_connection()
        
        # Obtener columnas numéricas de la tabla
        numeric_cols = get_numeric_columns(conn)
        
        # Obtener producto de referencia
        product_to_match = get_product_by_siid(conn, siid=product_to_match_siid)
        if not product_to_match:
            return []
        
        results = []
        
        # Calcular distancia con cada candidato
        for siid_founded in products_founded_siids:
            product_founded = get_product_by_siid(conn, siid=siid_founded)
            if not product_founded:
                continue
            
            distance, cols_used = calculate_euclidean_distance(
                product_to_match, 
                product_founded, 
                numeric_cols
            )
            
            results.append({
                'product_to_match_siid': product_to_match_siid,
                'product_founded_siid': siid_founded,
                'distance': distance,
                'columns_used': cols_used
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
