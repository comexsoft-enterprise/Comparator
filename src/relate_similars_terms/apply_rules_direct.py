#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_rules_direct.py

Aplica directamente las reglas de rules_seed.json a Neo4j:
- Lee aliases del archivo
- Para cada alias, crea relación ALIAS_OF si ambos nodos existen
- Opcionalmente puede fusionar nodos (migrar relaciones y borrar el incorrecto)

Uso:
    python3 apply_rules_direct.py [--merge]
    
    --merge: Fusiona y borra nodos incorrectos (por defecto solo crea ALIAS_OF)
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.connectors.neo4j_connector import Neo4jConnector

# Config
PACKAGING_LABEL = "Ingredient"
NAME_PROP = "name"
RULES_PATH = Path(__file__).parent / "rules_seed.json"


def load_rules() -> Dict:
    """Carga rules_seed.json"""
    if not RULES_PATH.exists():
        print(f"❌ No existe {RULES_PATH}")
        sys.exit(1)
    
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    print(f"✓ Cargadas {len(data.get('aliases', {}))} reglas de aliases")
    print(f"✓ Términos canónicos: {len(data.get('canonical_terms', []))}")
    return data


def check_nodes_exist(connector: Neo4jConnector, wrong: str, correct: str) -> Tuple[bool, bool]:
    """Verifica si ambos nodos existen en Neo4j"""
    query = f"""
    OPTIONAL MATCH (w:`{PACKAGING_LABEL}` {{{NAME_PROP}: $wrong}})
    OPTIONAL MATCH (c:`{PACKAGING_LABEL}` {{{NAME_PROP}: $correct}})
    RETURN count(w) AS wrong_count, count(c) AS correct_count
    """
    result = connector.execute_query(query, {"wrong": wrong, "correct": correct})
    
    if not result:
        return False, False
    
    return result[0]["wrong_count"] > 0, result[0]["correct_count"] > 0


def create_alias_relation(connector: Neo4jConnector, wrong: str, correct: str) -> bool:
    """Crea relación ALIAS_OF entre dos nodos"""
    query = f"""
    MATCH (w:`{PACKAGING_LABEL}` {{{NAME_PROP}: $wrong}})
    MATCH (c:`{PACKAGING_LABEL}` {{{NAME_PROP}: $correct}})
    WHERE w <> c
    MERGE (w)-[:ALIAS_OF]->(c)
    RETURN count(*) AS created
    """
    result = connector.execute_query(query, {"wrong": wrong, "correct": correct})
    return result and result[0]["created"] > 0


def get_relationship_types(connector: Neo4jConnector, node_name: str) -> Tuple[List[str], List[str]]:
    """Obtiene tipos de relaciones salientes y entrantes de un nodo"""
    # Salientes
    q_out = f"""
    MATCH (w:`{PACKAGING_LABEL}` {{{NAME_PROP}: $n}})-[r]->()
    RETURN DISTINCT type(r) AS t
    """
    out_result = connector.execute_query(q_out, {"n": node_name})
    out_types = [rec["t"] for rec in out_result]
    
    # Entrantes
    q_in = f"""
    MATCH ()-[r]->(w:`{PACKAGING_LABEL}` {{{NAME_PROP}: $n}})
    RETURN DISTINCT type(r) AS t
    """
    in_result = connector.execute_query(q_in, {"n": node_name})
    in_types = [rec["t"] for rec in in_result]
    
    return out_types, in_types


def merge_and_delete_node(connector: Neo4jConnector, wrong: str, correct: str) -> bool:
    """
    Fusiona un nodo incorrecto con uno correcto:
    1. Migra todas las relaciones salientes
    2. Migra todas las relaciones entrantes
    3. Borra el nodo incorrecto
    """
    # Obtener tipos de relaciones
    out_types, in_types = get_relationship_types(connector, wrong)
    
    if not out_types and not in_types:
        print(f"  ⚠️  '{wrong}' no tiene relaciones, solo se borrará")
    
    # Migrar relaciones salientes
    for rel_type in out_types:
        query = f"""
        MATCH (w:`{PACKAGING_LABEL}` {{{NAME_PROP}: $wrong}})-[r:`{rel_type}`]->(target)
        MATCH (c:`{PACKAGING_LABEL}` {{{NAME_PROP}: $correct}})
        MERGE (c)-[r2:`{rel_type}`]->(target)
        SET r2 = properties(r)
        DELETE r
        RETURN count(*) AS migrated
        """
        result = connector.execute_query(query, {"wrong": wrong, "correct": correct})
        if result and result[0]["migrated"] > 0:
            print(f"    ↗️  Migradas {result[0]['migrated']} relaciones salientes de tipo {rel_type}")
    
    # Migrar relaciones entrantes
    for rel_type in in_types:
        query = f"""
        MATCH (source)-[r:`{rel_type}`]->(w:`{PACKAGING_LABEL}` {{{NAME_PROP}: $wrong}})
        MATCH (c:`{PACKAGING_LABEL}` {{{NAME_PROP}: $correct}})
        MERGE (source)-[r2:`{rel_type}`]->(c)
        SET r2 = properties(r)
        DELETE r
        RETURN count(*) AS migrated
        """
        result = connector.execute_query(query, {"wrong": wrong, "correct": correct})
        if result and result[0]["migrated"] > 0:
            print(f"    ↙️  Migradas {result[0]['migrated']} relaciones entrantes de tipo {rel_type}")
    
    # Borrar nodo incorrecto
    delete_query = f"""
    MATCH (w:`{PACKAGING_LABEL}` {{{NAME_PROP}: $wrong}})
    DETACH DELETE w
    RETURN count(*) AS deleted
    """
    result = connector.execute_query(delete_query, {"wrong": wrong})
    return result and result[0]["deleted"] > 0


