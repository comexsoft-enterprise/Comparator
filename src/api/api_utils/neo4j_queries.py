import logging
from pathlib import Path
import sys
from enum import Enum
from typing import List, Union, Tuple, Any, Dict, Optional
from collections import deque

from pydantic import BaseModel, Field, field_validator

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from data.schemas.neo4j_ontology import NEO4J_ONTOLOGY

class FilterTypes(str, Enum):
    EQUAL = "equal"
    LESS_THAN = "less_than"
    GREATER_THAN = "greater_than"
    BETWEEN = "between"
    CONTAINS = "contains"
    IN = "in"

class FilterTriplets(BaseModel):
    field: str
    filter_type: FilterTypes
    value: Union[str, float, List[Union[str, float]]] = Field(..., description="Type depends on filter_type")

    @field_validator("value")
    def validate_value_for_type(v, values):
        """
        Validator for FilterTriplets.value that inspects the associated filter_type.
        Support both pydantic v1 (values: dict) and pydantic v2 (values: ValidationInfo).
        """
        # values may be a dict (pydantic v1) or ValidationInfo (pydantic v2)
        try:
            # pydantic v1 path
            filter_type = values.get("filter_type") if values is not None else None
        except AttributeError:
            # pydantic v2 ValidationInfo path: it exposes .data with the other field values
            filter_type = getattr(values, "data", {}).get("filter_type") if values is not None else None

        # Basic type checks depending on filter_type
        if filter_type in ("in", "not_in"):
            if not isinstance(v, list):
                raise ValueError("value must be a list when filter_type is 'in' or 'not_in'")
        else:
            if isinstance(v, list):
                raise ValueError("value must not be a list for this filter_type")

        return v


# Global cache for graph schema
_GRAPH_SCHEMA_CACHE = None


def get_graph_schema(connector):
    """
    Query and cache the actual graph structure from Neo4j.
    Returns a dictionary mapping node label pairs to their relationship info.
    
    Returns:
        Dict with structure:
        {
            ('NodeA', 'NodeB'): [
                {'rel_type': 'RELATIONSHIP_TYPE', 'direction': 'outgoing|incoming|both'},
                ...
            ],
            ...
        }
    """
    global _GRAPH_SCHEMA_CACHE
    
    if _GRAPH_SCHEMA_CACHE is not None:
        return _GRAPH_SCHEMA_CACHE
    
    if connector is None:
        logging.warning("No connector provided to get_graph_schema, cannot cache schema")
        return {}
    
    try:
        # Query the actual graph structure
        schema_query = """
        MATCH (a)-[r]->(b)
        WITH DISTINCT labels(a) as source_labels, type(r) as rel_type, labels(b) as target_labels
        UNWIND source_labels as source_label
        UNWIND target_labels as target_label
        RETURN DISTINCT source_label, rel_type, target_label
        """
        
        result = connector.execute_query(schema_query)
        
        schema = {}
        for record in result:
            source = record['source_label']
            rel_type = record['rel_type']
            target = record['target_label']
            
            # Add outgoing relationship from source to target
            key_out = (source, target)
            if key_out not in schema:
                schema[key_out] = []
            schema[key_out].append({'rel_type': rel_type, 'direction': 'outgoing'})
            
            # Add incoming relationship from target to source
            key_in = (target, source)
            if key_in not in schema:
                schema[key_in] = []
            schema[key_in].append({'rel_type': rel_type, 'direction': 'incoming'})
        
        _GRAPH_SCHEMA_CACHE = schema
        logging.info(f"Graph schema cached with {len(schema)} node pair connections")
        return schema
        
    except Exception as e:
        logging.error(f"Error querying graph schema: {e}")
        return {}


def find_shortest_path_in_schema(start_node: str, end_node: str, schema: Dict, max_depth: int = 5):
    """
    Use BFS to find the shortest path between two node types using the cached schema.
    
    Args:
        start_node: Starting node label
        end_node: Ending node label
        schema: Cached graph schema from get_graph_schema()
        max_depth: Maximum path length to search
    
    Returns:
        List of tuples: [(source, target, rel_type, direction), ...] or None if no path found
    """
    if start_node == end_node:
        return None
    
    # BFS to find shortest path
    queue = deque([(start_node, [])])
    visited = {start_node}
    
    while queue:
        current_node, path = queue.popleft()
        
        if len(path) >= max_depth:
            continue
        
        # Check all possible next nodes from current node
        for (source, target), rels in schema.items():
            if source == current_node and target not in visited:
                for rel_info in rels:
                    new_path = path + [(source, target, rel_info['rel_type'], rel_info['direction'])]
                    
                    if target == end_node:
                        return new_path
                    
                    visited.add(target)
                    queue.append((target, new_path))
    
    return None


def build_path_pattern_from_schema(path_info: List[Tuple], start_var: str = 'n', end_var: str = 'm'):
    """
    Build a precise Cypher path pattern from schema path information.
    
    Args:
        path_info: List of tuples from find_shortest_path_in_schema
        start_var: Variable name for start node
        end_var: Variable name for end node
    
    Returns:
        Cypher pattern string
    """
    if not path_info:
        return None
    
    # Build pattern by connecting segments
    # Don't use separate parts and join - build as one continuous pattern
    pattern = f"({start_var})"
    current_var = start_var
    
    for idx, (source, target, rel_type, direction) in enumerate(path_info):
        next_var = end_var if idx == len(path_info) - 1 else f"p{idx}"
        
        if direction == 'outgoing':
            pattern += f"-[:{rel_type}]->({next_var})"
        elif direction == 'incoming':
            pattern += f"<-[:{rel_type}]-({next_var})"
        else:
            pattern += f"-[:{rel_type}]-({next_var})"
        
        current_var = next_var
    
    return pattern


def get_nodes_by_label(label: str, limit: int = 10):
    """
    Get all nodes for a given label from the NEO4J_ONTOLOGY.
    Determine whether `label` is a node label, relationship type, node property
    or relationship property according to the ontology and return an appropriate
    Cypher query string.
    """
    lbl = label.strip()
    if not lbl:
        logging.error("Empty label provided to get_nodes_by_label")
        return None

    for node in NEO4J_ONTOLOGY.nodes:
        # 1) Is it a node label?
        if node.node_label.lower() == lbl.lower():
            query = f"MATCH (n:{node.node_label}) RETURN n LIMIT {limit}"
            return query
        # 3) Is it a node property? (check property_name and source_column on nodes)
        for prop in node.properties:
            pname = (prop.property_name or prop.source_column or "").strip()
            if pname and pname.lower() == lbl.lower():
                query = f"MATCH (n:{node.node_label}) WHERE n.`{pname}` IS NOT NULL RETURN DISTINCT n.`{pname}` AS value LIMIT {limit}"
                return query
    
    for rel in NEO4J_ONTOLOGY.relationships:
        # 2) Is it a relationship type?
        if rel.rel_type.lower() == lbl.lower():
            query = f"MATCH (n)-[r:{rel.rel_type}]-(m) RETURN r LIMIT {limit}"
            return query
            # 4) Is it a relationship property? (check property_name and source_column on relationships)
        for prop in rel.properties:
            pname = (prop.property_name or prop.source_column or "").strip()
            if pname and pname.lower() == lbl.lower():
                # match any relationship that has this property
                query = f"MATCH ()-[r:{rel.rel_type}]-() WHERE r.`{pname}` IS NOT NULL RETURN DISTINCT r.`{pname}` AS value LIMIT {limit}"
                return query       

    logging.error(f"Label '{label}' not found in NEO4J_ONTOLOGY")
    return None


