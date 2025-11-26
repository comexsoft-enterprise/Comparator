# -*- coding: utf-8 -*-
"""
approver_web.py (versión con 2 modos de resolución)

Modos por fila:
- ALIAS: crea (wrong)-[:ALIAS_OF]->(correct), no borra nada.
- MERGE_DELETE: migra TODAS las relaciones del nodo wrong al nodo correct y borra wrong.

El anotador:
1. Corrige/elige el destino (must existir en Neo4j, verde si OK).
2. Elige el modo (radio).
3. Marca filas y hace Approve.
4. Finalmente hace Apply Approved:
   - ejecuta ALIAS o MERGE_DELETE según cada fila
   - actualiza rules_seed.json
   - marca APPLIED
"""
from dotenv import load_dotenv
load_dotenv()
import os
import json
import socket
from datetime import datetime, UTC
from pathlib import Path
from typing import Dict, List, Tuple

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    jsonify,
    flash,
)
from neo4j import GraphDatabase

# ─────────── Funciones auxiliares para productos relacionados ───────────

def get_related_products(packaging_name: str, limit: int = 10) -> List[Dict]:
    """
    Obtiene productos relacionados con un packaging específico.
    Devuelve lista de dicts con información del producto incluyendo URL.
    """
    if not packaging_name:
        return []
    
    with driver.session() as session:
        query = f"""
        MATCH (p:{PACKAGING_LABEL} {{{NAME_PROP}: $packaging_name}})
        MATCH (p)-[r]-(prod:Product)
        RETURN prod.name AS product_name, 
               prod.url AS product_url,
               type(r) AS relationship_type
        LIMIT $limit
        """
        
        result = session.run(query, packaging_name=packaging_name, limit=limit)
        
        products = []
        for record in result:
            products.append({
                "name": record.get("product_name", "N/A"),
                "url": record.get("product_url", ""),
                "relationship": record.get("relationship_type", "RELATED")
            })
        
        return products


# ─────────── Flask ───────────

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j+s://04a91b12.databases.neo4j.io")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "5NtAEVGo8Rgc_Mz_y7I_jC_8rmPg0latUBr30_AwtUc")

PACKAGING_LABEL = os.getenv("PACKAGING_LABEL", "Ingredient ")
NAME_PROP = os.getenv("PACKAGING_NAME_PROP", "name")

SUGGESTIONS_JSON = Path(os.getenv("PACK_SUGGESTIONS_PATH", "sugerencias_packaging.json"))

RULES_PATH = Path(os.getenv("PACK_RULES_PATH", "./rules_seed.json"))

PAGE_SIZE_DEFAULT = 50

print(f"[BOOT] Neo4j URI={NEO4J_URI}")
print(f"[BOOT] Packaging label={PACKAGING_LABEL} prop={NAME_PROP}")
print(f"[BOOT] Sugerencias JSON={SUGGESTIONS_JSON}")
print(f"[BOOT] Rules path={RULES_PATH}")


# ───────── Neo4j connection / helpers ─────────

def get_driver():
    print("[get_driver] Conectando a Neo4j...")
    drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    drv.verify_connectivity()
    print("[get_driver] Conexión OK.")
    return drv

driver = get_driver()

def fetch_all_packaging_names(driver) -> List[str]:
    """
    Lista blanca de nombres válidos en Neo4j (Packaging.name).
    """
    print("[fetch_all_packaging_names] leyendo Packaging válidos…")
    with driver.session() as s:
        q = f"""
        MATCH (p:{PACKAGING_LABEL})
        RETURN DISTINCT p.{NAME_PROP} AS name
        ORDER BY name
        """
        out = [rec["name"] for rec in s.run(q) if rec["name"]]
    print(f"[fetch_all_packaging_names] {len(out)} términos")
    return out

VALID_PACKAGING_NAMES = fetch_all_packaging_names(driver)


# ───────── rules_seed.json helpers ─────────

DEFAULT_RULES = {
    "aliases": {},
    "canonical_terms": []
}

def load_rules() -> Dict:
    if RULES_PATH.exists():
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("aliases", {})
        data.setdefault("canonical_terms", [])
        return data
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_RULES, f, ensure_ascii=False, indent=2)
    return DEFAULT_RULES.copy()

def save_rules(d: Dict) -> None:
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"[save_rules] Reglas guardadas en {RULES_PATH}")


# ───────── sugerencias_packaging.json helpers ─────────

