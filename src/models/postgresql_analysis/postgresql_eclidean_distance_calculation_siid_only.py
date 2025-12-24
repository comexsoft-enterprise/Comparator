'''
Este script, permite calcular las distancias euclídeas entre los productos que vienen desde el documento
del modelo 'get_similar_neo4j.py' y poder saber cual de los productos es el más parecido al original,
teniendo en cuenta las características numéricas, como peso, información nutricional y precios.
Para poder hacer todo esto, hay que seguir los siguientes pasos:
1. Coger los SIID de los productos que vienen desde el modelo 'get_similar_neo4j.py'.
2. Hacer una consulta a PostgreSQL para obtener los vectores numéricos de esos productos.
3. Calcular cada distancia euclídea entre el producto original y los productos similares.
4. Devolver los resultados ordenados por distancia euclídea, la menor distancia primero (ya que es 
la más parecida).

A tener en cuenta: la tabla de la base de datos que se utiliza es 'product_vector_data'. Esta tabla tiene las siguientes columnas:
- siid: Identificador del producto en la tienda.
- measure_value: Valor de la medida (peso, volumen, etc.).
- columnas de información nutricional (calories, fat, carbohydrates, protein, etc.).
- columnas de precios (price_with_vat, price_without_vat, etc.).

Muchas de estas columnas no están presentes en todos los productos, por lo que hay que tener en cuenta esto
a la hora de calcular las distancias euclídeas.

Uso:
    python postgresql_eclidean_distance_calculation_siid_only.py --csv <archivo.csv>
    python postgresql_eclidean_distance_calculation_siid_only.py  (sin CSV usa datos de prueba)
    
El CSV debe tener dos columnas:
    - Product_to_match_siid: SIID del producto que se quiere matchear
    - Product_founded_siid: SIID del producto encontrado como posible match
    
Si no se proporciona archivo CSV, se utilizan datos de prueba.
'''

import os
import sys
import csv
import argparse
import numpy as np
from typing import Dict, List, Tuple, Optional

# Importar el conector centralizado
from src.connectors.postgresql_connector import get_postgresql_connection

# Ruta al archivo CSV de datos de prueba
TEST_DATA_CSV = os.path.join(os.path.dirname(__file__), 'datos_prueba_siid.csv')



# El conector centralizado ya maneja la conexión y los errores


def get_numeric_columns(conn) -> Tuple[List[str], List[str]]:
    """
    Obtiene las columnas numéricas de la tabla product_vector_data.
    Retorna una tupla con (columnas_numericas, columnas_no_numericas).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'product_vector_data'
        ORDER BY ordinal_position;
    """)
    
    columns = cur.fetchall()
    numeric_cols = []
    non_numeric_cols = []
    
    # Tipos numéricos en PostgreSQL
    numeric_types = ['integer', 'bigint', 'smallint', 'numeric', 'real', 'double precision', 'decimal']
    
    for col_name, col_type in columns:
        # Excluir identificadores de las métricas numéricas
        if col_name in ['id', 'uuid', 'product_hash', 'siid', 'created_at']:
            non_numeric_cols.append(col_name)
        elif col_type in numeric_types:
            numeric_cols.append(col_name)
        else:
            non_numeric_cols.append(col_name)
    
    cur.close()
    return numeric_cols, non_numeric_cols


def get_product_by_siid(conn, siid: str) -> Optional[Dict]:
    """
    Busca un producto por SIID.
    
    Retorna el producto como diccionario o None si no se encuentra.
    """
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM product_vector_data WHERE siid = %s LIMIT 1;", (siid,))
    row = cur.fetchone()
    
    if not row:
        cur.close()
        return None
    
    # Obtener nombres de columnas
    col_names = [desc[0] for desc in cur.description]
    product = dict(zip(col_names, row))
    
    cur.close()
    return product


def get_products_by_siids(conn, siids: List[str]) -> List[Dict]:
    """
    Obtiene múltiples productos basándose en una lista de SIIDs.
    """
    products = []
    
    for siid in siids:
        product = get_product_by_siid(conn, siid=siid)
        if product:
            products.append(product)
        else:
            print(f"⚠️  Producto no encontrado: SIID={siid}")
    
    return products


