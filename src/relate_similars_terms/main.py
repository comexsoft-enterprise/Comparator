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
from pathlib import Path
from difflib import SequenceMatcher
from typing import List, Dict, Tuple, Optional

from neo4j import GraphDatabase

# ───────────────── Config ─────────────────

# Labels / props en Neo4j
PACKAGING_LABEL = os.getenv("PACKAGING_LABEL", "Ingredient")
NAME_PROP = os.getenv("PACKAGING_NAME_PROP", "name")

from dotenv import load_dotenv
load_dotenv()
# Conexión Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j+s://04a91b12.databases.neo4j.io")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "5NtAEVGo8Rgc_Mz_y7I_jC_8rmPg0latUBr30_AwtUc")

# Fichero de reglas consolidado (aliases aprendidos + términos canónicos oficiales)
RULES_PATH = Path(os.getenv("PACK_RULES_PATH", "./rules_seed.json"))

# Fichero de salida con las sugerencias generadas en este run
SUGGESTIONS_OUT = Path(os.getenv("PACK_SUGGESTIONS_OUT", "sugerencias_packaging.json"))


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

def get_driver():
    """
    Valida credenciales mínimas y abre un driver Neo4j.
    """
    print("› get_driver(): iniciando conexión Neo4j...")

    if not NEO4J_URI or "://" not in NEO4J_URI:
        raise ValueError(
            "NEO4J_URI no está definido o es inválido. Ejemplos válidos:\n"
            "  bolt://localhost:7687   (local)\n"
            "  neo4j://localhost:7687  (routing)\n"
            "  neo4j+s://<host>.databases.neo4j.io  (Aura/SSL)\n"
            f"Valor actual: {repr(NEO4J_URI)}"
        )
    if not NEO4J_USER:
        raise ValueError("NEO4J_USERNAME no está definido.")
    if not NEO4J_PASSWORD:
        raise ValueError("NEO4J_PASSWORD no está definido.")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()

    print(f"✓ get_driver(): conectado a Neo4j {NEO4J_URI} como usuario '{NEO4J_USER}'")
    return driver


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


def normalize_base(s: str) -> str:
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
    if original_s != s:
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


# ─────────────── Consultas Neo4j ────────────────

def contar_conexiones(driver, nodeA_label: str, nodeB_label: str) -> Dict[str, int]:
    """
    Devuelve dict { packaging_name: nº_de_conexiones_con_Product }, ordenado desc.
    Usamos esto para priorizar lo más frecuente.
    """
    print(f"› contar_conexiones(): calculando conexiones {nodeA_label} - {nodeB_label} ...")

    with driver.session() as session:
        query = f"""
            MATCH (a:{nodeA_label})-[r]-(b:{nodeB_label})
            RETURN a.{NAME_PROP} AS name, count(r) AS conexiones
        """
        result = session.run(query)

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


def target_exists(driver, value: str) -> bool:
    """
    Chequea si YA existe un Packaging con name = value.
    Esto se usa para decidir status = PENDING / PENDING_NO_TARGET.
    """
    if value is None:
        print("  · target_exists(): valor None -> devolvemos False")
        return False

    with driver.session() as s:
        q = f"MATCH (n:{PACKAGING_LABEL} {{{NAME_PROP}: $v}}) RETURN count(n) AS c"
        rec = s.run(q, v=value).single()
        exists = rec["c"] > 0

    # print(f"  · target_exists(): '{value}' existe? -> {exists}")
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
    driver,
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
        norm_term = normalize_base(term)
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

    for idx, (original, count) in enumerate(names.items(), start=1):
        # print(f"\n--- sugerir(): procesando {idx}/{len(names)} -> '{original}' (conexiones={count})")

        # Normalización base para comparar
        nb = normalize_base(original)
        
        # ⚡ FILTRO 1: Si el término original EXACTO ya está en aliases, skip
        # Solo comparamos el nombre EXACTO, no la versión normalizada
        if original in aliases_map:
            alias_target = aliases_map[original]
            skipped_already_corrected += 1
            print(f"  ⏭️  SKIP '{original}' -> ya fue corregido a '{alias_target}'")
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

        # 2) SIMILITUD contra vocabulario canónico
        parts = [t.strip() for t in re.split(r'[\/\+\-]', nb) if t.strip()]
        found = []
        for p in parts or [nb]:
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
                exists = target_exists(driver, sug)
                sugg_item = {
                    "original": original,
                    "original_normalized": nb,
                    "amount": count,
                    "suggested_standard": sug,
                    "rule_type": "FUZZY_COMPOSED",
                    "similarity": round(sim, 3),
                    "rationale": "Componentes mapeados por similitud (comparado tras normalización).",
                    "status": "PENDING" if exists else "PENDING_NO_TARGET"
                }
                suggestions.append(sugg_item)
            else:
                # Caso simple fuzzy
                sug = found[0][1]
                sim = found[0][2]
                exists = target_exists(driver, sug)
                sugg_item = {
                    "original": original,
                    "original_normalized": nb,
                    "amount": count,
                    "suggested_standard": sug,
                    "rule_type": "FUZZY",
                    "similarity": round(sim, 3),
                    "rationale": "Mejor coincidencia por similitud (comparado tras normalización).",
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

def generar_solo_sugerencias():
    """
    1. Carga reglas (aliases + canonical_terms) desde rules_seed.json
    2. Conecta a Neo4j
    3. Calcula frecuencia/conexiones de Packaging vs. Product
    4. Genera sugerencias con esas reglas
    5. Exporta resultado a SUGGESTIONS_OUT (ej. sugerencias_packaging.json)
    """
    print("=== generar_solo_sugerencias(): inicio ===")

    # Paso 1: reglas actuales
    rules = load_rules_local()
    aliases_map = rules.get("aliases", {})
    canonical_terms = rules.get("canonical_terms", [])
    # Aseguramos que canonical_terms sea lista de strings única/ordenada estable
    canonical_terms = sorted(list(dict.fromkeys(canonical_terms)))

    # Paso 2: Neo4j
    driver = get_driver()
    try:
        # Paso 3: contar conexiones Packaging-Product
        print("• Paso 3/5 contar_conexiones()")
        count = contar_conexiones(driver, PACKAGING_LABEL, "Product")

        # Paso 4: generar sugerencias normalizadas
        print("• Paso 4/5 sugerir()")
        sugs = sugerir(driver, count, aliases_map, canonical_terms)

        # Paso 5: exportar el resultado a JSON
        print("• Paso 5/5 exportar_json()")
        exportar_json(sugs, SUGGESTIONS_OUT)

        print("=== generar_solo_sugerencias(): FIN OK ===")
        print(f"   Archivo generado: {SUGGESTIONS_OUT}")
        print("   Ahora puedes abrir el approver_web.py para revisar, aprobar y aplicar.")

    finally:
        print("› generar_solo_sugerencias(): cerrando conexión Neo4j...")
        driver.close()
        print("✓ generar_solo_sugerencias(): conexión cerrada.")


# ─────────────── CLI directo ───────────────

if __name__ == "__main__":
    generar_solo_sugerencias()