def apply_rules_to_neo4j(merge_mode: bool = False):
    """
    Aplica todas las reglas del archivo rules_seed.json a Neo4j
    
    Args:
        merge_mode: Si True, fusiona y borra nodos. Si False, solo crea ALIAS_OF
    """
    print("=" * 60)
    print("📋 APLICACIÓN DIRECTA DE REGLAS A NEO4J")
    print("=" * 60)
    print(f"Modo: {'🔥 FUSIONAR Y BORRAR' if merge_mode else '🔗 CREAR ALIAS_OF'}")
    print()
    
    # Cargar reglas
    rules = load_rules()
    aliases = rules.get("aliases", {})
    
    if not aliases:
        print("⚠️  No hay aliases para aplicar")
        return
    
    print(f"📝 Total de aliases a procesar: {len(aliases)}")
    print()
    
    # Conectar a Neo4j
    connector = Neo4jConnector()
    if not connector.connect_to_neo4j():
        print("❌ No se pudo conectar a Neo4j")
        sys.exit(1)
    
    try:
        # Estadísticas
        stats = {
            "processed": 0,
            "success": 0,
            "skipped_not_exist": 0,
            "skipped_same": 0,
            "errors": 0
        }
        
        # Procesar cada alias
        for idx, (wrong, correct) in enumerate(aliases.items(), 1):
            stats["processed"] += 1
            
            # Skip si son iguales
            if wrong == correct:
                print(f"[{idx}/{len(aliases)}] ⏭️  SKIP '{wrong}' == '{correct}'")
                stats["skipped_same"] += 1
                continue
            
            # Verificar que ambos nodos existen
            wrong_exists, correct_exists = check_nodes_exist(connector, wrong, correct)
            
            if not wrong_exists and not correct_exists:
                print(f"[{idx}/{len(aliases)}] ⚠️  SKIP '{wrong}' -> '{correct}' (ninguno existe)")
                stats["skipped_not_exist"] += 1
                continue
            
            if not wrong_exists:
                print(f"[{idx}/{len(aliases)}] ⚠️  SKIP '{wrong}' -> '{correct}' ('{wrong}' no existe)")
                stats["skipped_not_exist"] += 1
                continue
            
            if not correct_exists:
                print(f"[{idx}/{len(aliases)}] ⚠️  SKIP '{wrong}' -> '{correct}' ('{correct}' no existe)")
                stats["skipped_not_exist"] += 1
                continue
            
            # Aplicar regla
            try:
                if merge_mode:
                    # Fusionar y borrar
                    print(f"[{idx}/{len(aliases)}] 🔥 FUSIONAR '{wrong}' -> '{correct}'")
                    if merge_and_delete_node(connector, wrong, correct):
                        print(f"    ✅ Fusionado y borrado exitosamente")
                        stats["success"] += 1
                    else:
                        print(f"    ❌ Error al fusionar")
                        stats["errors"] += 1
                else:
                    # Solo crear ALIAS_OF
                    print(f"[{idx}/{len(aliases)}] 🔗 ALIAS '{wrong}' -> '{correct}'")
                    if create_alias_relation(connector, wrong, correct):
                        print(f"    ✅ Relación ALIAS_OF creada")
                        stats["success"] += 1
                    else:
                        print(f"    ⚠️  Relación ya existe o no se pudo crear")
                        stats["success"] += 1  # No es error, puede que ya exista
            
            except Exception as e:
                print(f"    ❌ ERROR: {e}")
                stats["errors"] += 1
        
        # Resumen final
        print()
        print("=" * 60)
        print("📊 RESUMEN DE APLICACIÓN")
        print("=" * 60)
        print(f"Total procesados:        {stats['processed']}")
        print(f"✅ Exitosos:            {stats['success']}")
        print(f"⏭️  Saltados (no existen): {stats['skipped_not_exist']}")
        print(f"⏭️  Saltados (iguales):    {stats['skipped_same']}")
        print(f"❌ Errores:             {stats['errors']}")
        print()
        
        if merge_mode:
            print("🔥 IMPORTANTE: Los nodos incorrectos han sido BORRADOS de Neo4j")
            print("   Las relaciones han sido migradas a los nodos correctos")
        else:
            print("🔗 Se han creado relaciones ALIAS_OF entre nodos incorrectos y correctos")
            print("   Los nodos incorrectos siguen existiendo en Neo4j")
        
    finally:
        connector.close_connection()
        print()
        print("✓ Conexión cerrada")


def main():
    parser = argparse.ArgumentParser(
        description="Aplica reglas de rules_seed.json directamente a Neo4j"
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Fusiona nodos y borra los incorrectos (por defecto solo crea ALIAS_OF)"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirma la operación sin preguntar (útil para scripts)"
    )
    
    args = parser.parse_args()
    
    # Advertencia y confirmación
    if args.merge:
        print()
        print("⚠️  " + "=" * 56)
        print("⚠️  ATENCIÓN: Modo FUSIONAR Y BORRAR activado")
        print("⚠️  " + "=" * 56)
        print("⚠️  Este proceso va a:")
        print("⚠️  1. Migrar TODAS las relaciones de nodos incorrectos a correctos")
        print("⚠️  2. BORRAR PERMANENTEMENTE los nodos incorrectos")
        print("⚠️  3. Esta operación NO se puede deshacer fácilmente")
        print("⚠️  " + "=" * 56)
        print()
        
        if not args.confirm:
            respuesta = input("¿Estás SEGURO de continuar? (escribe 'SI' para confirmar): ")
            if respuesta.strip().upper() != "SI":
                print("❌ Operación cancelada")
                sys.exit(0)
    
    apply_rules_to_neo4j(merge_mode=args.merge)


if __name__ == "__main__":
    main()