def calculate_euclidean_distance(original: Dict, similar: Dict, numeric_cols: List[str]) -> Tuple[float, int]:
    """
    Calcula la distancia euclidiana entre dos productos.
    Ignora columnas donde alguno de los productos tenga valor NULL.
    
    Fórmula de la distancia euclidiana:
    d(p, q) = √(Σ(pi - qi)²)
    
    Donde:
    - p y q son los vectores de características numéricas de dos productos
    - pi y qi son los valores de la característica i en cada producto
    - Σ es la suma sobre todas las características numéricas disponibles
    - Solo se consideran características donde ambos productos tienen valores (no NULL)
    
    Retorna (distancia, cantidad_de_columnas_usadas).
    """
    squared_diff_sum = 0.0
    cols_used = 0
    
    for col in numeric_cols:
        val_orig = original.get(col)
        val_sim = similar.get(col)
        
        # Ignorar si alguno es None
        if val_orig is None or val_sim is None:
            continue
        
        # Convertir a float
        try:
            val_orig = float(val_orig)
            val_sim = float(val_sim)
        except (ValueError, TypeError):
            continue
        
        squared_diff_sum += (val_orig - val_sim) ** 2
        cols_used += 1
    
    distance = np.sqrt(squared_diff_sum)
    return distance, cols_used


