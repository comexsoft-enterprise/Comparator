"""
Script para verificar y crear índices en Neo4j para optimizar queries de similaridad
"""

import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.connectors.neo4j_connector import Neo4jConnector
from config.settings import NEO4J_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def check_indices():
    """Verificar índices existentes"""
    connector = Neo4jConnector()
    driver = connector.get_neo4j_driver()
    
    with driver.session() as session:
        result = session.run("SHOW INDEXES")
        indices = list(result)
        
        print("\n" + "="*70)
        print("ÍNDICES EXISTENTES EN NEO4J")
        print("="*70)
        
        if not indices:
            print("❌ NO HAY ÍNDICES CREADOS")
        else:
            for idx in indices:
                print(f"✅ {idx['name']}: {idx['labelsOrTypes']} - {idx['properties']}")
        
        print("="*70 + "\n")
    
    connector.close_connection()
    return len(indices)

def create_indices():
    """Crear índices necesarios para optimización"""
    connector = Neo4jConnector()
    driver = connector.get_neo4j_driver()
    
    indices_to_create = [
        # Índices para Store
        "CREATE INDEX store_name_idx IF NOT EXISTS FOR (s:Store) ON (s.name)",
        
        # Índices para Product
        "CREATE INDEX product_siid_idx IF NOT EXISTS FOR (p:Product) ON (p.siid)",
        "CREATE INDEX product_hash_idx IF NOT EXISTS FOR (p:Product) ON (p.product_hash)",
        "CREATE INDEX product_name_idx IF NOT EXISTS FOR (p:Product) ON (p.product_name)",
        
        # Índices para SELLS relationship
        "CREATE INDEX sells_id_idx IF NOT EXISTS FOR ()-[r:SELLS]-() ON (r.id)",
        
        # Índices para taxonomía
        "CREATE INDEX internal_type_name_idx IF NOT EXISTS FOR (t:Internal_Type) ON (t.name)",
        "CREATE INDEX internal_category_name_idx IF NOT EXISTS FOR (c:Internal_Category) ON (c.name)",
        "CREATE INDEX internal_subcategory_name_idx IF NOT EXISTS FOR (s:Internal_Subcategory) ON (s.name)",
        
        # Índices para atributos
        "CREATE INDEX brand_name_idx IF NOT EXISTS FOR (b:Brand) ON (b.name)",
        "CREATE INDEX format_name_idx IF NOT EXISTS FOR (f:Format) ON (f.name)",
        "CREATE INDEX ingredient_name_idx IF NOT EXISTS FOR (i:Ingredient) ON (i.name)",
        "CREATE INDEX quantity_name_idx IF NOT EXISTS FOR (q:Quantity) ON (q.name)",
        "CREATE INDEX country_name_idx IF NOT EXISTS FOR (c:Country) ON (c.name)",
    ]
    
    print("\n" + "="*70)
    print("CREANDO ÍNDICES EN NEO4J")
    print("="*70)
    
    with driver.session() as session:
        for idx, query in enumerate(indices_to_create, 1):
            try:
                session.run(query)
                print(f"✅ [{idx}/{len(indices_to_create)}] {query[:60]}...")
            except Exception as e:
                print(f"⚠️  [{idx}/{len(indices_to_create)}] Error: {e}")
    
    print("="*70)
    print("✅ PROCESO COMPLETADO")
    print("="*70 + "\n")
    
    connector.close_connection()

def analyze_query_performance():
    """Analizar performance de un query de ejemplo"""
    connector = Neo4jConnector()
    driver = connector.get_neo4j_driver()
    
    print("\n" + "="*70)
    print("ANALIZANDO PERFORMANCE DE QUERY")
    print("="*70)
    
    # Query de prueba
    test_query = """
    MATCH (s:Store {name: 'eroski-01013-01'})-[r:SELLS]->(p:Product)
    RETURN count(p) as total_products
    """
    
    with driver.session() as session:
        import time
        start = time.time()
        result = session.run(test_query)
        data = result.single()
        elapsed = time.time() - start
        
        print(f"\n📊 Productos en eroski-01013-01: {data['total_products']}")
        print(f"⏱️  Tiempo de query: {elapsed:.3f}s")
        
        # Test con filtro de tipo
        test_query_type = """
        MATCH (s:Store {name: 'eroski-01013-01'})-[r:SELLS]->(p:Product)
              -[:COVERS]->(:Internal_Subcategory)
              -[:HAS_INTERNAL_SUBCATEGORY]->(:Internal_Category)
              -[:HAS_INTERNAL_CATEGORY]->(t:Internal_Type)
        RETURN t.name as type_name, count(p) as count
        ORDER BY count DESC
        """
        
        start = time.time()
        result = session.run(test_query_type)
        types = list(result)
        elapsed = time.time() - start
        
        print(f"\n📊 Tipos de productos:")
        for t in types[:5]:
            print(f"   {t['type_name']}: {t['count']} productos")
        print(f"⏱️  Tiempo de query: {elapsed:.3f}s")
    
    print("="*70 + "\n")
    
    connector.close_connection()

def main():
    print("\n🔍 NEO4J OPTIMIZATION TOOL")
    print("="*70)
    
    # 1. Check existing indices
    num_indices = check_indices()
    
    if num_indices < 5:
        print("⚠️  POCOS ÍNDICES DETECTADOS - Creando índices necesarios...")
        create_indices()
        print("\n✅ Índices creados. Verificando nuevamente...")
        check_indices()
    else:
        print("✅ Indices suficientes detectados")
    
    # 2. Analyze query performance
    analyze_query_performance()
    
    print("\n💡 RECOMENDACIONES:")
    print("   1. Si el query sigue lento, verifica que el filtro de Internal_Type esté activo")
    print("   2. Considera usar LIMIT más bajo (50 en lugar de 100)")
    print("   3. Aumenta la memoria de Neo4j si es posible (dbms.memory.heap)")
    print("   4. Ejecuta: CALL db.clearQueryCaches() para limpiar cachés")
    print()

if __name__ == "__main__":
    main()