def find_in_ontology(lbl: str):
    key = lbl.strip().lower()
    
    for node in NEO4J_ONTOLOGY.nodes:
        # 1) node label
        if getattr(node, "node_label", "").strip().lower() == key:
            return "node", node
        # 3) node property
        for prop in getattr(node, "properties", []) or []:
            pname = (getattr(prop, "property_name", None) or getattr(prop, "source_column", "")).strip().lower()
            if pname == key:
                return "node_property", (node, prop)
    
    for rel in NEO4J_ONTOLOGY.relationships:
        # 2) relationship type
        if getattr(rel, "rel_type", "").strip().lower() == key:
            return "relationship", rel
        # 4) relationship property
        for prop in getattr(rel, "properties", []) or []:
            pname = (getattr(prop, "property_name", None) or getattr(prop, "source_column", "")).strip().lower()
            if pname == key:
                return "relationship_property", (rel, prop)
    return None, None

def get_relationships_from_ontology(from_node: str, to_node: str) -> List[str]:
    """
    Get all relationship types connecting two nodes from the ontology.
    
    Args:
        from_node: Source node label
        to_node: Target node label
    
    Returns:
        List of relationship types (may be empty if no direct connection)
    """
    rel_types = []
    for rel in NEO4J_ONTOLOGY.relationships:
        if rel.from_node == from_node and rel.to_node == to_node:
            rel_types.append(rel.rel_type)
    return rel_types


def build_path_from_ontology(start_node: str, end_node: str, max_depth: int = 4) -> Optional[str]:
    """
    Build a path pattern from start_node to end_node using ontology relationships.
    Uses BFS to find shortest path.
    
    Args:
        start_node: Starting node label
        end_node: Target node label
        max_depth: Maximum path length
    
    Returns:
        Cypher path pattern or None if no path found
    """
    if start_node == end_node:
        return None
    
    # Direct connection check
    direct_rels = get_relationships_from_ontology(start_node, end_node)
    if direct_rels:
        rel_str = "|".join(direct_rels)
        return f"-[:{rel_str}]->"
    
    # BFS to find path through ontology
    from collections import deque
    queue = deque([(start_node, [])])
    visited = {start_node}
    
    while queue:
        current, path = queue.popleft()
        
        if len(path) >= max_depth:
            continue
        
        # Check all relationships from current node
        for rel in NEO4J_ONTOLOGY.relationships:
            if rel.from_node == current and rel.to_node not in visited:
                new_path = path + [(rel.from_node, rel.rel_type, rel.to_node)]
                
                if rel.to_node == end_node:
                    # Build pattern from path
                    return _build_pattern_from_path(new_path)
                
                visited.add(rel.to_node)
                queue.append((rel.to_node, new_path))
    
    return None


def _build_pattern_from_path(path: List[Tuple[str, str, str]]) -> str:
    """
    Build Cypher pattern from a path of (from_node, rel_type, to_node) tuples.
    Returns pattern like: -[:REL1]->(:Node1)-[:REL2]->
    """
    if not path:
        return ""
    
    pattern_parts = []
    for i, (from_node, rel_type, to_node) in enumerate(path):
        pattern_parts.append(f"-[:{rel_type}]->")
        if i < len(path) - 1:  # Add intermediate node
            pattern_parts.append(f"(:{to_node})")
    
    return "".join(pattern_parts)


def can_use_product_centric_optimization(ref_label: str, filters: List[FilterTriplets], schema: Dict) -> bool:
    """
    Check if Product-centric optimization can be applied.
    Returns True if ref_label connects to Product and all filters can be applied through Product.
    """
    if not filters or len(filters) < 2:
        return False
    
    ref_rep, ref_obj = find_in_ontology(ref_label)
    if not ref_rep:
        return False
    
    # Get reference node label
    if ref_rep == "node":
        ref_node_label = getattr(ref_obj, "node_label", ref_label)
    elif ref_rep == "node_property":
        ref_node_label = getattr(ref_obj[0], "node_label", "")
    else:
        # Relationships not supported for product-centric optimization
        return False
    
    # Check if reference and all filters connect to Product using ontology
    def connects_to_product(node_label: str) -> bool:
        """Check if a node has direct or multi-hop connection to Product."""
        if node_label == "Product":
            return True
        # Check direct connections
        if get_relationships_from_ontology(node_label, "Product") or \
           get_relationships_from_ontology("Product", node_label):
            return True
        # Check multi-hop (up to 4 hops)
        return build_path_from_ontology("Product", node_label, max_depth=4) is not None
    
    if not connects_to_product(ref_node_label):
        return False
    
    # Check if all filters can connect through Product
    for filt in filters:
        f_rep, f_obj = find_in_ontology(filt.field)
        if not f_rep:
            return False
        
        # Get filter node label
        if f_rep == "node":
            filter_label = getattr(f_obj, "node_label", "")
            if not connects_to_product(filter_label):
                return False
        elif f_rep == "node_property":
            filter_label = getattr(f_obj[0], "node_label", "")
            if not connects_to_product(filter_label):
                return False
        elif f_rep == "relationship":
            # Check if relationship involves Product
            rel_from = getattr(f_obj, "from_node", None)
            rel_to = getattr(f_obj, "to_node", None)
            if "Product" not in [rel_from, rel_to]:
                return False
        elif f_rep == "relationship_property":
            # Check if relationship involves Product
            rel_from = getattr(f_obj[0], "from_node", None)
            rel_to = getattr(f_obj[0], "to_node", None)
            if "Product" not in [rel_from, rel_to]:
                return False
        else:
            return False
    
    return True


