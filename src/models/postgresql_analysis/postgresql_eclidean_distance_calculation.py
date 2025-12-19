'''
Este script, permite calcular las distancias euclídeas entre los productos que vienen desde el documento
del modelo 'get_similar_neo4j.py' y poder saber cual de los productos es el más parecido al original,
teniendo en cuenta las características numéricas, como peso, información nutricional y precios.
Para poder hacer todo esto, hay que seguir los siguientes pasos:
1. Coger los SIID o los UUID de los productos que vienen desde el modelo 'get_similar_neo4j.py'.
2. Hacer una consulta a PostgreSQL para obtener los vectores numéricos de esos productos.
3. Calcular cada distancia euclídea entre el producto original y los productos similares.
4. Devolver los resultados ordenados por distancia euclídea, la menor distancia primero (ya que es 
la más parecida).

A tener en cuenta: la tabla de la base de datos que se utiliza es 'product_vector_data'. Esta tabla tiene las siguientes columnas:
- uuid: Identificador único del producto.
- siid: Identificador del producto en la tienda.
- measure_value: Valor de la medida (peso, volumen, etc.).
- columnas de información nutricional (calories, fat, carbohydrates, protein, etc.).
- columnas de precios (price_with_vat, price_without_vat, etc.).

Muchas de estas columnas no están presentes en todos los productos, por lo que hay que tener en cuenta esto
a la hora de calcular las distancias euclídeas.

Uso:
    python postgresql_eclidean_distance_calculation.py --csv <archivo.csv>
    python postgresql_eclidean_distance_calculation.py  (sin CSV usa datos de prueba)
    
El CSV debe tener cuatro columnas:
    - Product_to_match_uuid: UUID del producto que se quiere matchear
    - Product_to_match_siid: SIID del producto que se quiere matchear
    - Product_founded_uuid: UUID del producto encontrado como posible match
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
TEST_DATA_CSV = os.path.join(os.path.dirname(__file__), 'datos_prueba.csv')



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


def get_product_by_identifier(conn, uuid: Optional[str] = None, siid: Optional[str] = None, strict: bool = True) -> Tuple[Optional[Dict], str]:
    """
    Busca un producto por UUID y SIID.
    Si strict=True, busca por UUID y SIID simultáneamente.
    Si no encuentra con ambos, intenta solo con UUID y retorna advertencia.
    
    Retorna una tupla (producto_dict, mensaje_advertencia).
    """
    cur = conn.cursor()
    warning = ""
    
    if uuid and siid and strict:
        # Intentar primero con UUID y SIID (convertir UUID a string para comparación)
        cur.execute("SELECT * FROM product_vector_data WHERE CAST(uuid AS TEXT) = %s AND siid = %s LIMIT 1;", (str(uuid), siid))
        row = cur.fetchone()
        
        if not row:
            # Intentar solo con UUID
            cur.execute("SELECT * FROM product_vector_data WHERE CAST(uuid AS TEXT) = %s LIMIT 1;", (str(uuid),))
            row = cur.fetchone()
            if row:
                warning = f"⚠️  Producto encontrado solo por UUID (UUID={uuid}). SIID no coincide: esperado={siid}"
    elif uuid:
        cur.execute("SELECT * FROM product_vector_data WHERE CAST(uuid AS TEXT) = %s LIMIT 1;", (str(uuid),))
        row = cur.fetchone()
    elif siid:
        cur.execute("SELECT * FROM product_vector_data WHERE siid = %s LIMIT 1;", (siid,))
        row = cur.fetchone()
    else:
        cur.close()
        return None, ""
    
    if not row:
        cur.close()
        return None, ""
    
    # Obtener nombres de columnas
    col_names = [desc[0] for desc in cur.description]
    product = dict(zip(col_names, row))
    
    cur.close()
    return product, warning


def get_products_by_identifiers(conn, identifiers: List[Dict[str, str]]) -> List[Dict]:
    """
    Obtiene múltiples productos basándose en una lista de identificadores.
    identifiers: Lista de diccionarios con 'uuid' o 'siid'
    """
    products = []
    
    for identifier in identifiers:
        uuid = identifier.get('uuid')
        siid = identifier.get('siid')
        
        product = get_product_by_identifier(conn, uuid=uuid, siid=siid)
        if product:
            products.append(product)
        else:
            id_str = f"UUID={uuid}" if uuid else f"SIID={siid}"
            print(f"⚠️  Producto no encontrado: {id_str}")
    
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


def read_csv_input(csv_path: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """
    Lee un archivo CSV con columnas:
    - Product_to_match_uuid (o Product_A_ID)
    - Product_to_match_siid (o Product_A_SIID)
    - Product_founded_uuid (o Product_B_ID)
    - Product_founded_siid (o Product_B_SIID)
    
    Retorna una tupla con:
    - Lista de diccionarios con los pares válidos (ambos productos completos)
    - Lista de diccionarios con productos sin match (product_founded vacío)
    """
    pairs = []
    products_without_match = []
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            for idx, row in enumerate(reader, 1):
                # Soportar ambos formatos de nombres de columnas
                uuid_to_match = (row.get('Product_to_match_uuid') or row.get('Product_A_ID', '')).strip()
                siid_to_match = (row.get('Product_to_match_siid') or row.get('Product_A_SIID', '')).strip()
                uuid_founded = (row.get('Product_founded_uuid') or row.get('Product_B_ID', '')).strip()
                siid_founded = (row.get('Product_founded_siid') or row.get('Product_B_SIID', '')).strip()
                
                # Verificar que el producto a matchear tenga datos
                if not uuid_to_match or not siid_to_match:
                    if idx <= 5:
                        print(f"⚠️  Fila {idx} ignorada - Product_to_match incompleto")
                    continue
                
                # Si no hay producto founded, significa que el modelo no encontró match
                if not uuid_founded or not siid_founded:
                    products_without_match.append({
                        'uuid': uuid_to_match,
                        'siid': siid_to_match
                    })
                    continue
                
                # Agregar par válido
                pairs.append({
                    'to_match_uuid': uuid_to_match,
                    'to_match_siid': siid_to_match,
                    'founded_uuid': uuid_founded,
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
        description='Calcula distancias euclidianas entre pares de productos basándose en características numéricas.'
    )
    parser.add_argument('--csv', type=str, help='Archivo CSV con pares de productos (columnas: Product_to_match_uuid, Product_to_match_siid, Product_founded_uuid, Product_founded_siid)')

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
    warnings = []

    for pair in pairs:
        uuid_to_match = pair['to_match_uuid']
        siid_to_match = pair['to_match_siid']
        uuid_founded = pair['founded_uuid']
        siid_founded = pair['founded_siid']

        # Obtener producto a matchear
        product_to_match, warning_to_match = get_product_by_identifier(
            conn, uuid=uuid_to_match, siid=siid_to_match, strict=True
        )
        if not product_to_match:
            products_not_found.append(f"{uuid_to_match} (SIID: {siid_to_match})")
            print(f"⚠️  Producto no encontrado (Product_to_match): UUID={uuid_to_match}, SIID={siid_to_match}")
            continue

        if warning_to_match:
            warnings.append(warning_to_match)
            print(warning_to_match)

        # Obtener producto encontrado
        product_founded, warning_founded = get_product_by_identifier(
            conn, uuid=uuid_founded, siid=siid_founded, strict=True
        )
        if not product_founded:
            products_not_found.append(f"{uuid_founded} (SIID: {siid_founded})")
            print(f"⚠️  Producto no encontrado (Product_founded): UUID={uuid_founded}, SIID={siid_founded}")
            continue

        if warning_founded:
            warnings.append(warning_founded)
            print(warning_founded)

        # Calcular distancia
        distance, cols_used = calculate_euclidean_distance(product_to_match, product_founded, numeric_cols)

        results.append({
            'product_to_match_uuid': uuid_to_match,
            'product_to_match_siid': product_to_match['siid'],
            'product_founded_uuid': uuid_founded,
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
        grouped_results[result['product_to_match_uuid']].append(result)
    for uuid in grouped_results:
        grouped_results[uuid].sort(key=lambda x: x['distance'])

    # Guardar resultados en un CSV
    output_csv = os.path.join(os.path.dirname(__file__), 'resultados_distancias.csv')
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            'product_to_match_uuid', 'product_to_match_siid',
            'product_founded_uuid', 'product_founded_siid',
            'distance', 'columns_used', 'is_best_match'
        ])
        for product_to_match_uuid, matches in grouped_results.items():
            for i, match in enumerate(matches, 1):
                is_best = 1 if i == 1 else 0
                writer.writerow([
                    match['product_to_match_uuid'],
                    match['product_to_match_siid'],
                    match['product_founded_uuid'],
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
    if warnings:
        print(f"Advertencias (productos encontrados solo por UUID): {len(warnings)}")

    # Mostrar productos sin match
    if products_without_match:
        print(f"\n⚠️  Productos sin match encontrado por el modelo: {len(products_without_match)}")
        print("="*80)

        # Obtener productos únicos
        unique_no_match = {}
        for prod in products_without_match:
            key = f"{prod['uuid']}|{prod['siid']}"
            unique_no_match[key] = prod

        print(f"\nProductos únicos sin match: {len(unique_no_match)}\n")

        for idx, (key, prod) in enumerate(unique_no_match.items(), 1):
            print(f"   {idx}. UUID: {prod['uuid']}, SIID: {prod['siid']}")
            if idx >= 20:  # Mostrar solo los primeros 20
                remaining = len(unique_no_match) - 20
                if remaining > 0:
                    print(f"\n   ... y {remaining} productos más sin match")
                break

    print()

    conn.close()
    print("✅ Proceso completado.")