def read_csv_input(csv_path: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """
    Lee un archivo CSV con columnas:
    - Product_to_match_siid o Product_A_SIID
    - Product_founded_siid o Product_B_SIID
    
    Retorna una tupla con:
    - Lista de diccionarios con los pares válidos (ambos SIIDs presentes)
    - Lista de SIIDs sin match (product_founded vacío)
    """
    pairs = []
    products_without_match = []
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            for idx, row in enumerate(reader, 1):
                # Intentar ambos formatos de nombres de columnas
                siid_to_match = row.get('Product_to_match_siid', row.get('Product_A_SIID', '')).strip()
                siid_founded = row.get('Product_founded_siid', row.get('Product_B_SIID', '')).strip()
                
                # Verificar que el producto a matchear tenga SIID
                if not siid_to_match:
                    if idx <= 5:
                        print(f"⚠️  Fila {idx} ignorada - Product_to_match_siid vacío")
                    continue
                
                # Si no hay producto founded, significa que el modelo no encontró match
                if not siid_founded:
                    products_without_match.append(siid_to_match)
                    continue
                
                # Agregar par válido
                pairs.append({
                    'to_match_siid': siid_to_match,
                    'founded_siid': siid_founded
                })
    except Exception as e:
        print(f"❌ Error leyendo CSV: {e}")
        sys.exit(1)
    
    return pairs, products_without_match


def get_test_data(conn) -> Tuple[Dict, List[Dict]]:
    """
    Obtiene datos de prueba de la base de datos.
    Retorna (producto_original, productos_similares).
    """
    cur = conn.cursor()
    
    # Obtener un producto aleatorio como original
    cur.execute("SELECT * FROM product_vector_data ORDER BY RANDOM() LIMIT 1;")
    row = cur.fetchone()
    col_names = [desc[0] for desc in cur.description]
    original = dict(zip(col_names, row))
    
    # Obtener 5 productos aleatorios como similares (excluyendo el original)
    original_id = original['id']
    cur.execute("SELECT * FROM product_vector_data WHERE id != %s ORDER BY RANDOM() LIMIT 5;", (original_id,))
    rows = cur.fetchall()
    
    similar_products = []
    for row in rows:
        similar_products.append(dict(zip(col_names, row)))
    
    cur.close()
    return original, similar_products


    parser = argparse.ArgumentParser(
        description='Calcula distancias euclidianas entre pares de productos basándose en características numéricas (solo SIID).'
    )
    parser.add_argument('--csv', type=str, help='Archivo CSV con pares de productos (columnas: Product_to_match_siid, Product_founded_siid)')

    args = parser.parse_args()

    # Conectar a la base de datos usando el conector centralizado
    try:
        conn = get_postgresql_connection()
    except Exception as e:
        print(f"❌ Error conectando a PostgreSQL: {e}")
        sys.exit(1)

    # Obtener columnas numéricas
    print("🔍 Analizando columnas de la tabla product_vector_data...\n")
    numeric_cols, non_numeric_cols = get_numeric_columns(conn)

    print(f"📊 Columnas NUMÉRICAS utilizadas para el cálculo ({len(numeric_cols)}):")
    for col in numeric_cols:
        print(f"   - {col}")

    print(f"\n📋 Columnas NO NUMÉRICAS excluidas del cálculo ({len(non_numeric_cols)}):")
    for col in non_numeric_cols:
        print(f"   - {col}")

    print("\n" + "="*80 + "\n")

    # Determinar si usar datos de prueba o datos reales
    if not args.csv:
        print(f"⚙️  No se especificó CSV. Usando datos de prueba desde: {TEST_DATA_CSV}\n")

        if not os.path.exists(TEST_DATA_CSV):
            print(f"❌ Archivo de datos de prueba no encontrado: {TEST_DATA_CSV}")
            conn.close()
            sys.exit(1)

        pairs, products_without_match = read_csv_input(TEST_DATA_CSV)

        if not pairs and not products_without_match:
            print("❌ No se pudieron cargar los datos de prueba.")
            conn.close()
            sys.exit(1)

        print(f"✅ Cargados {len(pairs)} pares de prueba desde el archivo")
        if products_without_match:
            print(f"ℹ️  {len(products_without_match)} productos sin match encontrado (se reportarán al final)\n")
        else:
            print()
    else:
        print(f"📄 Leyendo pares de productos desde: {args.csv}\n")
        pairs, products_without_match = read_csv_input(args.csv)

        if not pairs and not products_without_match:
            print("❌ No se encontraron pares válidos en el CSV.")
            conn.close()
            sys.exit(1)

        print(f"✅ Leídos {len(pairs)} pares de productos")
        if products_without_match:
            print(f"ℹ️  {len(products_without_match)} productos sin match encontrado (se reportarán al final)\n")
        else:
            print()

    print(f"🔢 Calculando distancias euclidianas para {len(pairs)} pares...\n")

    # Calcular distancias para cada par
    results = []
    products_not_found = []

    for pair in pairs:
        siid_to_match = pair['to_match_siid']
        siid_founded = pair['founded_siid']

        # Obtener producto a matchear
        product_to_match = get_product_by_siid(conn, siid=siid_to_match)
        if not product_to_match:
            products_not_found.append(siid_to_match)
            print(f"⚠️  Producto no encontrado (Product_to_match): SIID={siid_to_match}")
            continue

        # Obtener producto encontrado
        product_founded = get_product_by_siid(conn, siid=siid_founded)
        if not product_founded:
            products_not_found.append(siid_founded)
            print(f"⚠️  Producto no encontrado (Product_founded): SIID={siid_founded}")
            continue

        # Calcular distancia
        distance, cols_used = calculate_euclidean_distance(product_to_match, product_founded, numeric_cols)

        results.append({
            'product_to_match_siid': product_to_match['siid'],
            'product_founded_siid': product_founded['siid'],
            'distance': distance,
            'columns_used': cols_used
        })

    if not results:
        print("❌ No se pudieron calcular distancias para ningún par de productos.")
        conn.close()
        sys.exit(1)

    # Agrupar resultados por producto a matchear
    from collections import defaultdict
    grouped_results = defaultdict(list)
    for result in results:
        grouped_results[result['product_to_match_siid']].append(result)
    for siid in grouped_results:
        grouped_results[siid].sort(key=lambda x: x['distance'])

    # Guardar resultados en un CSV
    output_csv = os.path.join(os.path.dirname(__file__), 'resultados_distancias_siid.csv')
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            'product_to_match_siid',
            'product_founded_siid',
            'distance', 'columns_used', 'is_best_match'
        ])
        for product_to_match_siid, matches in grouped_results.items():
            for i, match in enumerate(matches, 1):
                is_best = 1 if i == 1 else 0
                writer.writerow([
                    match['product_to_match_siid'],
                    match['product_founded_siid'],
                    f"{match['distance']:.6f}",
                    match['columns_used'],
                    is_best
                ])
    print(f"\n✅ Resultados de distancias euclídeas guardados en: {output_csv}\n")

    # Resumen final
    print("="*80)
    print("📈 RESUMEN")
    print("="*80)
    print(f"Total de productos únicos a matchear: {len(grouped_results)}")
    print(f"Total de pares procesados: {len(results)}")
    if products_not_found:
        print(f"Productos no encontrados en BD: {len(set(products_not_found))}")

    # Mostrar productos sin match
    if products_without_match:
        print(f"\n⚠️  Productos sin match encontrado por el modelo: {len(products_without_match)}")
        print("="*80)

        # Obtener productos únicos
        unique_no_match = list(set(products_without_match))

        print(f"\nProductos únicos sin match: {len(unique_no_match)}\n")

        for idx, siid in enumerate(unique_no_match, 1):
            print(f"   {idx}. SIID: {siid}")
            if idx >= 20:  # Mostrar solo los primeros 20
                remaining = len(unique_no_match) - 20
                if remaining > 0:
                    print(f"\n   ... y {remaining} productos más sin match")
                break

    print()

    conn.close()
    print("✅ Proceso completado.")