def build_product_centric_query(
    ref_label: str,
    filters: List[FilterTriplets],
    limit: int,
    logical_operator: str,
    schema: Dict
) -> Tuple[str, Dict[str, Any]]:
    """
    Build optimized Product-centric query.
    Applies filters as pipeline stages starting from Product to progressively narrow results.
    """
    logging.info(f"\n{'='*60}")
    logging.info(f"BUILDING PRODUCT-CENTRIC OPTIMIZED QUERY")
    logging.info(f"{'='*60}")
    
    ref_rep, ref_obj = find_in_ontology(ref_label)
    params = {}
    stages = []
    
    # Determine reference node label
    if ref_rep == "node":
        ref_node_label = getattr(ref_obj, "node_label", ref_label)
    elif ref_rep == "node_property":
        ref_node_label = getattr(ref_obj[0], "node_label", "")
        canonical_ref = (getattr(ref_obj[1], "property_name", None) or 
                        getattr(ref_obj[1], "source_column", ref_label))
    else:
        ref_node_label = None
    
    # Stage 0: Start with Product
    stages.append("MATCH (p:Product)")
    logging.info("Stage 0: MATCH (p:Product)")
    
    # Build filter stages - each filter progressively narrows Product set
    for idx, filt in enumerate(filters):
        logging.info(f"\nProcessing filter {idx}: {filt.field} {filt.filter_type} {filt.value}")
        
        f_rep, f_obj = find_in_ontology(filt.field)
        if not f_rep:
            logging.warning(f"Filter field '{filt.field}' not found, skipping")
            continue
        
        # Extract filter metadata
        filter_prop_name = None
        filter_prop_type = None
        filter_node_label = None
        filter_rel_type = None
        filter_rel_from = None
        filter_rel_to = None
        
        if f_rep == "node":
            filter_node_label = getattr(f_obj, "node_label", filt.field)
        elif f_rep == "node_property":
            parent_f_node, fprop = f_obj
            filter_prop_name = (getattr(fprop, "property_name", None) or 
                               getattr(fprop, "source_column", "")).strip()
            filter_prop_type = getattr(fprop, "type", None)
            filter_node_label = getattr(parent_f_node, "node_label", "")
        elif f_rep == "relationship":
            # Relationship filter (e.g., checking for ALLERGENS existence)
            filter_rel_type = getattr(f_obj, "rel_type", filt.field)
            filter_rel_from = getattr(f_obj, "from_node", None)
            filter_rel_to = getattr(f_obj, "to_node", None)
        elif f_rep == "relationship_property":
            parent_f_rel, fprop = f_obj
            filter_prop_name = (getattr(fprop, "property_name", None) or 
                               getattr(fprop, "source_column", "")).strip()
            filter_prop_type = getattr(fprop, "type", None)
            # Get the relationship details from ontology
            filter_rel_from = getattr(parent_f_rel, "from_node", None)
            filter_rel_to = getattr(parent_f_rel, "to_node", None)
            filter_rel_type = getattr(parent_f_rel, "rel_type", None)
        
        # Build stage pattern and WHERE clause
        stage_match = None
        stage_where = None
        prop_expr = None
        param_name = f"p{idx}"
        
        # Handle relationship filters (e.g., ALLERGENS existence check)
        if f_rep == "relationship":
            # Check if this is a relationship existence check
            is_existence_check = (filt.filter_type == FilterTypes.EQUAL and 
                                 str(filt.value).upper() == filter_rel_type.upper())
            
            if is_existence_check:
                # For existence check, just match the specific relationship
                if filter_rel_from == "Product":
                    stage_match = f"MATCH (p)-[:{filter_rel_type}]->({filter_rel_to.lower()}{idx}:{filter_rel_to})"
                elif filter_rel_to == "Product":
                    stage_match = f"MATCH ({filter_rel_from.lower()}{idx}:{filter_rel_from})-[:{filter_rel_type}]->(p)"
                else:
                    stage_match = f"MATCH (p)-[:{filter_rel_type}]-({filter_rel_to.lower()}{idx})"
                prop_expr = None  # No property comparison needed
                logging.info(f"  Relationship existence check: {stage_match}")
            else:
                # For value comparison, match connected node and compare its property
                var_name = filter_rel_to.lower()[:4] + str(idx)
                if filter_rel_from == "Product":
                    stage_match = f"MATCH (p)-[:{filter_rel_type}]->({var_name}:{filter_rel_to})"
                elif filter_rel_to == "Product":
                    stage_match = f"MATCH ({var_name}:{filter_rel_from})-[:{filter_rel_type}]->(p)"
                else:
                    stage_match = f"MATCH (p)-[:{filter_rel_type}]-({var_name})"
                prop_expr = f"{var_name}.name"
                logging.info(f"  Relationship with property filter: {stage_match}")
        
        # Handle relationship properties (e.g., price on SELLS)
        elif f_rep == "relationship_property":
            # Find which node connects via this relationship to Product
            if filter_rel_to == "Product":
                # Relationship points TO Product (e.g., Store-[:SELLS]->Product)
                stage_match = f"MATCH ({filter_rel_from.lower()}{idx}:{filter_rel_from})-[{filter_rel_type.lower()}{idx}:{filter_rel_type}]->(p)"
                prop_expr = f"{filter_rel_type.lower()}{idx}.`{filter_prop_name}`"
            elif filter_rel_from == "Product":
                # Relationship from Product (e.g., Product-[:HAS_OFFER]->Offer)
                stage_match = f"MATCH (p)-[{filter_rel_type.lower()}{idx}:{filter_rel_type}]->({filter_rel_to.lower()}{idx}:{filter_rel_to})"
                prop_expr = f"{filter_rel_type.lower()}{idx}.`{filter_prop_name}`"
            logging.info(f"  Relationship property: {stage_match}")
        
        # Handle node filters - use ontology to build path
        elif filter_node_label and filter_node_label != "Product":
            var_name = filter_node_label.lower()[:4] + str(idx)  # e.g., "brand0", "icat1"
            
            # Check for incoming relationships (to Product)
            incoming_rels = get_relationships_from_ontology(filter_node_label, "Product")
            if incoming_rels:
                rel_str = "|".join(incoming_rels)
                stage_match = f"MATCH ({var_name}:{filter_node_label})-[:{rel_str}]->(p)"
                prop_expr = f"{var_name}.`{filter_prop_name}`" if filter_prop_name else f"{var_name}.name"
                logging.info(f"  Incoming relationship: {stage_match}")
            else:
                # Check for outgoing relationships (from Product)
                outgoing_rels = get_relationships_from_ontology("Product", filter_node_label)
                if outgoing_rels:
                    rel_str = "|".join(outgoing_rels)
                    stage_match = f"MATCH (p)-[:{rel_str}]->({var_name}:{filter_node_label})"
                    prop_expr = f"{var_name}.`{filter_prop_name}`" if filter_prop_name else f"{var_name}.name"
                    logging.info(f"  Outgoing relationship: {stage_match}")
                else:
                    # Try to find multi-hop path
                    path_pattern = build_path_from_ontology("Product", filter_node_label)
                    if path_pattern:
                        stage_match = f"MATCH (p){path_pattern}({var_name}:{filter_node_label})"
                        prop_expr = f"{var_name}.`{filter_prop_name}`" if filter_prop_name else f"{var_name}.name"
                        logging.info(f"  Multi-hop path: {stage_match}")
                    else:
                        logging.warning(f"No path found from Product to {filter_node_label}")
                        continue
        
        # Product property filter
        elif filter_node_label == "Product" and filter_prop_name:
            stage_match = None  # No additional MATCH needed
            prop_expr = f"p.`{filter_prop_name}`"
            logging.info(f"  Product property: {prop_expr}")
        
        # Fallback for node properties without explicit label
        elif f_rep == "node_property" and not stage_match and filter_prop_name:
            stage_match = None
            prop_expr = f"p.`{filter_prop_name}`"
            logging.info(f"  Assumed Product property: {prop_expr}")
        
        else:
            logging.warning(f"Unsupported filter type for product-centric: {filter_node_label or f_rep}")
            continue
        
        # For relationship existence checks, prop_expr is None - no WHERE clause needed
        if not prop_expr:
            if f_rep == "relationship" and filt.filter_type == FilterTypes.EQUAL:
                logging.info(f"  Relationship existence check - no WHERE clause needed")
                # Add stage to pipeline without WHERE
                if stage_match:
                    stages.append(stage_match)
                stages.append("WITH DISTINCT p")
                logging.info(f"  Stage {idx+1}: {stage_match or 'Relationship existence'}")
                continue
            else:
                logging.warning(f"No property expression determined for filter {idx}: {filt.field}")
                continue
        
        # Build WHERE clause based on filter_type
        ft = filt.filter_type
        
        if ft == FilterTypes.EQUAL:
            params[param_name] = filt.value
            prop_type_str = (str(getattr(filter_prop_type, "value", filter_prop_type)).lower() 
                           if filter_prop_type else "")
            is_numeric = prop_type_str in ("float", "int", "integer", "numeric") or isinstance(filt.value, (int, float))
            
            if is_numeric:
                stage_where = f"toFloat({prop_expr}) = toFloat(${param_name})"
            else:
                stage_where = f"toLower(coalesce(toString({prop_expr}), '')) = toLower(${param_name})"
        
        elif ft == FilterTypes.CONTAINS:
            params[param_name] = str(filt.value)
            stage_where = f"toLower(coalesce(toString({prop_expr}), '')) CONTAINS toLower(${param_name})"
        
        elif ft == FilterTypes.IN:
            params[param_name] = filt.value if isinstance(filt.value, list) else [filt.value]
            stage_where = f"{prop_expr} IN ${param_name}"
        
        elif ft == FilterTypes.GREATER_THAN:
            params[param_name] = filt.value
            stage_where = f"toFloat({prop_expr}) > toFloat(${param_name})"
        
        elif ft == FilterTypes.LESS_THAN:
            params[param_name] = filt.value
            stage_where = f"toFloat({prop_expr}) < toFloat(${param_name})"
        
        elif ft == FilterTypes.BETWEEN:
            if isinstance(filt.value, (list, tuple)):
                v1, v2 = filt.value[0], filt.value[1]
            else:
                parts = str(filt.value).split(",")
                v1, v2 = parts[0].strip(), parts[1].strip()
            param_name1, param_name2 = f"{param_name}_1", f"{param_name}_2"
            params[param_name1], params[param_name2] = v1, v2
            stage_where = f"toFloat({prop_expr}) >= toFloat(${param_name1}) AND toFloat({prop_expr}) <= toFloat(${param_name2})"
        
        # Add stage to pipeline
        if stage_match:
            stages.append(stage_match)
        if stage_where:
            stages.append(f"WHERE {stage_where}")
        stages.append("WITH DISTINCT p")
        
        logging.info(f"  Stage {idx+1}: {stage_match or 'Product property filter'}")
        logging.info(f"  WHERE: {stage_where}")
    
    # Final stage: Navigate from Product to target entity using ontology
    if ref_node_label == "Product":
        if ref_rep == "node":
            stages.append(f"RETURN DISTINCT p AS m LIMIT {limit}")
        else:  # node_property
            stages.append(f"RETURN DISTINCT p.`{canonical_ref}` AS value LIMIT {limit}")
    
    elif ref_node_label:
        # Use ontology to determine navigation path
        # Check for incoming relationships (to Product)
        incoming_rels = get_relationships_from_ontology(ref_node_label, "Product")
        if incoming_rels:
            rel_str = "|".join(incoming_rels)
            stages.append(f"MATCH (m:{ref_node_label})-[:{rel_str}]->(p)")
            if ref_rep == "node":
                stages.append(f"RETURN DISTINCT m LIMIT {limit}")
            else:
                stages.append(f"RETURN DISTINCT m.`{canonical_ref}` AS value LIMIT {limit}")
        else:
            # Check for outgoing relationships (from Product)
            outgoing_rels = get_relationships_from_ontology("Product", ref_node_label)
            if outgoing_rels:
                rel_str = "|".join(outgoing_rels)
                stages.append(f"MATCH (p)-[:{rel_str}]->(m:{ref_node_label})")
                if ref_rep == "node":
                    stages.append(f"RETURN DISTINCT m LIMIT {limit}")
                else:
                    stages.append(f"RETURN DISTINCT m.`{canonical_ref}` AS value LIMIT {limit}")
            else:
                # Try multi-hop path
                path_pattern = build_path_from_ontology("Product", ref_node_label)
                if path_pattern:
                    stages.append(f"MATCH (p){path_pattern}(m:{ref_node_label})")
                    if ref_rep == "node":
                        stages.append(f"RETURN DISTINCT m LIMIT {limit}")
                    else:
                        stages.append(f"RETURN DISTINCT m.`{canonical_ref}` AS value LIMIT {limit}")
                else:
                    # Fallback: return products
                    logging.warning(f"No path found from Product to {ref_node_label}, returning products")
                    stages.append(f"RETURN DISTINCT p AS m LIMIT {limit}")
    
    else:
        # Fallback: if ref_node_label not determined, return products
        logging.warning(f"Reference label could not be determined, returning products")
        stages.append(f"RETURN DISTINCT p AS m LIMIT {limit}")
    
    query = "\n".join(stages)
    
    logging.info(f"\n{'='*60}")
    logging.info(f"PRODUCT-CENTRIC OPTIMIZED QUERY:")
    logging.info(f"{'='*60}")
    logging.info(f"\n{query}\n")
    logging.info(f"Parameters: {params}")
    logging.info(f"{'='*60}\n")
    
    return query, params


