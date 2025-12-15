# -*- coding: utf-8 -*-
"""
verify_and_apply_aliases.py

Verifica y aplica TODOS los aliases de rules_seed.json a Neo4j.
Se debe ejecutar ANTES de generar nuevas sugerencias para asegurar
que todos los aliases conocidos ya están implementados en la BD.

Uso:
    python verify_and_apply_aliases.py [--dry-run]
    
    --dry-run: Solo reporta qué haría sin aplicar cambios
"""

import os
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.connectors.neo4j_connector import Neo4jConnector

from dotenv import load_dotenv
load_dotenv()

# ─────────────── Config ───────────────

PACKAGING_LABEL = os.getenv("PACKAGING_LABEL", "Ingredient")
NAME_PROP = os.getenv("PACKAGING_NAME_PROP", "name")
RULES_PATH = Path(os.getenv("PACK_RULES_PATH", "./rules_seed.json"))

# ─────────────── Funciones ───────────────

def load_rules() -> Dict:
    """Carga rules_seed.json"""
    if not RULES_PATH.exists():
        print(f"❌ Error: {RULES_PATH} no existe")
        return {"aliases": {}, "canonical_terms": []}
    
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return data


def check_alias_exists(connector: Neo4jConnector, wrong: str, correct: str) -> bool:
    """
    Verifica si existe la relación ALIAS_OF en Neo4j.
    """
    query = f"""
    MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $wrong}})
    MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $correct}})
    MATCH (w)-[r:ALIAS_OF]->(c)
    RETURN count(r) AS cnt
    """
    
    result = connector.execute_query(query, {"wrong": wrong, "correct": correct})
    return result[0]["cnt"] > 0 if result else False


def check_nodes_exist(connector: Neo4jConnector, wrong: str, correct: str) -> Tuple[bool, bool]:
    """
    Verifica si existen los nodos wrong y correct en Neo4j.
    Retorna (wrong_exists, correct_exists)
    """
    query = f"""
    OPTIONAL MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $wrong}})
    OPTIONAL MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $correct}})
    RETURN 
        count(w) > 0 AS wrong_exists,
        count(c) > 0 AS correct_exists
    """
    
    result = connector.execute_query(query, {"wrong": wrong, "correct": correct})
    if result:
        return result[0]["wrong_exists"], result[0]["correct_exists"]
    return False, False


def create_alias_relation(connector: Neo4jConnector, wrong: str, correct: str) -> bool:
    """
    Crea la relación ALIAS_OF entre wrong y correct.
    Asume que ambos nodos ya existen.
    """
    query = f"""
    MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $wrong}})
    MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $correct}})
    MERGE (w)-[r:ALIAS_OF]->(c)
    RETURN count(r) AS created
    """
    
    try:
        result = connector.execute_query(query, {"wrong": wrong, "correct": correct})
        return result[0]["created"] > 0 if result else False
    except Exception as e:
        print(f"  ❌ Error creando relación: {e}")
        return False


