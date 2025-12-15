# -*- coding: utf-8 -*-
"""
normalizar_packaging.py

Genera sugerencias de normalización SIN aplicar cambios en la BD:
- Lee Packaging y su nº de conexiones con Product
- Propone un "sugerido" por reglas (capacidad/unidades), alias y similitud
- Comprueba si el sugerido ya existe en Packaging
  - Si existe  -> status = PENDING (aprobable)
  - Si NO      -> status = PENDING_NO_TARGET (revisión manual)
- Exporta TODO a un JSON local (no toca Neo4j más allá de leer)

Uso:
    python normalizar_packaging.py

Requiere (env opcionales):
    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD
    PACKAGING_LABEL, PACKAGING_NAME_PROP
    PACK_RULES_PATH (fichero con aliases + canonical_terms)
    PACK_SUGGESTIONS_OUT (salida sugerencias)
"""

import os
import re
import json
import sys
from pathlib import Path
from difflib import SequenceMatcher
from typing import List, Dict, Tuple, Optional

# Add parent directory to path to import connectors
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.connectors.neo4j_connector import Neo4jConnector

# ───────────────── Config ─────────────────

# Labels / props en Neo4j
PACKAGING_LABEL = os.getenv("PACKAGING_LABEL", "Ingredient")
# NAME_PROP cambia según el label: "value" para Quantity, "name" para los demás
NAME_PROP = "value" if PACKAGING_LABEL == "Quantity" else "name"

from dotenv import load_dotenv
load_dotenv()
# Conexión Neo4j - ahora manejado por Neo4jConnector

def get_rules_path() -> Path:
    """Devuelve la ruta del archivo de reglas basado en el PACKAGING_LABEL actual."""
    label_lower = PACKAGING_LABEL.lower()
    return Path(f"./{label_lower}_rules_seed.json")

def get_suggestions_path() -> Path:
    """Devuelve la ruta del archivo de sugerencias basado en el PACKAGING_LABEL actual."""
    label_lower = PACKAGING_LABEL.lower()
    return Path(f"./sugerencias_{label_lower}.json")

# Fichero de reglas consolidado (aliases aprendidos + términos canónicos oficiales)
RULES_PATH = get_rules_path()

# Fichero de salida con las sugerencias generadas en este run
SUGGESTIONS_OUT = get_suggestions_path()


# ─────────────── Reglas (aliases/canonical) ───────────────

def load_rules_local() -> Dict:
    """
    Carga el fichero de reglas (aliases + canonical_terms).
    Si no existe, lo crea con estructura mínima y lo devuelve.
    Este mismo fichero también lo usa approver_web.py.
    """
    print(f"› load_rules_local(): cargando reglas desde {RULES_PATH} ...")

    base_structure = {
        "aliases": {},
        "canonical_terms": []
    }

    if RULES_PATH.exists():
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            print("abierto")
            data = json.load(f)

        if not isinstance(data, dict):
            print("  ⚠ rules_seed.json no es un objeto JSON válido, usando estructura vacía.")
            data = base_structure
        else:
            data.setdefault("aliases", {})
            data.setdefault("canonical_terms", [])
    else:
        # Si no existe, lo creamos vacío
        RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(base_structure, f, ensure_ascii=False, indent=2)
        data = base_structure
        print(f"  · No existía {RULES_PATH}, creado con estructura base.")

    print(f"✓ load_rules_local(): {len(data['aliases'])} aliases, {len(data['canonical_terms'])} términos canónicos.")
    return data


# ─────────────── Conexión Neo4j ───────────────

def get_connector():
    """
    Crea y conecta un Neo4jConnector.
    """
    print("› get_connector(): iniciando conexión Neo4j...")
    
    connector = Neo4jConnector()
    if not connector.connect_to_neo4j():
        raise ConnectionError("No se pudo conectar a Neo4j")
    
    print("✓ get_connector(): conectado a Neo4j exitosamente")
    return connector


# ─────────────── Helpers NLP ──────────────