def find_path_between_entities(start_label: str, end_label: str, max_depth: int = 5, schema: Dict = None):
    """
    Find if there's a path between two entities in the ontology.
    Uses cached graph schema for precise path patterns instead of variable-length paths.
    
    Args:
        start_label: Starting entity (node/relationship label or property name)
        end_label: Target entity (node/relationship label or property name)
        max_depth: Maximum relationship depth to search
        schema: Cached graph schema (optional, will use global cache if not provided)
    
    Returns:
        Tuple of (path_pattern, start_var, end_var, is_relationship_target) or (None, None, None, False)
    """
    start_rep, start_obj = find_in_ontology(start_label)
    end_rep, end_obj = find_in_ontology(end_label)
    
    if not start_rep or not end_rep:
        return None, None, None, False
    
    is_rel_target = False
    
    # Get node labels for both entities
    if start_rep == "node":
        start_node_label = getattr(start_obj, "node_label", start_label)
    elif start_rep == "node_property":
        start_node_label = getattr(start_obj[0], "node_label", "")
    else:
        return None, None, None, False  # Can't start from relationship
    
    if end_rep == "node":
        end_node_label = getattr(end_obj, "node_label", end_label)
    elif end_rep == "node_property":
        end_node_label = getattr(end_obj[0], "node_label", "")
    elif end_rep == "relationship":
        # Target is a relationship - need to find path to nodes connected by this relationship
        end_rel_type = getattr(end_obj, "rel_type", end_label)
        # Find which node labels are connected by this relationship
        rel_from = getattr(end_obj, "from_node", None)
        rel_to = getattr(end_obj, "to_node", None)
        
        # Check if start node directly participates in this relationship
        if start_node_label in (rel_from, rel_to):
            path_pattern = f"(m)-[r:{end_rel_type}]-()"
            return path_pattern, "m", "r", True
        
        # Otherwise, need to find path to a node that participates in this relationship
        # Try to find path to either from_node or to_node
        if schema:
            for target_node in [rel_from, rel_to]:
                if target_node:
                    path_info = find_shortest_path_in_schema(start_node_label, target_node, schema, max_depth)
                    if path_info:
                        pattern = build_path_pattern_from_schema(path_info, 'n', 'p')
                        if pattern:
                            pattern = pattern.replace('(n)', f'(n:{start_node_label})', 1)
                            # Add the relationship at the end
                            pattern = f"{pattern}-[r:{end_rel_type}]-()"
                            return pattern, "n", "r", True
        
        # Fallback: use variable-length path to find connection
        path_pattern = f"(n:{start_node_label})-[*1..{max_depth}]-(p)-[r:{end_rel_type}]-()"
        return path_pattern, "n", "r", True
        
    elif end_rep == "relationship_property":
        # Target is a relationship property
        parent_rel, _ = end_obj
        end_rel_type = getattr(parent_rel, "rel_type", "")
        rel_from = getattr(parent_rel, "from_node", None)
        rel_to = getattr(parent_rel, "to_node", None)
        
        # Check if start node directly participates in this relationship
        if start_node_label in (rel_from, rel_to):
            path_pattern = f"(m)-[r:{end_rel_type}]-()"
            return path_pattern, "m", "r", True
        
        # Otherwise, need to find path to a node that participates in this relationship
        if schema:
            for target_node in [rel_from, rel_to]:
                if target_node:
                    path_info = find_shortest_path_in_schema(start_node_label, target_node, schema, max_depth)
                    if path_info:
                        pattern = build_path_pattern_from_schema(path_info, 'n', 'p')
                        if pattern:
                            pattern = pattern.replace('(n)', f'(n:{start_node_label})', 1)
                            # Add the relationship at the end
                            pattern = f"{pattern}-[r:{end_rel_type}]-()"
                            return pattern, "n", "r", True
        
        # Fallback: use variable-length path to find connection
        path_pattern = f"(n:{start_node_label})-[*1..{max_depth}]-(p)-[r:{end_rel_type}]-()"
        return path_pattern, "n", "r", True
    else:
        return None, None, None, False
    
    if start_node_label == end_node_label:
        return None, None, None, False  # Same node type, should be handled differently
    
    # Use cached schema to find precise path
    if schema is None:
        schema = _GRAPH_SCHEMA_CACHE or {}
    
    if schema:
        path_info = find_shortest_path_in_schema(start_node_label, end_node_label, schema, max_depth)
        if path_info:
            pattern = build_path_pattern_from_schema(path_info, 'n', 'm')
            if pattern:
                # Add node labels to start and end
                pattern = pattern.replace('(n)', f'(n:{start_node_label})', 1)
                # Don't add label to 'm' as it's already defined in base MATCH
                logging.info(f"Using precise path from schema: {pattern}")
                return pattern, "n", "m", False
    
    # Fallback to variable-length path if schema not available
    # Use max_depth of 5 to handle paths like Internal_Category -> Brand (4 hops)
    logging.warning(f"Schema not available, using variable-length path (slower)")
    path_pattern = f"(n:{start_node_label})-[*1..{max_depth}]-(m)"
    return path_pattern, "n", "m", False