def verify_and_apply_aliases(dry_run: bool = False):
    """
    Verifica todos los aliases en rules_seed.json y los aplica a Neo4j.
    
    Args:
        dry_run: Si es True, solo reporta sin aplicar cambios
    """
    print("=" * 80)
    print("🔍 VERIFICACIÓN Y APLICACIÓN DE ALIASES")
    print("=" * 80)
    print(f"Archivo: {RULES_PATH}")
    print(f"Modo: {'DRY-RUN (solo lectura)' if dry_run else 'APLICAR CAMBIOS'}")
    print(f"Label: {PACKAGING_LABEL}, Propiedad: {NAME_PROP}")
    print()
    
    # 1. Cargar reglas
    print("📖 Paso 1: Cargando rules_seed.json...")
    rules = load_rules()
    aliases = rules.get("aliases", {})
    
    if not aliases:
        print("⚠️  No hay aliases definidos en rules_seed.json")
        return
    
    print(f"✓ {len(aliases)} aliases encontrados")
    print()
    
    # 2. Conectar a Neo4j
    print("🔌 Paso 2: Conectando a Neo4j...")
    connector = Neo4jConnector()
    if not connector.connect_to_neo4j():
        print("❌ Error: No se pudo conectar a Neo4j")
        return
    print("✓ Conexión establecida")
    print()
    
    try:
        # 3. Verificar cada alias
        print("🔍 Paso 3: Verificando aliases en Neo4j...")
        print("-" * 80)
        
        already_exists = []
        missing_aliases = []
        missing_nodes = []
        
        for idx, (wrong, correct) in enumerate(aliases.items(), 1):
            # Mostrar progreso cada 10 items
            if idx % 10 == 0:
                print(f"  Progreso: {idx}/{len(aliases)} ({idx/len(aliases)*100:.1f}%)")
            
            # Verificar si los nodos existen
            wrong_exists, correct_exists = check_nodes_exist(connector, wrong, correct)
            
            if not wrong_exists and not correct_exists:
                missing_nodes.append((wrong, correct, "ambos"))
                continue
            elif not wrong_exists:
                missing_nodes.append((wrong, correct, "wrong"))
                continue
            elif not correct_exists:
                missing_nodes.append((wrong, correct, "correct"))
                continue
            
            # Ambos nodos existen, verificar relación
            if check_alias_exists(connector, wrong, correct):
                already_exists.append((wrong, correct))
            else:
                missing_aliases.append((wrong, correct))
        
        print(f"✓ Verificación completada: {len(aliases)} aliases analizados")
        print()
        
        # 4. Reporte
        print("=" * 80)
        print("📊 REPORTE DE VERIFICACIÓN")
        print("=" * 80)
        print()
        
        print(f"✅ Ya implementados en Neo4j: {len(already_exists)}")
        print(f"⚠️  Faltan por implementar: {len(missing_aliases)}")
        print(f"❌ Nodos faltantes: {len(missing_nodes)}")
        print()
        
        # Mostrar aliases faltantes
        if missing_aliases:
            print("⚠️  ALIASES FALTANTES (nodos existen, relación no):")
            print("-" * 80)
            for wrong, correct in missing_aliases[:20]:  # Mostrar primeros 20
                print(f"  '{wrong}' → '{correct}'")
            if len(missing_aliases) > 20:
                print(f"  ... y {len(missing_aliases) - 20} más")
            print()
        
        # Mostrar nodos faltantes
        if missing_nodes:
            print("❌ NODOS FALTANTES (no se pueden crear relaciones):")
            print("-" * 80)
            for wrong, correct, missing in missing_nodes[:20]:  # Mostrar primeros 20
                if missing == "ambos":
                    print(f"  '{wrong}' ✗ y '{correct}' ✗ (ambos faltantes)")
                elif missing == "wrong":
                    print(f"  '{wrong}' ✗ (nodo origen faltante)")
                else:
                    print(f"  '{correct}' ✗ (nodo destino faltante)")
            if len(missing_nodes) > 20:
                print(f"  ... y {len(missing_nodes) - 20} más")
            print()
            print("💡 Nota: Estos nodos deben existir antes de crear relaciones ALIAS_OF")
            print()
        
        # 5. Aplicar cambios si no es dry-run
        if not dry_run and missing_aliases:
            print("=" * 80)
            print("🔧 Paso 4: Aplicando aliases faltantes...")
            print("-" * 80)
            
            created_count = 0
            failed_count = 0
            
            for idx, (wrong, correct) in enumerate(missing_aliases, 1):
                if idx % 10 == 0:
                    print(f"  Progreso: {idx}/{len(missing_aliases)} ({idx/len(missing_aliases)*100:.1f}%)")
                
                if create_alias_relation(connector, wrong, correct):
                    created_count += 1
                else:
                    failed_count += 1
                    print(f"  ❌ Falló: '{wrong}' → '{correct}'")
            
            print()
            print(f"✓ Relaciones ALIAS_OF creadas: {created_count}")
            if failed_count > 0:
                print(f"❌ Fallos: {failed_count}")
            print()
        elif dry_run and missing_aliases:
            print("💡 Ejecuta sin --dry-run para aplicar los cambios")
            print()
        
        # 6. Resumen final
        print("=" * 80)
        print("📋 RESUMEN FINAL")
        print("=" * 80)
        print(f"Total aliases en rules_seed.json: {len(aliases)}")
        print(f"  ✅ Ya implementados: {len(already_exists)} ({len(already_exists)/len(aliases)*100:.1f}%)")
        print(f"  ⚠️  Faltantes: {len(missing_aliases)} ({len(missing_aliases)/len(aliases)*100:.1f}%)")
        print(f"  ❌ No aplicables (nodos faltantes): {len(missing_nodes)} ({len(missing_nodes)/len(aliases)*100:.1f}%)")
        
        if not dry_run and missing_aliases:
            print(f"  🔧 Recién creados: {created_count}")
            final_pending = len(missing_aliases) - created_count
            if final_pending > 0:
                print(f"  ⚠️  Aún pendientes: {final_pending}")
        
        print()
        
        if len(missing_aliases) == 0 and len(missing_nodes) == 0:
            print("🎉 ¡PERFECTO! Todos los aliases están correctamente implementados en Neo4j")
        elif not dry_run:
            print("✓ Proceso de aplicación completado")
        else:
            print("ℹ️  Modo dry-run: No se aplicaron cambios")
        
        print("=" * 80)
        
    finally:
        connector.close_connection()
        print("🔌 Conexión a Neo4j cerrada")


# ─────────────── CLI ───────────────

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Verifica y aplica aliases de rules_seed.json a Neo4j"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo reporta qué haría sin aplicar cambios"
    )
    
    args = parser.parse_args()
    
    verify_and_apply_aliases(dry_run=args.dry_run)