def load_suggestions() -> List[dict]:
    """
    Lee sugerencias JSON y normaliza campos.
    Añade _sid para UI y resolution_mode para la estrategia.
    """
    if not SUGGESTIONS_JSON.exists():
        raise FileNotFoundError(
            f"No existe {SUGGESTIONS_JSON}. Ejecuta primero normalizar_packaging.py."
        )

    with open(SUGGESTIONS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    for i, row in enumerate(data):
        row["_sid"] = i
        row.setdefault("status", "PENDING")
        row.setdefault("similarity", None)
        row.setdefault("rule_type", None)
        row.setdefault("rationale", "")
        row.setdefault("original", "")
        row.setdefault("original_normalized", "")
        row.setdefault("suggested_standard", None)
        row.setdefault("amount", 0)
        row.setdefault("resolution_mode", "ALIAS")  # "ALIAS" | "MERGE_DELETE"
        row["suggested"] = row.get("suggested_standard")
        
        # Auto-populate suggested with original value if suggested is empty
        if not row.get("suggested"):
            row["suggested"] = row.get("original", "")

    return data

def save_suggestions(rows: List[dict]) -> None:
    """
    Persiste sugerencias a disco (sin _sid).
    """
    clean_rows = []
    for r in rows:
        c = dict(r)
        c.pop("_sid", None)
        clean_rows.append(c)
    with open(SUGGESTIONS_JSON, "w", encoding="utf-8") as f:
        json.dump(clean_rows, f, ensure_ascii=False, indent=2)
    print(f"[save_suggestions] Guardadas {len(clean_rows)} sugerencias en {SUGGESTIONS_JSON}")


def update_status(ids: List[int], new_status: str) -> int:
    """
    Marca sugerencias seleccionadas como APPROVED o REJECTED.
    También añade timestamp en approved_at / rejected_at.
    """
    print(f"[update_status] ids={ids} new_status={new_status}")
    rows = load_suggestions()

    ts_field = None
    if new_status == "APPROVED":
        ts_field = "approved_at"
    elif new_status == "REJECTED":
        ts_field = "rejected_at"

    updated = 0
    now_iso = datetime.utcnow().isoformat() + "Z"

    for r in rows:
        if r["_sid"] in ids:
            r["status"] = new_status
            if ts_field:
                r[ts_field] = now_iso
            updated += 1

    save_suggestions(rows)
    print(f"[update_status] actualizadas={updated}")
    return updated


# ───────── negocio: listar/ordenar/paginar ─────────

def fetch_suggestions(
    status: str = "PENDING",
    q: str = "",
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
    sort: str | None = None,
    direction: str = "asc",
    rule_type: str | None = None
) -> Tuple[List[dict], int]:

    print(f"[fetch_suggestions] status={status} q={q} rule_type={rule_type} page={page} size={page_size}")
    all_rows = load_suggestions()

    # filtrar status
    if status and status.upper() != "ALL":
        all_rows = [r for r in all_rows if r.get("status", "").upper() == status.upper()]

    # filtrar por tipo de regla
    if rule_type and rule_type.upper() != "ALL":
        all_rows = [r for r in all_rows if (r.get("rule_type") or "").upper() == rule_type.upper()]

    # filtro libre
    if q:
        q_upper = q.upper()
        def matches(row):
            vals = [
                row.get("original"),
                row.get("original_normalized"),
                row.get("suggested"),
                row.get("rule_type"),
                row.get("rationale"),
                row.get("status"),
                str(row.get("similarity")) if row.get("similarity") is not None else "",
                str(row.get("amount")) if row.get("amount") is not None else "",
            ]
            return any(v and q_upper in v.upper() for v in vals)
        all_rows = [r for r in all_rows if matches(r)]

    total = len(all_rows)

    # orden
    allowed_sorts = {
        "sid", "original", "suggested", "rule_type", "similarity",
        "rationale", "status", "amount",
        "original_normalized", "created_at", "approved_at", "applied_at"
    }

    if sort not in allowed_sorts or sort is None:
        def default_key(r):
            return (
                r.get("status", ""),
                -(r.get("similarity") or 0),
                r.get("original") or "",
            )
        all_rows.sort(key=default_key)
    else:
        reverse = (direction == "desc")
        numeric_fields = {"similarity", "amount", "sid"}

        def get_val(r):
            if sort == "sid":
                return r.get("_sid")
            v = r.get(sort)
            if sort in numeric_fields:
                return v if v is not None else -1
            return (v or "").upper()
        all_rows.sort(key=get_val, reverse=reverse)

    # pagina
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = all_rows[start:end]

    # preparar para UI
    ui_rows = []
    for r in page_rows:
        ui_rows.append({
            "sid": r["_sid"],
            "original": r.get("original"),
            "suggested": r.get("suggested"),
            "rule_type": r.get("rule_type"),
            "similarity": r.get("similarity"),
            "rationale": r.get("rationale"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "approved_at": r.get("approved_at"),
            "applied_at": r.get("applied_at"),
            "amount": r.get("amount"),
            "resolution_mode": r.get("resolution_mode", "ALIAS"),
        })

    print(f"[fetch_suggestions] total_filtradas={total} devolviendo={len(ui_rows)} sort={sort} dir={direction}")
    return ui_rows, total


# ───────── negocio: aprobar / rechazar (bulk) ─────────

def bulk_update(rows: List[dict], form, valid_names: List[str]) -> Tuple[int,int,int]:
    """
    Aplica cambios de la vista a las filas seleccionadas.
    Devuelve (approved_valid, approved_invalid, rejected_count).
    """
    ids_raw = form.getlist("sid")
    ids = [int(i) for i in ids_raw if i.isdigit()]
    action = form.get("action")
    now_iso = datetime.now(UTC).isoformat()

    approved_valid = 0
    approved_invalid = 0
    rejected_count = 0

    for r in rows:
        sid = r.get("_sid")
        if sid not in ids:
            continue

        # 1. leer destino sugerido editado
        new_sug = form.get(f"suggested_{sid}", "").strip()
        if new_sug:
            r["suggested_standard"] = new_sug
            r["suggested"] = new_sug

        # 2. leer modo resolución elegido
        new_mode = form.get(f"mode_{sid}")
        if new_mode in ("ALIAS", "MERGE_DELETE"):
            r["resolution_mode"] = new_mode

        dest = r.get("suggested_standard") or r.get("suggested") or ""
        dest_is_valid = dest in valid_names

        if action == "approve":
            if dest_is_valid:
                r["status"] = "APPROVED"
                r["approved_at"] = now_iso
                r.pop("rejected_at", None)
                approved_valid += 1
            else:
                r["status"] = "PENDING_NO_TARGET"
                r["approved_at"] = None
                r.pop("rejected_at", None)
                # Auto-populate suggested with original value when no valid target
                if not r.get("suggested"):
                    r["suggested"] = r.get("original", "")
                approved_invalid += 1

        elif action == "reject":
            r["status"] = "REJECTED"
            r["rejected_at"] = now_iso
            r.pop("approved_at", None)
            rejected_count += 1

    return approved_valid, approved_invalid, rejected_count


# ───────── negocio: MERGE_DELETE helpers ─────────

def get_rel_types_for_node(session, node_name: str) -> Tuple[List[str], List[str]]:
    """
    Devuelve (tipos_salientes, tipos_entrantes) para un Packaging {name: node_name}.
    Cada lista son strings con los nombres de las relaciones (ej "IS_PACKED_IN", "PART_OF", etc).
    """
    q_out = f"""
    MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $n}})-[r]->()
    RETURN DISTINCT type(r) AS t
    """
    out_types = [rec["t"] for rec in session.run(q_out, n=node_name)]

    q_in = f"""
    MATCH ()-[r]->(w:{PACKAGING_LABEL} {{{NAME_PROP}: $n}})
    RETURN DISTINCT type(r) AS t
    """
    in_types = [rec["t"] for rec in session.run(q_in, n=node_name)]

    return out_types, in_types


def migrate_and_delete(session, wrong: str, correct: str):
    """
    Modo MERGE_DELETE para un par (wrong -> correct):
    - Mueve TODAS las relaciones (entrantes y salientes) de 'wrong' a 'correct'
      preservando propiedades.
    - Borra el nodo 'wrong'.
    Importante: NO crea nodos nuevos, ambos deben existir.
    """
    # 1. asegurarnos que ambos existen
    q_check = f"""
    MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $o}})
    OPTIONAL MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $s}})
    RETURN count(w) AS cw, count(c) AS cc
    """
    rec = session.run(q_check, o=wrong, s=correct).single()
    cw, cc = rec["cw"], rec["cc"]
    if cw == 0 or cc == 0 or wrong == correct:
        print(f"[migrate_and_delete] SKIP {wrong} -> {correct} (cw={cw}, cc={cc})")
        return False

    # 2. listar tipos de relaciones salientes y entrantes
    out_types, in_types = get_rel_types_for_node(session, wrong)

    # 3. para cada tipo saliente T:
    for t in out_types:
        q_out = f"""
        MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $o}})-[r:`{t}`]->(x)
        MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $s}})
        MERGE (c)-[r2:`{t}`]->(x)
        SET r2 = properties(r)
        DELETE r
        """
        session.run(q_out, o=wrong, s=correct)

    # 4. para cada tipo entrante T:
    for t in in_types:
        q_in = f"""
        MATCH (x)-[r:`{t}`]->(w:{PACKAGING_LABEL} {{{NAME_PROP}: $o}})
        MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $s}})
        MERGE (x)-[r2:`{t}`]->(c)
        SET r2 = properties(r)
        DELETE r
        """
        session.run(q_in, o=wrong, s=correct)

    # 5. finalmente borra el nodo wrong
    q_del = f"""
    MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $o}})
    DETACH DELETE w
    """
    session.run(q_del, o=wrong)

    print(f"[migrate_and_delete] DONE {wrong} -> {correct}")
    return True


def alias_only(session, wrong: str, correct: str):
    """
    Modo ALIAS: crea/asegura relación ALIAS_OF.
    """
    q_check = f"""
    MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $o}})
    OPTIONAL MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $s}})
    RETURN count(w) AS cw, count(c) AS cc
    """
    rec = session.run(q_check, o=wrong, s=correct).single()
    cw, cc = rec["cw"], rec["cc"]
    if cw == 0 or cc == 0 or wrong == correct:
        print(f"[alias_only] SKIP {wrong} -> {correct} (cw={cw}, cc={cc})")
        return False

    q_merge = f"""
    MATCH (w:{PACKAGING_LABEL} {{{NAME_PROP}: $o}})
    MATCH (c:{PACKAGING_LABEL} {{{NAME_PROP}: $s}})
    WHERE w <> c
    MERGE (w)-[:ALIAS_OF]->(c)
    """
    session.run(q_merge, o=wrong, s=correct)
    print(f"[alias_only] ALIAS_OF asegurado: {wrong} -> {correct}")
    return True


# ───────── negocio: apply approved ─────────

def apply_approved_and_persist_rules() -> Dict[str, int]:
    """
    Para cada fila con status == "APPROVED":
      - Si resolution_mode == "ALIAS": alias_only()
      - Si resolution_mode == "MERGE_DELETE": migrate_and_delete()
      - Añade al rules_seed.json aliases[original] = destino
      - Marca fila APPLIED
    """
    print("[apply_approved_and_persist_rules] inicio")

    rows = load_suggestions()
    rules = load_rules()
    aliases = rules.get("aliases", {})

    approved_rows = [r for r in rows if r.get("status") == "APPROVED"]
    print(f"[apply] nº filas APPROVED = {len(approved_rows)}")

    linked_or_merged = 0   # cuántas filas se aplicaron con éxito
    skipped = 0
    now_iso = datetime.now(UTC).isoformat()

    with driver.session() as session:
        for r in approved_rows:
            wrong = r.get("original")
            correct = r.get("suggested_standard") or r.get("suggested")
            mode = r.get("resolution_mode", "ALIAS")

            if not wrong or not correct:
                print(f"[apply] SKIP (faltan valores) {wrong} -> {correct}")
                skipped += 1
                continue

            if mode == "MERGE_DELETE":
                ok = migrate_and_delete(session, wrong, correct)
            else:
                ok = alias_only(session, wrong, correct)

            if ok:
                linked_or_merged += 1
                # aprendizaje reglas
                if wrong != correct:
                    if aliases.get(wrong) != correct:
                        aliases[wrong] = correct
                        print(f"[apply] alias aprendido {wrong} -> {correct}")
            else:
                skipped += 1

    # Recopilar términos canónicos (destinos) únicos de las filas aprobadas
    canonical_terms = set(rules.get("canonical_terms", []))
    for r in approved_rows:
        correct = r.get("suggested_standard") or r.get("suggested")
        if correct and correct.strip():
            canonical_terms.add(correct.strip())
            print(f"[apply] término canónico agregado: {correct}")
    
    # marcar APPLIED en memoria/json
    applied_count = 0
    for r in rows:
        if r.get("status") == "APPROVED":
            r["status"] = "APPLIED"
            r["applied_at"] = now_iso
            applied_count += 1

    rules["aliases"] = aliases
    rules["canonical_terms"] = sorted(list(canonical_terms))
    save_rules(rules)
    save_suggestions(rows)

    print("[apply_approved_and_persist_rules] FIN")
    return {
        "done": linked_or_merged,
        "applied": applied_count,
        "skipped": skipped,
        "aliases_count": len(aliases),
        "canonical_count": len(canonical_terms),
    }


# ───────── Flask app / template ─────────

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")

BASE_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Normalización Packaging – Aprobaciones</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }
    body { margin: 24px; max-width: 1500px; }
    header { display:flex; align-items:baseline; gap:12px; margin-bottom:16px; flex-wrap:wrap; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px 10px; border-bottom: 1px solid #eee; vertical-align: top; }
    th { position: sticky; top: 0; background: #fafafa; z-index: 1; }
    .toolbar { display:flex; gap:8px; flex-wrap:wrap; margin: 8px 0 16px; }
    .btn { padding:8px 12px; border:1px solid #ddd; border-radius:8px; background:#fff; cursor:pointer; text-decoration:none; display:inline-block; }
    .btn:hover { background:#f8f9fa; }
    .btn.primary { background:#0d6efd; border-color:#0d6efd; color:#fff; }
    .btn.primary:hover { background:#0b5ed7; }
    .btn.danger { background:#dc3545; border-color:#dc3545; color:#fff; }
    .btn.danger:hover { background:#bb2d3b; }
    .btn.success { background:#198754; border-color:#198754; color:#fff; }
    .btn.success:hover { background:#157347; }
    .btn.small { padding:4px 8px; font-size:12px; }
    .btn.outline { background:transparent; }
    .btn.outline.success { color:#198754; border-color:#198754; }
    .btn.outline.success:hover { background:#198754; color:#fff; }
    .btn.outline.danger { color:#dc3545; border-color:#dc3545; }
    .btn.outline.danger:hover { background:#dc3545; color:#fff; }
    .status { font-size: 12px; padding:2px 6px; border-radius:6px; border:1px solid #ddd; display:inline-block; }
    .status.PENDING{ background:#fff3cd; border-color:#ffe69c; }
    .status.PENDING_NO_TARGET{ background:#fde2e1; border-color:#f5b5b3; }
    .status.APPROVED{ background:#d1e7dd; border-color:#a3cfbb; }
    .status.REJECTED{ background:#f8d7da; border-color:#f1aeb5; }
    .status.APPLIED{ background:#e2e3e5; border-color:#cfd1d4; }
    .right { text-align:right; }
    .muted { color:#666; font-size:12px;}
    .msg { margin: 8px 0; }
    .pager { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    input[type="text"], select { padding:8px; border:1px solid #ddd; border-radius:8px; }
    .nowrap { white-space: nowrap; }
    .actions { text-align:center; width:120px; }
    .row-actions { display:flex; gap:4px; justify-content:center; }
    tr:hover { background:#f8f9fa; }
    .similarity-high { color:#198754; font-weight:bold; }
    .similarity-medium { color:#fd7e14; }
    .similarity-low { color:#dc3545; }
    
    /* Expandir productos relacionados */
    .expand-btn { 
      cursor: pointer; 
      padding: 4px 8px; 
      border: 1px solid #ddd; 
      border-radius: 4px; 
      background: #f8f9fa; 
      font-size: 12px;
    }
    .expand-btn:hover { background: #e9ecef; }
    .expand-btn.loading { opacity: 0.6; }
    
    .products-section {
      background: #f8f9fa;
      border-top: 1px solid #ddd;
      padding: 0;
      display: none;
    }
    
    .products-content {
      padding: 12px;
      max-height: 300px;
      overflow-y: auto;
    }
    
    .products-list {
      display: grid;
      gap: 8px;
    }
    
    .product-item {
      background: white;
      border: 1px solid #ddd;
      border-radius: 6px;
      padding: 8px;
      font-size: 12px;
    }
    
    .product-name {
      font-weight: 500;
      margin-bottom: 4px;
    }
    
    .product-url {
      color: #0d6efd;
      text-decoration: none;
      word-break: break-all;
    }
    
    .product-url:hover {
      text-decoration: underline;
    }
    
    .no-products {
      color: #666;
      font-style: italic;
      text-align: center;
      padding: 20px;
    }
    
    /* Checkbox más proporcionado */
    .checkbox-cell {
      padding: 4px;
      text-align: center;
      width: 40px;
    }
    
    input[type="checkbox"] {
      width: 20px;
      height: 20px;
      margin: 0;
      accent-color: #87ceeb;
      cursor: pointer;
    }
    
    input[type="checkbox"]:checked {
      background-color: #e3f2fd;
      border-color: #87ceeb;
    }

    /* input destino */
    .dest-input {
      padding:6px 8px;
      border-radius:6px;
      border:2px solid #ccc;
      min-width:180px;
      font-family: inherit;
      font-size: 14px;
      outline: none;
    }
    .dest-input.ok {
      border-color:#198754;
      background:#e9f7ef;
    }
    .dest-input.bad {
      border-color:#dc3545;
      background:#fde2e1;
    }

    .mode-box {
      display: flex;
      flex-direction: row;
      gap: 4px;
      padding: 4px;
    }
    .mode-option {
      flex: 1;
      position: relative;
      border: 2px solid #ddd;
      border-radius: 6px;
      padding: 8px;
      text-align: center;
      cursor: pointer;
      transition: all 0.2s;
      font-size: 12px;
      font-weight: 500;
    }
    .mode-option:hover {
      border-color: #0d6efd;
      background: #f8f9fa;
    }
    .mode-option.selected {
      border-color: #0d6efd;
      background: #e7f3ff;
      color: #0d6efd;
    }
    .mode-option input[type="radio"] {
      position: absolute;
      opacity: 0;
      width: 100%;
      height: 100%;
      margin: 0;
      cursor: pointer;
    }
    .mode-alias.selected { 
      background: #d1e7dd; 
      border-color: #198754; 
      color: #198754; 
    }
    .mode-merge.selected { 
      background: #f8d7da; 
      border-color: #dc3545; 
      color: #dc3545; 
    }
  </style>
</head>
<body>
<header>
  <h2>Normalización de Packaging – Aprobaciones</h2>
  <span class="muted">
    Paso 1: elige destino válido (verde).<br/>
    Paso 2: selecciona modo "Alias" o "Fusionar y borrar".<br/>
    Paso 3: Approve seleccionados. Luego Apply Approved.
  </span>
</header>

{% with messages = get_flashed_messages() %}
  {% if messages %}
    <div class="msg">
      {% for m in messages %}<div>{{ m }}</div>{% endfor %}
    </div>
  {% endif %}
{% endwith %}

<form method="get" action="{{ url_for('index') }}" class="toolbar">
  <input type="text" name="q" value="{{ q or '' }}" placeholder="Buscar original / destino…">

  <select name="status">
    {% for s in ['PENDING','PENDING_NO_TARGET','APPROVED','REJECTED','APPLIED','ALL'] %}
      <option value="{{ s }}" {% if status==s %}selected{% endif %}>{{ s }}</option>
    {% endfor %}
  </select>

  <select name="rule_type">
    {% set rt_selected = rule_type or 'ALL' %}
    <option value="ALL" {% if rt_selected=='ALL' %}selected{% endif %}>Regla: ALL</option>
    {% for rt in rule_types %}
      <option value="{{ rt }}" {% if rt_selected==rt %}selected{% endif %}>Regla: {{ rt }}</option>
    {% endfor %}
  </select>

  <select name="page_size">
    {% for sz in [25,50,100,200] %}
      <option value="{{ sz }}" {% if page_size==sz %}selected{% endif %}>{{ sz }} por página</option>
    {% endfor %}
  </select>

  <input type="hidden" name="sort" value="{{ sort or '' }}">
  <input type="hidden" name="direction" value="{{ direction or 'asc' }}">

  <button type="button" class="btn" onclick="clearFilters()">🔄 Limpiar</button>
</form>

<form method="post" action="{{ url_for('bulk_action') }}">
  <div class="toolbar">
    <button name="action" value="approve" class="btn success">Approve seleccionados</button>
    <button name="action" value="reject" class="btn danger">Reject seleccionados</button>
    <span class="muted">
      Approve:
      - si destino es válido ⇒ status=APPROVED<br>
      - si NO ⇒ status=PENDING_NO_TARGET<br>
      Reject ⇒ status=REJECTED
    </span>
  </div>

  <table>
    <thead>
      <tr>
        <th class="checkbox-cell"><input type="checkbox" id="checkAll"></th>
        <th>
          <a href="{{ url_for('index', q=q, status=status, rule_type=rule_type, page_size=page_size, sort='original', direction='desc' if sort=='original' and direction=='asc' else 'asc') }}">
            Original{% if sort=='original' %} {{ '▲' if direction=='asc' else '▼' }}{% endif %}
          </a>
        </th>
        <th>Destino sugerido<br><small>(elige Packaging existente)</small></th>
        <th>Modo resolución</th>
        <th>
          <a href="{{ url_for('index', q=q, status=status, rule_type=rule_type, page_size=page_size, sort='rule_type', direction='desc' if sort=='rule_type' and direction=='asc' else 'asc') }}">
            Regla{% if sort=='rule_type' %} {{ '▲' if direction=='asc' else '▼' }}{% endif %}
          </a>
        </th>
        <th>
          <a href="{{ url_for('index', q=q, status=status, rule_type=rule_type, page_size=page_size, sort='similarity', direction='desc' if sort=='similarity' and direction=='asc' else 'asc') }}">
            Similitud{% if sort=='similarity' %} {{ '▲' if direction=='asc' else '▼' }}{% endif %}
          </a>
        </th>
        <th>
          <a href="{{ url_for('index', q=q, status=status, rule_type=rule_type, page_size=page_size, sort='amount', direction='desc' if sort=='amount' and direction=='asc' else 'asc') }}">
            Cantidad{% if sort=='amount' %} {{ '▲' if direction=='asc' else '▼' }}{% endif %}
          </a>
        </th>
        <th>
          <a href="{{ url_for('index', q=q, status=status, rule_type=rule_type, page_size=page_size, sort='status', direction='desc' if sort=='status' and direction=='asc' else 'asc') }}">
            Estado{% if sort=='status' %} {{ '▲' if direction=='asc' else '▼' }}{% endif %}
          </a>
        </th>
        <th class="actions">Acciones</th>
      </tr>
    </thead>

    <tbody>
      {% for r in rows %}
      {% set is_valid = (r.suggested in valid_names) %}
      <tr>
        <td class="checkbox-cell"><input type="checkbox" name="sid" value="{{ r.sid }}" {% if r.status == 'PENDING_NO_TARGET' %}checked{% endif %}></td>
        <td>
          {{ r.original }}
          <br>
          <span class="expand-btn" onclick="toggleProducts({{ r.sid }}, '{{ r.original }}')">
            🔍 Ver productos
          </span>
        </td>

        <td class="nowrap">
          <input
            type="text"
            name="suggested_{{ r.sid }}"
            value="{{ r.suggested or '' }}"
            list="packagingOptions"
            class="dest-input {{ 'ok' if is_valid else 'bad' }}"
          />
        </td>

        <td class="mode-box">
          <div class="mode-option mode-alias {% if r.resolution_mode != 'MERGE_DELETE' %}selected{% endif %}"
               onclick="selectMode(this, '{{ r.sid }}', 'ALIAS')">
            <input type="radio" name="mode_{{ r.sid }}" value="ALIAS"
              {% if r.resolution_mode != 'MERGE_DELETE' %}checked{% endif %}>
            MANTENER
          </div>
          <div class="mode-option mode-merge {% if r.resolution_mode == 'MERGE_DELETE' %}selected{% endif %}"
               onclick="selectMode(this, '{{ r.sid }}', 'MERGE_DELETE')">
            <input type="radio" name="mode_{{ r.sid }}" value="MERGE_DELETE"
              {% if r.resolution_mode == 'MERGE_DELETE' %}checked{% endif %}>
            BORRAR
          </div>
        </td>

        <td class="nowrap">{{ r.rule_type or '-' }}</td>
        <td class="right nowrap">{{ ('%.3f' % r.similarity) if r.similarity is not none else '-' }}</td>
        <td class="right nowrap">{{ r.amount if r.amount is not none else '-' }}</td>
        <td class="nowrap"><span class="status {{ r.status }}">{{ r.status }}</span></td>
        <td class="actions">
          {% if r.status == 'PENDING' %}
          <div class="row-actions">
            <form method="post" action="{{ url_for('single_action', sid=r.sid, action='approve') }}" style="display:inline;">
              <button type="submit" class="btn small outline success" title="Aprobar">✓</button>
            </form>
            <form method="post" action="{{ url_for('single_action', sid=r.sid, action='reject') }}" style="display:inline;">
              <button type="submit" class="btn small outline danger" title="Rechazar">✗</button>
            </form>
          </div>
          {% else %}
          <span class="muted">-</span>
          {% endif %}
        </td>
      </tr>
      <!-- Fila expandible para productos relacionados -->
      <tr id="products-row-{{ r.sid }}" class="products-section">
        <td colspan="9" class="products-content">
          <div id="products-content-{{ r.sid }}">
            <div class="no-products">Cargando productos relacionados...</div>
          </div>
        </td>
      </tr>
      {% endfor %}
      {% if not rows %}
      <tr><td colspan="8" class="muted">No hay resultados.</td></tr>
      {% endif %}
    </tbody>
  </table>

  <div class="toolbar pager">
    {% set total_pages = (total + page_size - 1) // page_size %}
    <span>Página {{ page }} / {{ total_pages }} · Total {{ total }}</span>

    {% if page > 1 %}
      <a class="btn"
         href="{{ url_for('index',
                          q=q, status=status, rule_type=rule_type,
                          page=page-1, page_size=page_size,
                          sort=sort, direction=direction) }}">
        ← Anterior
      </a>
    {% endif %}

    {% if page < total_pages %}
      <a class="btn"
         href="{{ url_for('index',
                          q=q, status=status, rule_type=rule_type,
                          page=page+1, page_size=page_size,
                          sort=sort, direction=direction) }}">
        Siguiente →
      </a>
    {% endif %}
  </div>
</form>

<hr>

<form method="post" action="{{ url_for('apply_approved') }}" class="toolbar">
  <button class="btn primary"
    onclick="return confirm(
      'Vas a aplicar TODAS las filas APPROVED.\n' +
      '- ALIAS: crea relación ALIAS_OF.\n' +
      '- FUSIONAR Y BORRAR: migra TODAS las relaciones al destino y BORRA el nodo original.\n' +
      'También se actualiza rules_seed.json y se marcan como APPLIED.\n\n¿Continuar?'
    )">
    Apply Approved → Neo4j + rules_seed.json
  </button>
  <span class="muted">
    Ojo: "Fusionar y borrar" elimina el nodo original del grafo.
  </span>
</form>

<!-- Todos los Packaging válidos para autocompletar -->
<datalist id="packagingOptions">
  {% for nm in valid_names %}
    <option value="{{ nm }}"></option>
  {% endfor %}
</datalist>

<script>
  // Lista blanca de Packaging válidos (desde backend)
  const VALID_NAMES = {{ valid_names | tojson }};

  function validateInput(el){
    const val = el.value.trim();
    if (VALID_NAMES.includes(val)) {
        el.classList.add('ok');
        el.classList.remove('bad');
    } else {
        el.classList.add('bad');
        el.classList.remove('ok');
    }
  }

  // Función para seleccionar modo de resolución
  function selectMode(element, sid, mode) {
    // Remover selected de todos los modos de esta fila
    const row = element.closest('tr');
    row.querySelectorAll('.mode-option').forEach(opt => {
      opt.classList.remove('selected');
    });
    
    // Agregar selected al elemento clickeado
    element.classList.add('selected');
    
    // Marcar el radio button correspondiente
    const radio = element.querySelector('input[type="radio"]');
    if (radio) {
      radio.checked = true;
    }
    
    // Marcar automáticamente el checkbox de la fila cuando se selecciona un modo
    const checkbox = row.querySelector('input[type="checkbox"][name="sid"]');
    if (checkbox) {
      checkbox.checked = true;
    }
  }

  // validar en vivo todos los inputs destino
  const destInputs = document.querySelectorAll('.dest-input');
  destInputs.forEach(inp => {
    inp.addEventListener('input', () => validateInput(inp));
    validateInput(inp);
  });

  // checkbox "select all"
  const checkAll = document.getElementById('checkAll');
  if (checkAll) {
    checkAll.addEventListener('change', (e) => {
      document.querySelectorAll('input[type="checkbox"][name="sid"]').forEach(cb => {
        cb.checked = e.target.checked;
      });
    });
  }

  // Filtros inmediatos
  const searchInput = document.querySelector('input[name="q"]');
  const statusSelect = document.querySelector('select[name="status"]');
  const ruleTypeSelect = document.querySelector('select[name="rule_type"]');
  const pageSizeSelect = document.querySelector('select[name="page_size"]');
  
  function applyFiltersImmediately() {
    // Agregar los campos hidden de ordenamiento
    const form = document.querySelector('form');
    const sortInput = document.querySelector('input[name="sort"]');
    const directionInput = document.querySelector('input[name="direction"]');
    
    if (sortInput) form.appendChild(sortInput);
    if (directionInput) form.appendChild(directionInput);
    
    form.submit();
  }

  // Filtro inmediato para búsqueda (con pequeño delay para evitar muchas requests)
  let searchTimeout;
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(applyFiltersImmediately, 500);
    });
  }

  // Filtros inmediatos para selects
  if (statusSelect) {
    statusSelect.addEventListener('change', applyFiltersImmediately);
  }
  if (ruleTypeSelect) {
    ruleTypeSelect.addEventListener('change', applyFiltersImmediately);
  }
  if (pageSizeSelect) {
    pageSizeSelect.addEventListener('change', applyFiltersImmediately);
  }

  // Limpiar filtros
  function clearFilters() {
    if (searchInput) searchInput.value = '';
    if (statusSelect) statusSelect.value = 'PENDING';
    if (ruleTypeSelect) ruleTypeSelect.value = 'ALL';
    if (pageSizeSelect) pageSizeSelect.value = '50';
    applyFiltersImmediately();
  }
  
  // Hacer clearFilters global para el botón
  window.clearFilters = clearFilters;

  // Función para expandir/colapsar productos relacionados
  window.toggleProducts = function(sid, packagingName) {
    const productsRow = document.getElementById(`products-row-${sid}`);
    const contentDiv = document.getElementById(`products-content-${sid}`);
    const expandBtn = document.querySelector(`[onclick*="toggleProducts(${sid}"]`);
    
    if (!productsRow || !contentDiv) return;
    
    // Si ya está visible, colapsar
    if (productsRow.style.display === 'table-row') {
      productsRow.style.display = 'none';
      expandBtn.textContent = '🔍 Ver productos';
      return;
    }
    
    // Mostrar fila
    productsRow.style.display = 'table-row';
    expandBtn.textContent = '📦 Cargando...';
    expandBtn.classList.add('loading');
    
    // Cargar productos si no se han cargado antes
    if (!contentDiv.dataset.loaded) {
      fetch(`/api/products/${encodeURIComponent(packagingName)}`)
        .then(response => response.json())
        .then(data => {
          if (data.success && data.products.length > 0) {
            const productsHtml = data.products.map(product => `
              <div class="product-item">
                <div class="product-name">${escapeHtml(product.name || 'Sin nombre')}</div>
                ${product.url ? `<a href="${escapeHtml(product.url)}" target="_blank" class="product-url">${escapeHtml(product.url)}</a>` : '<span class="text-muted">Sin URL</span>'}
              </div>
            `).join('');
            
            contentDiv.innerHTML = `
              <div class="products-list">
                <strong>Productos relacionados con "${escapeHtml(packagingName)}" (${data.count}):</strong>
                ${productsHtml}
              </div>
            `;
          } else {
            contentDiv.innerHTML = `<div class="no-products">No se encontraron productos relacionados con "${escapeHtml(packagingName)}"</div>`;
          }
          contentDiv.dataset.loaded = 'true';
        })
        .catch(error => {
          console.error('Error loading products:', error);
          contentDiv.innerHTML = `<div class="no-products">Error cargando productos: ${error.message}</div>`;
        })
        .finally(() => {
          expandBtn.textContent = '📦 Productos';
          expandBtn.classList.remove('loading');
        });
    } else {
      expandBtn.textContent = '📦 Productos';
      expandBtn.classList.remove('loading');
    }
  };

  // Función auxiliar para escapar HTML
  function escapeHtml(text) {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
  }

  // Mejorar ordenamiento por columnas con indicadores visuales
  document.querySelectorAll('th a').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      
      // Extraer parámetros del href
      const url = new URL(link.href);
      const sort = url.searchParams.get('sort');
      const direction = url.searchParams.get('direction');
      
      // Crear inputs hidden para ordenamiento
      const form = document.querySelector('form');
      
      // Remover inputs de ordenamiento existentes
      form.querySelectorAll('input[name="sort"], input[name="direction"]').forEach(inp => inp.remove());
      
      // Agregar nuevos inputs
      const sortInput = document.createElement('input');
      sortInput.type = 'hidden';
      sortInput.name = 'sort';
      sortInput.value = sort;
      
      const directionInput = document.createElement('input');
      directionInput.type = 'hidden';
      directionInput.name = 'direction';
      directionInput.value = direction;
      
      form.appendChild(sortInput);
      form.appendChild(directionInput);
      
      // Resetear página a 1 cuando se ordena
      const pageInput = form.querySelector('input[name="page"]');
      if (pageInput) pageInput.remove();
      
      form.submit();
    });
  });
</script>

</body>
</html>
"""


# ───────── Flask rutas ─────────

@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "PENDING").upper()
    rule_type = request.args.get("rule_type") or "ALL"
    page_size = int(request.args.get("page_size", PAGE_SIZE_DEFAULT))
    page = max(1, int(request.args.get("page", "1")))
    sort = request.args.get("sort") or None
    direction = request.args.get("direction", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"

    all_rows_now = load_suggestions()
    rule_types = sorted({
        r.get("rule_type")
        for r in all_rows_now
        if r.get("rule_type")
    })

    rows, total = fetch_suggestions(
        status=status,
        q=q,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
        rule_type=rule_type,
    )

    return render_template_string(
        BASE_HTML,
        rows=rows,
        total=total,
        page=page,
        page_size=page_size,
        q=q,
        status=status,
        rule_type=rule_type,
        rule_types=rule_types,
        sort=sort,
        direction=direction,
        valid_names=VALID_PACKAGING_NAMES,
    )


@app.route("/bulk", methods=["POST"])
def bulk_action():
    """
    Se llama al pulsar Approve seleccionados / Reject seleccionados.
    - Para cada fila marcada, lee:
        * el texto destino actual
        * el modo resolución elegido
    - Valida si el destino existe en Neo4j (en memoria)
    - Actualiza status y timestamps acorde a la acción
    - Persiste sugerencias_packaging.json
    """
    action = request.form.get("action")
    print(f"[bulk_action] action={action}")

    rows = load_suggestions()

    approved_valid, approved_invalid, rejected_count = bulk_update(
        rows,
        request.form,
        VALID_PACKAGING_NAMES
    )

    save_suggestions(rows)

    if action == "approve":
        flash(
            f"Approve OK: {approved_valid} válidas · "
            f"{approved_invalid} con destino inválido (dejadas en PENDING_NO_TARGET)"
        )
    elif action == "reject":
        flash(f"Rechazadas: {rejected_count}")
    else:
        flash("Acción no reconocida.")

    return redirect(request.referrer or url_for("index"))


@app.route("/apply", methods=["POST"])
def apply_approved():
    """
    Ejecuta en Neo4j las filas APPROVED:
      - ALIAS => alias_only
      - MERGE_DELETE => migrate_and_delete
    Luego:
      - añade alias a rules_seed.json
      - marca esas filas APPLIED
    """
    res = apply_approved_and_persist_rules()
    flash(
        "Aplicado. Éxitos: {done} · Marcadas APPLIED: {applied} · "
        "Saltadas: {skipped} · Aliases totales: {aliases_count} · "
        "Términos canónicos: {canonical_count}".format(**res)
    )
    return redirect(url_for("index", status="APPLIED"))


@app.route("/single/<int:sid>/<action>", methods=["POST"])
def single_action(sid, action):
    """
    Aprobar o rechazar una sola sugerencia mediante botones individuales.
    """
    if action not in ("approve", "reject"):
        flash("Acción no válida.")
        return redirect(request.referrer or url_for("index"))
    
    new_status = "APPROVED" if action == "approve" else "REJECTED"
    count = update_status([sid], new_status)
    
    if count > 0:
        flash(f"Elemento {sid} {'aprobado' if action == 'approve' else 'rechazado'}.")
    else:
        flash(f"No se pudo actualizar el elemento {sid}.")
    
    return redirect(request.referrer or url_for("index"))


@app.route("/api/products/<packaging_name>")
def api_get_products(packaging_name):
    """
    Endpoint para obtener productos relacionados con un packaging específico.
    """
    try:
        products = get_related_products(packaging_name, limit=10)
        return jsonify({
            "success": True,
            "packaging": packaging_name,
            "products": products,
            "count": len(products)
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "packaging": packaging_name
        }), 500


@app.route("/api/suggestions")
def api_suggestions():
    """
    Devuelve lo que hay en la tabla como JSON (debug/integración).
    """
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "PENDING").upper()
    rule_type = request.args.get("rule_type") or "ALL"
    page_size = int(request.args.get("page_size", PAGE_SIZE_DEFAULT))
    page = max(1, int(request.args.get("page", "1")))
    sort = request.args.get("sort") or None
    direction = request.args.get("direction", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"

    rows, total = fetch_suggestions(
        status=status,
        q=q,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
        rule_type=rule_type,
    )

    return jsonify({
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": sort,
        "direction": direction,
        "rule_type": rule_type,
        "rows": rows,
    })


# ───────── Arranque ─────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Puerto HTTP. Si no se indica, intenta 5000 y si está ocupado usa uno libre."
    )
    args = parser.parse_args()

    port = args.port
    if port is None:
        try_port = 5000
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sck:
            try:
                sck.bind(("127.0.0.1", try_port))
                sck.close()
                port = try_port
            except OSError:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                    s2.bind(("127.0.0.1", 0))
                    port = s2.getsockname()[1]

    print(f"→ Web en http://127.0.0.1:{port}")
    print(f"   Leyendo sugerencias de {SUGGESTIONS_JSON}")
    print(f"   Guardando aprendizaje en {RULES_PATH}")
    print(f"   Neo4j en {NEO4J_URI}")
    print(f"   Packaging válidos cargados: {len(VALID_PACKAGING_NAMES)} términos")
    app.run(debug=True, port=port, host="127.0.0.1")