def generate_query_for_filter_triplets(
    ref_label: str,
    filters: List[FilterTriplets],
    limit: int = 10,
    logical_operator: str = "AND",
    connector = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Generalized generator for Cypher queries from filter triplets.
    Returns nodes matching the reference label with applied filters.
    If ref_label is a property, returns only the property value.
    Supports filtering by entities not directly connected through path traversal.
    """
    logging.info(f"\n{'='*60}")
    logging.info(f"GENERATING QUERY FOR FILTER TRIPLETS")
    logging.info(f"{'='*60}")
    logging.info(f"ref_label: {ref_label}")
    logging.info(f"limit: {limit}")
    logging.info(f"logical_operator: {logical_operator}")
    logging.info(f"Number of filters: {len(filters)}")
    for i, f in enumerate(filters):
        logging.info(f"  Filter {i}: field={f.field}, type={f.filter_type}, value={f.value}")
    
    # Initialize schema cache if connector provided
    schema = None
    if connector is not None:
        schema = get_graph_schema(connector)
        logging.info(f"Using cached graph schema with {len(schema)} connections")
    else:
        logging.warning("No connector provided, will use variable-length paths (slower)")
    
    # Check if Product-centric optimization can be applied
    if (len(filters) >= 1 and 
        logical_operator.upper() == "AND" and 
        schema and 
        can_use_product_centric_optimization(ref_label, filters, schema)):
        logging.info("✓✓✓ USING PRODUCT-CENTRIC OPTIMIZATION ✓✓✓")
        return build_product_centric_query(ref_label, filters, limit, logical_operator, schema)
    
    # Determine reference label type and canonical name
    ref_rep, ref_obj = find_in_ontology(ref_label)
    if not ref_rep:
        logging.error(f"Reference label '{ref_label}' not found in ontology")
        return "", {}
    
    logging.info(f"\nReference label analysis:")
    logging.info(f"  Representation: {ref_rep}")
    
    # Get canonical reference name from ontology
    ref_node_label = None
    ref_rel_type = None
    
    if ref_rep == "node":
        canonical_ref = getattr(ref_obj, "node_label", ref_label)
        logging.info(f"  Node label: {canonical_ref}")
    elif ref_rep == "relationship":
        canonical_ref = getattr(ref_obj, "rel_type", ref_label)
        logging.info(f"  Relationship type: {canonical_ref}")
    elif ref_rep == "node_property":
        parent_node, prop = ref_obj
        canonical_ref = (getattr(prop, "property_name", None) or 
                        getattr(prop, "source_column", ref_label))
        ref_node_label = getattr(parent_node, "node_label", "")
        logging.info(f"  Property name: {canonical_ref}")
        logging.info(f"  Parent node: {ref_node_label}")
    elif ref_rep == "relationship_property":
        parent_rel, prop = ref_obj
        canonical_ref = (getattr(prop, "property_name", None) or 
                        getattr(prop, "source_column", ref_label))
        ref_rel_type = getattr(parent_rel, "rel_type", "")
        logging.info(f"  Property name: {canonical_ref}")
        logging.info(f"  Parent relationship: {ref_rel_type}")

    # Initialize schema cache if connector provided
    schema = None
    if connector is not None:
        schema = get_graph_schema(connector)
        logging.info(f"Using cached graph schema with {len(schema)} connections")
    else:
        logging.warning("No connector provided, will use variable-length paths (slower)")

    match_clauses: List[str] = []
    where_clauses: List[str] = []
    params: Dict[str, Any] = {}
    
    # Track used variables to avoid conflicts
    used_vars = {'m'}  # 'm' is always used for the reference node

    # Base MATCH clause for reference label
    if ref_rep == "node":
        base_match = f"MATCH (m:{canonical_ref})"
    elif ref_rep == "relationship":
        base_match = f"MATCH (m1)-[m:{canonical_ref}]-(m2)"
        used_vars.update(['m1', 'm2'])
    elif ref_rep == "node_property":
        base_match = f"MATCH (m:{ref_node_label})"
        where_clauses.append(f"m.`{canonical_ref}` IS NOT NULL")
    elif ref_rep == "relationship_property":
        base_match = f"MATCH ()-[m:{ref_rel_type}]-()"
        where_clauses.append(f"m.`{canonical_ref}` IS NOT NULL")
    
    match_clauses.append(base_match)
    logging.info(f"\nBase MATCH clause: {base_match}")

    # Process each filter
    for idx, filt in enumerate(filters):
        logging.info(f"\n{'-'*60}")
        logging.info(f"PROCESSING FILTER {idx}")
        logging.info(f"{'-'*60}")
        logging.info(f"Field: {filt.field}")
        logging.info(f"Filter type: {filt.filter_type}")
        logging.info(f"Value: {filt.value}")
        
        f_rep, f_obj = find_in_ontology(filt.field)
        if not f_rep:
            logging.error(f"❌ Filter field '{filt.field}' not found in ontology; SKIPPING")
            continue

        logging.info(f"Filter field representation: {f_rep}")

        # Extract filter property metadata
        filter_prop_name = None
        filter_prop_type = None
        filter_node_label = None
        filter_rel_type = None
        
        if f_rep == "node":
            filter_label = getattr(f_obj, "node_label", filt.field)
            filter_node_label = filter_label
            logging.info(f"  → Filter is a NODE with label: {filter_node_label}")
        elif f_rep == "relationship":
            filter_label = getattr(f_obj, "rel_type", filt.field)
            filter_rel_type = filter_label
            logging.info(f"  → Filter is a RELATIONSHIP with type: {filter_rel_type}")
        elif f_rep == "node_property":
            parent_f_node, fprop = f_obj
            filter_prop_name = (getattr(fprop, "property_name", None) or 
                               getattr(fprop, "source_column", "")).strip()
            filter_prop_type = getattr(fprop, "type", None)
            filter_label = getattr(parent_f_node, "node_label", "")
            filter_node_label = filter_label
            logging.info(f"  → Filter is a NODE_PROPERTY")
            logging.info(f"     Property name: {filter_prop_name}")
            logging.info(f"     Parent node: {filter_node_label}")
            logging.info(f"     Property type: {filter_prop_type}")
        elif f_rep == "relationship_property":
            parent_f_rel, fprop = f_obj
            filter_prop_name = (getattr(fprop, "property_name", None) or 
                               getattr(fprop, "source_column", "")).strip()
            filter_prop_type = getattr(fprop, "type", None)
            filter_label = getattr(parent_f_rel, "rel_type", "")
            filter_rel_type = filter_label
            logging.info(f"  → Filter is a RELATIONSHIP_PROPERTY")
            logging.info(f"     Property name: {filter_prop_name}")
            logging.info(f"     Parent relationship: {filter_rel_type}")
            logging.info(f"     Property type: {filter_prop_type}")

        # Generate unique variable names for this filter
        var_prefix = f"f{idx}"
        filter_node_var = f"n{idx}" if f"n{idx}" not in used_vars else f"{var_prefix}_n"
        filter_rel_var = f"r{idx}" if f"r{idx}" not in used_vars else f"{var_prefix}_r"
        used_vars.update([filter_node_var, filter_rel_var])

        # Build filter expression based on ref_label and field combination
        prop_expr = None
        needs_path = False
        
        logging.info(f"\nBuilding filter expression...")
        logging.info(f"  Reference type: {ref_rep}")
        logging.info(f"  Filter type: {f_rep}")
        
        # REF: NODE
        if ref_rep == "node":
            if f_rep == "node":
                logging.info(f"  Case: REF=NODE, FILTER=NODE")
                if filter_node_label != canonical_ref:
                    logging.info(f"  Different node types: {filter_node_label} vs {canonical_ref}")
                    logging.info(f"  Looking for path from {filt.field} to {ref_label}...")
                    path_pattern, start_var, end_var, is_rel_target = find_path_between_entities(filt.field, ref_label, schema=schema)
                    logging.info(f"  Path result: {path_pattern}")
                    
                    if path_pattern:
                        # Replace generic variables with filter-specific ones
                        path_pattern = path_pattern.replace("(n:", f"({filter_node_var}:")
                        match_clauses.append(f"MATCH {path_pattern}")
                        prop_expr = f"{filter_node_var}.name"
                        needs_path = True
                        logging.info(f"  ✓ Using path pattern")
                    else:
                        match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[]-(m)")
                        prop_expr = f"{filter_node_var}.name"
                        logging.info(f"  ✓ Using direct connection")
                    logging.info(f"  ✓ Property expression: {prop_expr}")
                else:
                    prop_expr = "m.name"
                    logging.info(f"  Same node type, using m.name")
                    
            elif f_rep == "relationship":
                logging.info(f"  Case: REF=NODE, FILTER=RELATIONSHIP")
                # Get target node from ontology to build proper pattern
                rel_obj = f_obj
                rel_from = getattr(rel_obj, "from_node", None)
                rel_to = getattr(rel_obj, "to_node", None)
                
                # Check if this is a relationship existence check (value == relationship type name)
                is_existence_check = (filt.filter_type == FilterTypes.EQUAL and 
                                     str(filt.value).upper() == filter_rel_type.upper())
                
                if is_existence_check:
                    # For existence check, just verify the relationship exists (no prop_expr needed)
                    if rel_from == canonical_ref:
                        match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]->(:{rel_to})")
                    elif rel_to == canonical_ref:
                        match_clauses.append(f"MATCH (:{rel_from})-[{filter_rel_var}:{filter_rel_type}]->(m)")
                    else:
                        match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]-()")
                    prop_expr = None  # No property to compare, existence is enough
                    logging.info(f"  ✓ Relationship existence check (no property comparison needed)")
                else:
                    # For value comparison, match the connected node and compare its property
                    if rel_from == canonical_ref:
                        match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]->({filter_node_var}:{rel_to})")
                        prop_expr = f"{filter_node_var}.name"
                    elif rel_to == canonical_ref:
                        match_clauses.append(f"MATCH ({filter_node_var}:{rel_from})-[{filter_rel_var}:{filter_rel_type}]->(m)")
                        prop_expr = f"{filter_node_var}.name"
                    else:
                        match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]-({filter_node_var})")
                        prop_expr = f"{filter_node_var}.name"
                    logging.info(f"  ✓ Property expression: {prop_expr}")
                
            elif f_rep == "node_property":
                logging.info(f"  Case: REF=NODE, FILTER=NODE_PROPERTY")
                if filter_node_label and filter_node_label != canonical_ref:
                    logging.info(f"  Different node types: {filter_node_label} vs {canonical_ref}")
                    logging.info(f"  Looking for path from {filt.field} to {ref_label}...")
                    path_pattern, start_var, end_var, is_rel_target = find_path_between_entities(filt.field, ref_label, schema=schema)
                    logging.info(f"  Path result: {path_pattern}")
                    
                    if path_pattern:
                        # Replace generic variables with filter-specific ones
                        path_pattern = path_pattern.replace("(n:", f"({filter_node_var}:")
                        match_clauses.append(f"MATCH {path_pattern}")
                        prop_expr = f"{filter_node_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_node_var}.name"
                        needs_path = True
                        logging.info(f"  ✓ Using path pattern")
                    else:
                        match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[]-(m)")
                        prop_expr = f"{filter_node_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_node_var}.name"
                        logging.info(f"  ✓ Using direct connection")
                    logging.info(f"  ✓ Property expression: {prop_expr}")
                else:
                    prop_expr = f"m.`{filter_prop_name}`" if filter_prop_name else "m.name"
                    logging.info(f"  Same node type, property expression: {prop_expr}")
                    
            elif f_rep == "relationship_property":
                logging.info(f"  Case: REF=NODE, FILTER=RELATIONSHIP_PROPERTY")
                logging.info(f"  Looking for path from {ref_label} to {filt.field}...")
                path_pattern, start_var, end_var, is_rel_target = find_path_between_entities(ref_label, filt.field, schema=schema)
                logging.info(f"  Path result: {path_pattern}")
                
                if path_pattern and is_rel_target:
                    # Replace generic variables with filter-specific ones
                    # Replace all intermediate p variables with filter-specific ones to avoid conflicts
                    import re
                    if start_var == 'n':
                        # Pattern like (n:Brand)-...(p0)-...(p)-[r:SELLS]-() 
                        # needs unique variables for this filter
                        path_pattern = path_pattern.replace(f"({start_var}:", "(m:")
                        # Replace intermediate p0, p1, etc. with unique variables
                        for i in range(10):  # Support up to 10 intermediate nodes
                            path_pattern = path_pattern.replace(f"(p{i})", f"(pf{idx}_{i})")
                        # Replace the final (p) with unique variable
                        path_pattern = re.sub(r'\(p\)(?!f)', f'(pf{idx}_x)', path_pattern)
                    path_pattern = path_pattern.replace("-[r:", f"-[{filter_rel_var}:")
                    match_clauses.append(f"MATCH {path_pattern}")
                    prop_expr = f"{filter_rel_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_rel_var}.name"
                    logging.info(f"  ✓ Using path to relationship")
                else:
                    match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]-()")
                    prop_expr = f"{filter_rel_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_rel_var}.name"
                    logging.info(f"  ✓ Using direct connection to relationship")
                logging.info(f"  ✓ Property expression: {prop_expr}")
        
        # REF: RELATIONSHIP
        elif ref_rep == "relationship":
            if f_rep == "node":
                logging.info(f"  Case: REF=RELATIONSHIP, FILTER=NODE")
                match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[m]-()")
                prop_expr = f"{filter_node_var}.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "relationship":
                logging.info(f"  Case: REF=RELATIONSHIP, FILTER=RELATIONSHIP")
                # For relationship to relationship filtering, match the other relationship
                match_clauses.append(f"MATCH ()-[{filter_rel_var}:{filter_rel_type}]->({filter_node_var})")
                match_clauses.append(f"MATCH ({filter_node_var})-[m]-()")
                prop_expr = f"type({filter_rel_var})"  # Use type() function to get relationship type
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "node_property":
                logging.info(f"  Case: REF=RELATIONSHIP, FILTER=NODE_PROPERTY")
                match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[m]-()")
                prop_expr = f"{filter_node_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_node_var}.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "relationship_property":
                logging.info(f"  Case: REF=RELATIONSHIP, FILTER=RELATIONSHIP_PROPERTY")
                if filter_rel_type == canonical_ref:
                    prop_expr = f"m.`{filter_prop_name}`" if filter_prop_name else "m.name"
                    logging.info(f"  Same relationship, property expression: {prop_expr}")
                else:
                    match_clauses.append(f"MATCH ()-[{filter_rel_var}:{filter_rel_type}]-()")
                    prop_expr = f"{filter_rel_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_rel_var}.name"
                    logging.info(f"  Different relationship, property expression: {prop_expr}")
        
        # REF: NODE_PROPERTY
        elif ref_rep == "node_property":
            if f_rep == "node":
                logging.info(f"  Case: REF=NODE_PROPERTY, FILTER=NODE")
                if filter_node_label != ref_node_label:
                    logging.info(f"  Looking for path from {filt.field} to {ref_label}...")
                    path_pattern, start_var, end_var, is_rel_target = find_path_between_entities(filt.field, ref_label, schema=schema)
                    logging.info(f"  Path result: {path_pattern}")
                    if path_pattern:
                        path_pattern = path_pattern.replace("(n:", f"({filter_node_var}:")
                        match_clauses.append(f"MATCH {path_pattern}")
                        prop_expr = f"{filter_node_var}.name"
                        needs_path = True
                    else:
                        match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[]-(m)")
                        prop_expr = f"{filter_node_var}.name"
                else:
                    prop_expr = "m.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "relationship":
                logging.info(f"  Case: REF=NODE_PROPERTY, FILTER=RELATIONSHIP")
                # Get target node from ontology
                rel_obj = f_obj
                rel_from = getattr(rel_obj, "from_node", None)
                rel_to = getattr(rel_obj, "to_node", None)
                
                # Match the relationship and connected node
                if rel_from == ref_node_label:
                    match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]->({filter_node_var}:{rel_to})")
                elif rel_to == ref_node_label:
                    match_clauses.append(f"MATCH ({filter_node_var}:{rel_from})-[{filter_rel_var}:{filter_rel_type}]->(m)")
                else:
                    match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]-({filter_node_var})")
                prop_expr = f"{filter_node_var}.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "node_property":
                logging.info(f"  Case: REF=NODE_PROPERTY, FILTER=NODE_PROPERTY")
                if filter_node_label == ref_node_label:
                    prop_expr = f"m.`{filter_prop_name}`" if filter_prop_name else "m.name"
                    logging.info(f"  Same node, property expression: {prop_expr}")
                else:
                    logging.info(f"  Looking for path from {filt.field} to {ref_label}...")
                    path_pattern, start_var, end_var, is_rel_target = find_path_between_entities(filt.field, ref_label, schema=schema)
                    logging.info(f"  Path: {path_pattern}")
                    if path_pattern:
                        path_pattern = path_pattern.replace("(n:", f"({filter_node_var}:")
                        match_clauses.append(f"MATCH {path_pattern}")
                        prop_expr = f"{filter_node_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_node_var}.name"
                        needs_path = True
                    else:
                        match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[]-(m)")
                        prop_expr = f"{filter_node_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_node_var}.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "relationship_property":
                logging.info(f"  Case: REF=NODE_PROPERTY, FILTER=RELATIONSHIP_PROPERTY")
                match_clauses.append(f"MATCH (m)-[{filter_rel_var}:{filter_rel_type}]-()")
                prop_expr = f"{filter_rel_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_rel_var}.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
        
        # REF: RELATIONSHIP_PROPERTY
        elif ref_rep == "relationship_property":
            if f_rep == "node":
                logging.info(f"  Case: REF=RELATIONSHIP_PROPERTY, FILTER=NODE")
                logging.info(f"  Looking for path from {filt.field} to {ref_label}...")
                path_pattern, start_var, end_var, is_rel_target = find_path_between_entities(filt.field, ref_label, schema=schema)
                logging.info(f"  Path: {path_pattern}, is_rel_target: {is_rel_target}")
                if path_pattern and is_rel_target:
                    path_pattern = path_pattern.replace("(n:", f"({filter_node_var}:")
                    match_clauses.append(f"MATCH {path_pattern}")
                    prop_expr = f"{filter_node_var}.name"
                else:
                    match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[m]-()")
                    prop_expr = f"{filter_node_var}.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "relationship":
                logging.info(f"  Case: REF=RELATIONSHIP_PROPERTY, FILTER=RELATIONSHIP")
                if filter_rel_type == ref_rel_type:
                    # Same relationship type - use type() function
                    prop_expr = "type(m)"
                else:
                    # Different relationship - match nodes connected by both relationships
                    match_clauses.append(f"MATCH ({filter_node_var})-[{filter_rel_var}:{filter_rel_type}]-()")
                    match_clauses.append(f"MATCH ({filter_node_var})-[m:{ref_rel_type}]-()")
                    prop_expr = f"type({filter_rel_var})"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "node_property":
                logging.info(f"  Case: REF=RELATIONSHIP_PROPERTY, FILTER=NODE_PROPERTY")
                logging.info(f"  Looking for path from {filt.field} to {ref_label}...")
                path_pattern, start_var, end_var, is_rel_target = find_path_between_entities(filt.field, ref_label, schema=schema)
                logging.info(f"  Path: {path_pattern}, is_rel_target: {is_rel_target}")
                if path_pattern and is_rel_target:
                    path_pattern = path_pattern.replace("(n:", f"({filter_node_var}:")
                    match_clauses.append(f"MATCH {path_pattern}")
                    prop_expr = f"{filter_node_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_node_var}.name"
                else:
                    match_clauses.append(f"MATCH ({filter_node_var}:{filter_node_label})-[m]-()")
                    prop_expr = f"{filter_node_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_node_var}.name"
                logging.info(f"  ✓ Property expression: {prop_expr}")
            elif f_rep == "relationship_property":
                logging.info(f"  Case: REF=RELATIONSHIP_PROPERTY, FILTER=RELATIONSHIP_PROPERTY")
                if filter_rel_type == ref_rel_type:
                    prop_expr = f"m.`{filter_prop_name}`" if filter_prop_name else "m.name"
                    logging.info(f"  Same relationship, property expression: {prop_expr}")
                else:
                    match_clauses.append(f"MATCH ()-[{filter_rel_var}:{filter_rel_type}]-()")
                    prop_expr = f"{filter_rel_var}.`{filter_prop_name}`" if filter_prop_name else f"{filter_rel_var}.name"
                    logging.info(f"  Different relationship, property expression: {prop_expr}")

        if not prop_expr:
            # prop_expr is None for relationship existence checks - no WHERE clause needed
            if f_rep == "relationship" and filt.filter_type == FilterTypes.EQUAL:
                logging.info(f"✓ Relationship existence check complete (no WHERE clause needed)")
                continue
            else:
                logging.warning(f"❌ Could not determine property expression for filter {idx}, SKIPPING")
                continue

        logging.info(f"\n✓ Final property expression: {prop_expr}")

        # Build WHERE clause based on filter_type
        ft = filt.filter_type
        p = f"p{idx}"
        p1, p2 = f"p{idx}_1", f"p{idx}_2"

        if ft == FilterTypes.CONTAINS:
            params[p] = str(filt.value)
            where_clause = f"toLower(coalesce(toString({prop_expr}), '')) CONTAINS toLower(${p})"
            where_clauses.append(where_clause)
            logging.info(f"✓ WHERE clause: {where_clause}")

        elif ft == FilterTypes.IN:
            params[p] = filt.value if isinstance(filt.value, list) else [filt.value]
            where_clause = f"{prop_expr} IN ${p}"
            where_clauses.append(where_clause)
            logging.info(f"✓ WHERE clause: {where_clause}")

        elif ft == FilterTypes.BETWEEN:
            if isinstance(filt.value, (list, tuple)):
                v1, v2 = filt.value[0], filt.value[1]
            else:
                parts = str(filt.value).split(",")
                v1, v2 = parts[0].strip(), parts[1].strip()
            params[p1], params[p2] = v1, v2
            
            is_numeric = (filter_prop_type and 
                         str(getattr(filter_prop_type, "value", filter_prop_type)).lower() 
                         in ("float", "int", "integer", "numeric"))
            
            if is_numeric or (isinstance(v1, (int, float)) and isinstance(v2, (int, float))):
                where_clause = f"toFloat({prop_expr}) >= toFloat(${p1}) AND toFloat({prop_expr}) <= toFloat(${p2})"
            else:
                where_clause = f"{prop_expr} >= ${p1} AND {prop_expr} <= ${p2}"
            where_clauses.append(where_clause)
            logging.info(f"✓ WHERE clause: {where_clause}")

        elif ft == FilterTypes.LESS_THAN:
            params[p] = filt.value
            if filter_prop_type and str(getattr(filter_prop_type, "value", filter_prop_type)).lower() in ("float", "int", "integer", "numeric"):
                where_clause = f"toFloat({prop_expr}) < toFloat(${p})"
            else:
                where_clause = f"{prop_expr} < ${p}"
            where_clauses.append(where_clause)
            logging.info(f"✓ WHERE clause: {where_clause}")

        elif ft == FilterTypes.GREATER_THAN:
            params[p] = filt.value
            if filter_prop_type and str(getattr(filter_prop_type, "value", filter_prop_type)).lower() in ("float", "int", "integer", "numeric"):
                where_clause = f"toFloat({prop_expr}) > toFloat(${p})"
            else:
                where_clause = f"{prop_expr} > ${p}"
            where_clauses.append(where_clause)
            logging.info(f"✓ WHERE clause: {where_clause}")

        elif ft == FilterTypes.EQUAL:
            params[p] = filt.value
            prop_type_str = (str(getattr(filter_prop_type, "value", filter_prop_type)).lower() 
                           if filter_prop_type else "")
            is_numeric = prop_type_str in ("float", "int", "integer", "numeric")
            is_list = "list" in prop_type_str or "array" in prop_type_str
            
            # Handle type() function calls (for relationship type matching)
            if prop_expr.startswith("type("):
                where_clause = f"toLower({prop_expr}) = toLower(${p})"
            elif is_numeric or isinstance(filt.value, (int, float)):
                where_clause = f"toFloat({prop_expr}) = toFloat(${p})"
            elif is_list:
                where_clause = (
                    f"(toLower(coalesce(toString({prop_expr}), '')) = toLower(${p}) OR "
                    f"ANY(x IN {prop_expr} WHERE toLower(coalesce(toString(x), '')) = toLower(${p})))"
                )
            else:
                where_clause = f"toLower(coalesce(toString({prop_expr}), '')) = toLower(${p})"
            where_clauses.append(where_clause)
            logging.info(f"✓ WHERE clause: {where_clause}")

    # Deduplicate match clauses
    seen = set()
    deduped_matches = []
    for m in match_clauses:
        if m not in seen:
            deduped_matches.append(m)
            seen.add(m)

    logging.info(f"\n{'='*60}")
    logging.info(f"FINAL QUERY ASSEMBLY")
    logging.info(f"{'='*60}")
    logging.info(f"Match clauses ({len(deduped_matches)}):")
    for i, m in enumerate(deduped_matches):
        logging.info(f"  {i+1}. {m}")
    logging.info(f"\nWhere clauses ({len(where_clauses)}):")
    for i, w in enumerate(where_clauses):
        logging.info(f"  {i+1}. {w}")
    logging.info(f"\nParameters: {params}")

    # Optimize query using indexed properties
    # Reorder MATCH clauses to leverage indexes - put indexed property filters first
    if len(deduped_matches) > 1:
        base_match = deduped_matches[0]
        other_matches = deduped_matches[1:]
        
        # Categorize matches by whether they use indexed properties
        indexed_matches = []
        path_matches = []
        other_simple_matches = []
        
        for m in other_matches:
            # Check if this match uses indexed properties (name, product_hash, value)
            # These are matches with WHERE clauses on indexed properties
            has_indexed_prop = False
            match_lower = m.lower()
            
            # Check for indexed property patterns in match
            if 'name' in match_lower or 'product_hash' in match_lower or 'value' in match_lower:
                # This match likely benefits from indexes
                indexed_matches.append(m)
                has_indexed_prop = True
            
            if not has_indexed_prop:
                # Check if it's a path pattern (more expensive)
                if '-[' in m and ']-' in m and ('*' in m or 'p0' in m or 'pf' in m):
                    path_matches.append(m)
                else:
                    other_simple_matches.append(m)
        
        # Reorder: base → indexed matches → simple matches → path matches
        deduped_matches = [base_match] + indexed_matches + other_simple_matches + path_matches
        
        logging.info(f"\n✓ Query optimization: Reordered MATCH clauses")
        logging.info(f"  - Base match: 1")
        logging.info(f"  - Indexed matches: {len(indexed_matches)}")
        logging.info(f"  - Simple matches: {len(other_simple_matches)}")
        logging.info(f"  - Path matches: {len(path_matches)}")

    # Build final query
    query = "\n".join(deduped_matches)

    if where_clauses:
        op = " OR " if logical_operator.upper() == "OR" else " AND "
        
        # Separate WHERE clauses using indexed properties from others
        indexed_where = []
        other_where = []
        
        for w in where_clauses:
            w_lower = w.lower()
            # Check if WHERE clause uses indexed properties
            # Patterns: m.name, m.product_hash, m.value, n0.name, etc.
            if ('.name' in w_lower or '.product_hash' in w_lower or '.value' in w_lower or 
                '(`name`' in w_lower or '(`product_hash`' in w_lower or '(`value`' in w_lower):
                indexed_where.append(w)
            else:
                other_where.append(w)
        
        # Combine: indexed filters first (better selectivity)
        ordered_where = indexed_where + other_where
        
        if indexed_where:
            logging.info(f"\n✓ Query optimization: Using {len(indexed_where)} indexed property filters")
        
        query += "\nWHERE " + op.join(ordered_where)
    
    # Apply early LIMIT optimization for multiple filters to reduce intermediate result sets
    # This works best when we have selective indexed property filters
    if len(filters) > 1 and indexed_where:
        # Use WITH to apply early limiting after indexed filters
        # Get more candidates than needed to ensure we have enough after final filters
        early_limit = limit * 10
        query += f"\nWITH DISTINCT m LIMIT {early_limit}"
        logging.info(f"\n✓ Query optimization: Early LIMIT {early_limit} after indexed filters")
    
    # Return appropriate variable based on ref type
    if ref_rep == "node":
        query += f"\nRETURN DISTINCT m LIMIT {limit}"
    elif ref_rep == "relationship":
        query += f"\nRETURN DISTINCT m1, m, m2 LIMIT {limit}"
    elif ref_rep == "node_property":
        query += f"\nRETURN DISTINCT m.`{canonical_ref}` AS value LIMIT {limit}"
    elif ref_rep == "relationship_property":
        query += f"\nRETURN DISTINCT m.`{canonical_ref}` AS value LIMIT {limit}"

    logging.info(f"\n{'='*60}")
    logging.info(f"COMPLETE CYPHER QUERY:")
    logging.info(f"{'='*60}")
    logging.info(f"\n{query}\n")
    logging.info(f"Parameters: {params}")
    logging.info(f"{'='*60}\n")

    return query, params


def verify_node_exists_query(label: str, node_name: str) -> Tuple[str, Dict[str, Any]]:
    """
    Generate a parameterized Cypher query to verify if a node with a given label and name exists.

    Args:
        label: Node label to query
        node_name: Name of the node

    Returns:
        Tuple of (Cypher query string, params dict)
    """
    query = f"MATCH (s:{label}) WHERE toLower(s.name) = toLower($name) RETURN s.name as name"
    params = {"name": node_name}
    return query, params