def strip_accents(s: str) -> str:
    """
    Quita acentos/diacríticos: 'BOTELLÍN' -> 'BOTELLIN'
    """
    import unicodedata
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def normalize_base(s: str, verbose: bool = False) -> str:
    """
    Normaliza un nombre de packaging para comparaciones:
    - trim espacios
    - mayúsculas
    - sin acentos
    - separa / + - en tokens con espacios
    - colapsa espacios repetidos
    """
    original_s = s
    s = s.strip().upper()
    s = strip_accents(s)
    s = re.sub(r'\s+', ' ', s)
    s = s.replace('/', ' / ').replace('+', ' + ').replace('-', ' - ')
    s = re.sub(r'\s+', ' ', s).strip()
    # Solo loggear si verbose=True o si el cambio es significativo
    if verbose and original_s != s:
        print(f"  · normalize_base(): '{original_s}' -> '{s}'")
    return s


def normalize_capacity_or_count(token: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Detecta patrones tipo:
      '0,5L', '6L', '200ML'  -> ('500 ML', 'CAPACITY')
      '10UDS', '3UNIDADES'  -> ('10 UDS', 'COUNT')

    Devuelve (valor_normalizado, tipo_regla) o (None, None) si no aplica.
    """
    t = normalize_base(token).replace(' ', '')  # ej '10 UDS' -> '10UDS'

    # 1) CAPACIDAD / VOLUMEN
    m = re.match(r'^(\d+(?:[.,]\d+)?)(ML|L)$', t)
    if m:
        value = m.group(1).replace(',', '.')
        unit = m.group(2)

        if unit == 'L':
            # Convertimos L -> ML si es posible
            try:
                ml = float(value) * 1000.0
                ml = int(ml) if float(ml).is_integer() else ml
                norm_value = f"{ml} ML"
            except Exception:
                # fallback si hay algo raro
                norm_value = f"{value} L"
            print(f"  · normalize_capacity_or_count(): '{token}' detectado CAPACITY -> '{norm_value}'")
            return norm_value, "CAPACITY"
        else:
            norm_value = f"{value} ML"
            print(f"  · normalize_capacity_or_count(): '{token}' detectado CAPACITY -> '{norm_value}'")
            return norm_value, "CAPACITY"

    # 2) CONTEO / UNIDADES
    m2 = re.match(r'^(\d+)(U|UDS|UNIDAD(?:ES)?)$', t)
    if m2:
        qty = int(m2.group(1))
        norm_value = f"{qty} UDS"
        print(f"  · normalize_capacity_or_count(): '{token}' detectado COUNT -> '{norm_value}'")
        return norm_value, "COUNT"

    # 3) Nada
    return None, None


def resolve_alias_chain(term: str, aliases_map: Dict[str, str], max_depth: int = 10) -> str:
    """
    Resuelve una cadena de aliases hasta llegar al término final canónico.
    
    Ejemplo:
        aliases = {
            "high-oleic oil": "high-oleic sunflower oil",
            "high-oleic sunflower oil": "sunflower oil"
        }
        resolve_alias_chain("high-oleic oil") -> "sunflower oil"
    
    Args:
        term: Término a resolver
        aliases_map: Diccionario de aliases
        max_depth: Profundidad máxima para evitar ciclos infinitos
        
    Returns:
        Término canónico final después de resolver todos los aliases
    """
    original_term = term
    visited = set()
    depth = 0
    
    while term in aliases_map and depth < max_depth:
        if term in visited:
            # Detectado ciclo infinito
            print(f"  ⚠️ resolve_alias_chain(): ciclo detectado en '{original_term}' -> devolviendo último valor válido")
            break
        
        visited.add(term)
        next_term = aliases_map[term]
        
        if next_term == term:
            # El alias apunta a sí mismo
            break
        
        term = next_term
        depth += 1
    
    if depth > 0:
        print(f"  🔗 resolve_alias_chain(): '{original_term}' -> '{term}' (resuelto en {depth} paso{'s' if depth > 1 else ''})")
    
    return term


def best_match(term: str, vocab: List[str], cutoff: float = 0.86) -> Tuple[Optional[str], float]:
    """
    Busca en vocab el término más parecido a 'term' usando similitud difusa.
    Solo devuelve match si score >= cutoff.
    """
    best = None
    best_score = 0.0

    for v in vocab:
        score = SequenceMatcher(None, term, v).ratio()
        if score > best_score:
            best = v
            best_score = score

    if best and best_score >= cutoff:
        print(f"  · best_match(): '{term}' ≈ '{best}' (score={round(best_score,3)}) ✓")
        return best, best_score

    return None, best_score


def check_alias_similarity(term: str, aliases_map: Dict[str, str], cutoff: float = 0.75) -> Tuple[Optional[str], Optional[str], float]:
    """
    Verifica si el término es similar a alguna clave en aliases_map.
    Si encuentra match, devuelve la clave del alias, su valor canónico, y el score.
    
    Ejemplo:
        aliases = {"eroski": "marca_blanca", "makro": "marca_blanca"}
        check_alias_similarity("EROSKI") -> ("eroski", "marca_blanca", 1.0)
        check_alias_similarity("ERONSKI") -> ("eroski", "marca_blanca", 0.86)
    
    Args:
        term: Término a verificar
        aliases_map: Diccionario de aliases
        cutoff: Umbral de similitud mínimo
        
    Returns:
        (alias_key, canonical_value, score) si encuentra match, sino (None, None, 0.0)
    """
    term_normalized = normalize_base(term, verbose=False)
    
    best_alias_key = None
    best_canonical = None
    best_score = 0.0
    
    for alias_key, alias_value in aliases_map.items():
        alias_key_normalized = normalize_base(alias_key, verbose=False)
        score = SequenceMatcher(None, term_normalized, alias_key_normalized).ratio()
        
        if score > best_score:
            best_score = score
            best_alias_key = alias_key
            best_canonical = alias_value
    
    if best_alias_key and best_score >= cutoff:
        print(f"  🎯 check_alias_similarity(): '{term}' ≈ alias '{best_alias_key}' -> '{best_canonical}' (score={round(best_score,3)})")
        return best_alias_key, best_canonical, best_score
    
    return None, None, best_score


# ─────────────── Consultas Neo4j ────────────────

# Cache para evitar queries repetidas a Neo4j
_target_exists_cache = {}

def preload_valid_packaging_names(connector: Neo4jConnector) -> set:
    """
    Pre-carga TODOS los nombres de Packaging válidos en memoria.
    Esto es más eficiente que hacer queries individuales.
    """
    print("› preload_valid_packaging_names(): cargando nombres válidos desde Neo4j...")
    
    query = f"MATCH (n:{PACKAGING_LABEL}) RETURN n.{NAME_PROP} AS name"
    result = connector.execute_query(query)
    
    valid_names = {record["name"] for record in result if record.get("name")}
    
    # Llenar el cache
    global _target_exists_cache
    _target_exists_cache = {name: True for name in valid_names}
    
    print(f"✓ preload_valid_packaging_names(): {len(valid_names)} nombres válidos cargados en cache")
    return valid_names

def contar_conexiones(connector: Neo4jConnector, nodeA_label: str, nodeB_label: str) -> Dict[str, int]:
    """
    Devuelve dict { packaging_name: nº_de_conexiones_con_Product }, ordenado desc.
    Usamos esto para priorizar lo más frecuente.
    """
    print(f"› contar_conexiones(): calculando conexiones {nodeA_label} - {nodeB_label} ...")

    query = f"""
        MATCH (a:{nodeA_label})-[r]-(b:{nodeB_label})
        RETURN a.{NAME_PROP} AS name, count(r) AS conexiones
    """
    result = connector.execute_query(query)

    conexiones = {}
    for record in result:
        nm = record["name"]
        cnt = record["conexiones"]
        if nm:
            conexiones[nm] = cnt

    conexiones = dict(
        sorted(conexiones.items(), key=lambda item: item[1], reverse=True)
    )

    print(f"✓ contar_conexiones(): {len(conexiones)} nombres encontrados; top 5 ejemplo:")
    for i, (k, v) in enumerate(conexiones.items()):
        print(f"    {k!r}: {v} conexiones")
        if i >= 4:
            break

    return conexiones


# Cache para evitar queries repetidas a Neo4j
_target_exists_cache = {}

def target_exists(connector: Neo4jConnector, value: str) -> bool:
    """
    Chequea si YA existe un Packaging con name = value.
    Usa cache para evitar queries repetidas.
    Esto se usa para decidir status = PENDING / PENDING_NO_TARGET.
    """
    if value is None:
        return False
    
    # Usar cache para evitar queries repetidas
    if value in _target_exists_cache:
        return _target_exists_cache[value]

    q = f"MATCH (n:{PACKAGING_LABEL} {{{NAME_PROP}: $v}}) RETURN count(n) AS c"
    result = connector.execute_query(q, {"v": value})
    exists = result[0]["c"] > 0 if result else False
    
    # Guardar en cache
    _target_exists_cache[value] = exists
    return exists


# ─────────────── Export JSON ───────────────

def exportar_json(suggestions: List[Dict], ruta_salida: Path) -> None:
    """
    Vuelca las sugerencias a un archivo JSON legible.
    Reordena para que primero salgan las cosas más relevantes.
    """
    print(f"› exportar_json(): preparando volcado a {ruta_salida} ...")

    ordenadas = sorted(
        suggestions,
        key=lambda x: (
            x.get("status", ""),
            -x.get("amount", 0)
        )
    )

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)

    with open(ruta_salida, "w", encoding="utf-8") as f:
        json.dump(ordenadas, f, ensure_ascii=False, indent=2)

    print(f"✓ exportar_json(): archivo escrito correctamente.")
    print(f"  Total sugerencias exportadas: {len(ordenadas)}")


# ─────────────── Core lógica de sugerencias ───────────────

def sugerir(
    connector: Neo4jConnector,
    names: Dict[str, int],
    aliases_map: Dict[str, str],
    canonical_terms: List[str],
) -> List[Dict]:
    """
    Recorre cada nombre de Packaging y genera una sugerencia normalizada.
    No escribe en BD. Solo devuelve una lista de dicts listos para exportar.
    """
    print("› sugerir(): generando sugerencias...")

    suggestions: List[Dict] = []

    print(f"Aliases disponibles: {len(aliases_map)}")
    print(f"Términos canónicos: {len(canonical_terms)}")
    
    # Normalizar canonical_terms para comparación fuzzy
    # Crear mapa: término_normalizado -> término_original
    canonical_normalized_map = {}
    for term in canonical_terms:
        norm_term = normalize_base(term, verbose=False)
        canonical_normalized_map[norm_term] = term
    
    canonical_normalized_list = list(canonical_normalized_map.keys())
    print(f"Términos canónicos normalizados: {len(canonical_normalized_list)}")
    if len(canonical_normalized_list) > 0:
        print("Ejemplo términos canónicos (primeros 5):")
        for i, term in enumerate(list(canonical_normalized_list)[:5]):
            print(f"  '{term}' (original: '{canonical_normalized_map[term]}')")
    
    # Mostrar algunos ejemplos de aliases para debug
    if len(aliases_map) > 0:
        print("Ejemplo aliases (primeros 5):")
        for i, (k, v) in enumerate(list(aliases_map.items())[:5]):
            print(f"  '{k}' -> '{v}'")
    
    skipped_already_corrected = 0
    skipped_already_canonical = 0

    total_items = len(names)
    for idx, (original, count) in enumerate(names.items(), start=1):
        # Mostrar progreso cada 100 items o al final
        if idx % 100 == 0 or idx == total_items:
            print(f"  Progreso: {idx}/{total_items} ({idx/total_items*100:.1f}%)")

        # Normalización base para comparar
        nb = normalize_base(original)
        
        # ⚡ FILTRO 1: Si el término original (o cualquier variación) ya está en aliases, resolvemos la cadena
        # Primero intentamos con el término exacto
        resolved_term = resolve_alias_chain(original, aliases_map)
        if resolved_term != original:
            # El término tiene un alias directo
            skipped_already_corrected += 1
            print(f"  ⏭️  SKIP '{original}' -> ya fue corregido a '{resolved_term}'")
            continue
        
        # También intentamos con el término normalizado
        resolved_term_normalized = resolve_alias_chain(nb, aliases_map)
        if resolved_term_normalized != nb:
            # El término normalizado tiene un alias
            skipped_already_corrected += 1
            print(f"  ⏭️  SKIP '{original}' (normalizado: '{nb}') -> ya fue corregido a '{resolved_term_normalized}'")
            continue
            
        # ⚡ FILTRO 2: Si el término original EXACTO ya es canónico, skip
        # Solo comparamos el nombre EXACTO, no la versión normalizada
        if original in canonical_terms:
            skipped_already_canonical += 1
            print(f"  ⏭️  SKIP '{original}' -> ya es término canónico")
            continue
            
        # print(f"  Procesando: '{original}' -> normalizado: '{nb}'")

        # 1) CAPACITY / COUNT (patrones '0,5L', '10UDS', etc.)
        # cap, kind = normalize_capacity_or_count(original)
        # if cap:
        #     exists = target_exists(driver, cap)
        #     sugg_item = {
        #         "original": original,
        #         "original_normalized": nb,
        #         "amount": count,
        #         "suggested_standard": cap,
        #         "rule_type": kind,  # "CAPACITY" | "COUNT"
        #         "similarity": 1.0,
        #         "rationale": f"Detectado como {kind.lower()} por patrón.",
        #         "status": "PENDING" if exists else "PENDING_NO_TARGET"
        #     }
        #     suggestions.append(sugg_item)
        #     continue

        # 2) ALIAS SIMILARITY: Si el término es similar a un alias, sugerir el canónico
        alias_key, canonical_from_alias, alias_score = check_alias_similarity(original, aliases_map, cutoff=0.75)
        if canonical_from_alias:
            # Resolver la cadena de aliases para obtener el término final
            final_canonical = resolve_alias_chain(canonical_from_alias, aliases_map)
            exists = target_exists(connector, final_canonical)
            sugg_item = {
                "original": original,
                "original_normalized": nb,
                "amount": count,
                "suggested_standard": final_canonical,
                "rule_type": "ALIAS_SIMILARITY",
                "similarity": round(alias_score, 3),
                "rationale": f"Similar a alias '{alias_key}' que mapea a '{final_canonical}'.",
                "status": "PENDING" if exists else "PENDING_NO_TARGET"
            }
            suggestions.append(sugg_item)
            continue

        # 3) SIMILITUD contra vocabulario canónico
        parts = [t.strip() for t in re.split(r'[\/\+\-]', nb) if t.strip()]
        found = []
        for p in parts or [nb]:
            # Primero verificar si el part es similar a un alias
            alias_key_part, canonical_from_alias_part, alias_score_part = check_alias_similarity(p, aliases_map, cutoff=0.75)
            if canonical_from_alias_part:
                # Resolver la cadena de aliases
                final_canonical_part = resolve_alias_chain(canonical_from_alias_part, aliases_map)
                found.append((p, final_canonical_part, alias_score_part))
                print(f"  ✓ Match (vía alias): '{p}' ≈ '{alias_key_part}' -> '{final_canonical_part}' (score={round(alias_score_part,3)})")
            else:
                # Comparar contra términos canónicos NORMALIZADOS
                m, score = best_match(p, canonical_normalized_list, cutoff=0.75)
                if m:
                    # m es el término normalizado, buscar el original
                    original_canonical = canonical_normalized_map[m]
                    found.append((p, original_canonical, score))
                    print(f"  ✓ Match: '{p}' -> '{original_canonical}' (score={round(score,3)})")

        if found:
            if len(parts) > 1:
                # Caso compuesto: "BOTELLA+VIDRIO" -> "BOTELLA + VIDRIO"
                sug = " + ".join([f[1] for f in found])
                sim = sum(f[2] for f in found) / len(found)
                exists = target_exists(connector, sug)
                
                # Determinar si algún componente vino de un alias
                has_alias_match = any(
                    check_alias_similarity(f[0], aliases_map, cutoff=0.75)[1] is not None 
                    for f in found
                )
                rule_type = "FUZZY_COMPOSED_WITH_ALIAS" if has_alias_match else "FUZZY_COMPOSED"
                
                sugg_item = {
                    "original": original,
                    "original_normalized": nb,
                    "amount": count,
                    "suggested_standard": sug,
                    "rule_type": rule_type,
                    "similarity": round(sim, 3),
                    "rationale": "Componentes mapeados por similitud (incluyendo aliases)" if has_alias_match else "Componentes mapeados por similitud (comparado tras normalización).",
                    "status": "PENDING" if exists else "PENDING_NO_TARGET"
                }
                suggestions.append(sugg_item)
            else:
                # Caso simple fuzzy
                sug = found[0][1]
                sim = found[0][2]
                exists = target_exists(connector, sug)
                
                # Determinar si vino de un alias
                is_alias_match = check_alias_similarity(found[0][0], aliases_map, cutoff=0.75)[1] is not None
                rule_type = "FUZZY_WITH_ALIAS" if is_alias_match else "FUZZY"
                
                sugg_item = {
                    "original": original,
                    "original_normalized": nb,
                    "amount": count,
                    "suggested_standard": sug,
                    "rule_type": rule_type,
                    "similarity": round(sim, 3),
                    "rationale": "Mejor coincidencia por similitud via alias" if is_alias_match else "Mejor coincidencia por similitud (comparado tras normalización).",
                    "status": "PENDING" if exists else "PENDING_NO_TARGET"
                }
                suggestions.append(sugg_item)
        else:
            # 3) SIN RESOLVER
            sugg_item = {
                "original": original,
                "original_normalized": nb,
                "amount": count,
                "suggested_standard": None,
                "rule_type": "UNRESOLVED",
                "similarity": 0.0,
                "rationale": "Sin equivalencia clara; requiere revisión.",
                "status": "PENDING_NO_TARGET"
            }
            suggestions.append(sugg_item)

    total_skipped = skipped_already_corrected + skipped_already_canonical
    
    print(f"\n✓ sugerir(): procesamiento completado:")
    print(f"    📋 Total términos analizados: {len(names)}")
    print(f"    ✅ Ya corregidos (aliases): {skipped_already_corrected}")
    print(f"    🎯 Ya canónicos: {skipped_already_canonical}")
    print(f"    ⏭️  Total saltados: {total_skipped}")
    print(f"    💡 Nuevas sugerencias generadas: {len(suggestions)}")
    return suggestions


# ─────────────── Orquestador ───────────────

def create_alias_relation(connector: Neo4jConnector, wrong: str, correct: str) -> bool:
    """
    Crea la relación ALIAS_OF entre wrong y correct en Neo4j.
    Retorna True si se creó exitosamente, False si hubo error.
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
        print(f"  ❌ Error creando '{wrong}' → '{correct}': {e}")
        return False


def check_and_create_missing_aliases(connector: Neo4jConnector, aliases_map: Dict[str, str]) -> Tuple[int, int, int]:
    """
    Verifica todos los aliases y crea automáticamente los que faltan.
    Retorna (ya_existian, creados, no_aplicables)
    """
    print("🔍 Verificando y creando aliases faltantes en Neo4j...")
    print("-" * 80)
    
    already_exists = 0
    created = 0
    not_applicable = 0
    
    total = len(aliases_map)
    
    for idx, (wrong, correct) in enumerate(aliases_map.items(), 1):
        # Mostrar progreso cada 10 items
        if idx % 10 == 0 or idx == total:
            print(f"  📊 Progreso: {idx}/{total} ({idx/total*100:.1f}%) - Creados: {created}, Ya existían: {already_exists}")
        
        # Verificar si los nodos existen
        check_nodes_query = f"""
        OPTIONAL MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $wrong}})
        OPTIONAL MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $correct}})
        RETURN 
            count(w) > 0 AS wrong_exists,
            count(c) > 0 AS correct_exists
        """
        
        try:
            result = connector.execute_query(check_nodes_query, {"wrong": wrong, "correct": correct})
            if not result:
                not_applicable += 1
                continue
            
            wrong_exists = result[0]["wrong_exists"]
            correct_exists = result[0]["correct_exists"]
            
            # Si alguno de los nodos no existe, no se puede crear la relación
            if not wrong_exists or not correct_exists:
                not_applicable += 1
                continue
            
            # Verificar si ya existe la relación
            check_alias_query = f"""
            MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $wrong}})
            MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $correct}})
            OPTIONAL MATCH (w)-[r:ALIAS_OF]->(c)
            RETURN count(r) > 0 AS exists
            """
            
            result = connector.execute_query(check_alias_query, {"wrong": wrong, "correct": correct})
            if result and result[0]["exists"]:
                already_exists += 1
            else:
                # Crear la relación
                if create_alias_relation(connector, wrong, correct):
                    created += 1
                else:
                    not_applicable += 1
                    
        except Exception as e:
            print(f"  ⚠️ Error procesando '{wrong}' → '{correct}': {e}")
            not_applicable += 1
    
    print()
    print("✓ Verificación y creación de aliases completada:")
    print(f"  ✅ Ya existían: {already_exists}/{total} ({already_exists/total*100:.1f}%)")
    print(f"  🔧 Recién creados: {created}/{total} ({created/total*100:.1f}%)")
    if not_applicable > 0:
        print(f"  ⚠️  No aplicables (nodos faltantes): {not_applicable}/{total} ({not_applicable/total*100:.1f}%)")
    
    return already_exists, created, not_applicable


def generar_solo_sugerencias(auto_create_aliases: bool = True):
    """
    1. Verifica y crea aliases faltantes en Neo4j (si auto_create_aliases=True)
    2. Carga reglas (aliases + canonical_terms) desde rules_seed.json
    3. Conecta a Neo4j
    4. Calcula frecuencia/conexiones de Packaging vs. Product
    5. Genera sugerencias con esas reglas
    6. Exporta resultado a SUGGESTIONS_OUT (ej. sugerencias_packaging.json)
    
    Args:
        auto_create_aliases: Si True, verifica y crea automáticamente aliases faltantes
    """
    print("=== generar_solo_sugerencias(): inicio ===")
    print(f"    Label: {PACKAGING_LABEL}")
    print(f"    Rules: {get_rules_path()}")
    print(f"    Output: {get_suggestions_path()}")
    print()

    # Paso 1: reglas actuales
    print("• Paso 1/7: Cargando reglas desde rules_seed.json")
    rules = load_rules_local()
    aliases_map = rules.get("aliases", {})
    canonical_terms = rules.get("canonical_terms", [])
    # Aseguramos que canonical_terms sea lista de strings única/ordenada estable
    canonical_terms = sorted(list(dict.fromkeys(canonical_terms)))
    print()

    # Paso 2: Neo4j
    print("• Paso 2/7: Conectando a Neo4j")
    connector = get_connector()
    print()
    
    try:
        # Paso 3: Verificar y crear aliases faltantes
        if auto_create_aliases and aliases_map:
            print("• Paso 3/7: Verificando y creando aliases faltantes en Neo4j")
            already_exists, created, not_applicable = check_and_create_missing_aliases(connector, aliases_map)
            
            total_ok = already_exists + created
            total_aliases = len(aliases_map)
            
            if total_ok == total_aliases:
                print(f"\n🎉 ¡PERFECTO! Todos los aliases ({total_aliases}) están en Neo4j")
            elif created > 0:
                print(f"\n✅ Se crearon {created} aliases nuevos. Total implementados: {total_ok}/{total_aliases}")
            
            if not_applicable > 0:
                print(f"\n⚠️  ADVERTENCIA: {not_applicable} aliases no se pudieron aplicar (nodos faltantes)")
                print("   Estos términos podrían generar sugerencias aunque ya tengan corrección definida.")
            print()
        else:
            print("• Paso 3/7: Verificación de aliases deshabilitada")
            print()
        
        # Paso 4: pre-cargar nombres válidos de Packaging (optimización)
        print("• Paso 4/7: Precargando nombres válidos de Packaging")
        preload_valid_packaging_names(connector)
        print()
        
        # Paso 5: contar conexiones Packaging-Product
        print("• Paso 5/7: Contando conexiones Packaging-Product")
        count = contar_conexiones(connector, PACKAGING_LABEL, "Product")
        print()

        # Paso 6: generar sugerencias normalizadas
        print("• Paso 6/7: Generando sugerencias de normalización")
        sugs = sugerir(connector, count, aliases_map, canonical_terms)
        print()

        # Paso 7: exportar el resultado a JSON
        print("• Paso 7/7: Exportando sugerencias a JSON")
        exportar_json(sugs, SUGGESTIONS_OUT)

        print()
        print("=== generar_solo_sugerencias(): FIN OK ===")
        print(f"   Label usado: {PACKAGING_LABEL}")
        print(f"   Archivo generado: {get_suggestions_path()}")
        print("   Ahora puedes abrir web.py para revisar, aprobar y aplicar.")
        print()

    finally:
        print("› Cerrando conexión Neo4j...")
        connector.close_connection()
        print("✓ Conexión cerrada.")
        print()


# ─────────────── CLI directo ───────────────

if __name__ == "__main__":
    generar_solo_sugerencias()