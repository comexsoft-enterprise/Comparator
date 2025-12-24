import logging
import os
import re
from typing import Dict, Any, List, Optional
import pandas as pd
import math

from config.settings import PROJECT_ROOT
from data.schemas.taxonomy import (
    ColumnRegistry, 
    NodeTypes, 
    RelationshipTypes, 
    RoleInGraph,
    RELATIONSHIP_NODE_MAPPING,
    NODE_IDENTIFIERS
)
from data.schemas.categories import (
    InternalType, 
    InternalCategoryFood
)
from src.connectors.neo4j_connector import Neo4jConnector

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

logging.basicConfig(level=logging.INFO)


class Neo4jNodesRelationshipsManager:
    """
    Fully registry-driven manager for creating Neo4j nodes and relationships.
    Optimized for batch operations.
    """

    def __init__(self, registry_path: str = None):
        """Initialize the Registry-Driven Neo4j Manager."""
        self.neo4j_connector = Neo4jConnector()
        self.driver = self.neo4j_connector.get_neo4j_driver(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)
        
        if registry_path is None:
            registry_path = PROJECT_ROOT / "data" / "schemas" / "column_registry.json"
        
        self.column_registry = ColumnRegistry.load_from_json(registry_path)
        
        self.NODE_IDENTIFIERS = NODE_IDENTIFIERS
        self.RELATIONSHIP_NODE_MAPPING = RELATIONSHIP_NODE_MAPPING
        
        # Build dynamic mappings from registry
        self.node_source_columns = self._build_node_source_columns()
        self.node_property_map = self._build_node_property_map()
        self.relationship_property_map = self._build_relationship_property_map()
        
        logging.info(f"Loaded {len(self.node_source_columns)} node types from registry")

        # **DEBUG: Print what was found**
        logging.info(f"\n{'='*80}")
        logging.info("NODE PROPERTY MAP:")
        for node_type, props in self.node_property_map.items():
            if props:
                logging.info(f"  {node_type.value}: {props}")
        
        logging.info("\nRELATIONSHIP PROPERTY MAP:")
        for rel_type, props in self.relationship_property_map.items():
            if props:
                logging.info(f"  {rel_type.value}: {props}")
        logging.info(f"{'='*80}\n")

    def _build_node_source_columns(self) -> Dict[NodeTypes, str]:
        """Build mapping of NodeTypes to their source column names."""
        node_cols = {}
        
        for col_name, col_def in self.column_registry.columns.items():
            if col_def.representation == RoleInGraph.NODE:
                node_type = self._convert_to_node_type(col_def.belongs_to)
                if node_type:
                    node_cols[node_type] = col_name
        
        return node_cols

    def _build_node_property_map(self) -> Dict[NodeTypes, List[str]]:
        """Build mapping of NodeTypes to their property columns."""
        node_map = {node_type: [] for node_type in NodeTypes}
        
        logging.info("\n=== BUILDING NODE PROPERTY MAP ===")
        for col_name, col_def in self.column_registry.columns.items():
            if col_def.representation == RoleInGraph.NODE_PROPERTY:
                logging.info(f"Column '{col_name}': belongs_to='{col_def.belongs_to}' (type: {type(col_def.belongs_to)})")
                node_type = self._convert_to_node_type(col_def.belongs_to)
                if node_type:
                    node_map[node_type].append(col_name)
                    logging.info(f"  ✅ Mapped to {node_type.value}")
                else:
                    logging.warning(f"  ❌ Could not convert '{col_def.belongs_to}' to NodeType")
        logging.info("=== END NODE PROPERTY MAP ===\n")
        
        return node_map

    def _build_relationship_property_map(self) -> Dict[RelationshipTypes, List[str]]:
        """Build mapping of RelationshipTypes to their property columns."""
        rel_map = {rel_type: [] for rel_type in RelationshipTypes}
        
        logging.info("\n=== BUILDING RELATIONSHIP PROPERTY MAP ===")
        for col_name, col_def in self.column_registry.columns.items():
            if col_def.representation == RoleInGraph.RELATIONSHIP_PROPERTY:
                logging.info(f"Column '{col_name}': belongs_to='{col_def.belongs_to}' (type: {type(col_def.belongs_to)})")
                rel_type = self._convert_to_relationship_type(col_def.belongs_to)
                if rel_type:
                    rel_map[rel_type].append(col_name)
                    logging.info(f"  ✅ Mapped to {rel_type.value}")
                else:
                    logging.warning(f"  ❌ Could not convert '{col_def.belongs_to}' to RelationshipType")
        logging.info("=== END RELATIONSHIP PROPERTY MAP ===\n")
        
        return rel_map

    def _convert_to_node_type(self, belongs_to) -> Optional[NodeTypes]:
        """Convert belongs_to to NodeTypes enum."""
        if isinstance(belongs_to, NodeTypes):
            return belongs_to
        if isinstance(belongs_to, str):
            try:
                # Try exact match with enum value
                result = NodeTypes(belongs_to)
                logging.debug(f"  Converted '{belongs_to}' to {result.value} (exact match)")
                return result
            except ValueError:
                # Try case-insensitive match with enum names
                belongs_to_upper = belongs_to.upper()
                for node_type in NodeTypes:
                    if node_type.name.upper() == belongs_to_upper:
                        logging.debug(f"  Converted '{belongs_to}' to {node_type.value} (name match)")
                        return node_type
                logging.warning(f"  Failed to convert '{belongs_to}' to NodeType")
        return None

    def _convert_to_relationship_type(self, belongs_to) -> Optional[RelationshipTypes]:
        """Convert belongs_to to RelationshipTypes enum."""
        if isinstance(belongs_to, RelationshipTypes):
            return belongs_to
        if isinstance(belongs_to, str):
            try:
                # Try exact match with enum value
                result = RelationshipTypes(belongs_to)
                logging.debug(f"  Converted '{belongs_to}' to {result.value} (exact match)")
                return result
            except ValueError:
                # Try case-insensitive match with enum names
                belongs_to_upper = belongs_to.upper()
                for rel_type in RelationshipTypes:
                    if rel_type.name.upper() == belongs_to_upper:
                        logging.debug(f"  Converted '{belongs_to}' to {rel_type.value} (name match)")
                        return rel_type
                logging.warning(f"  Failed to convert '{belongs_to}' to RelationshipType")
        return None

    @staticmethod
    def is_empty_value(value) -> bool:
        """Check if a value is None, NaN, or empty string."""
        if value is None:
            return True
        if isinstance(value, float) and math.isnan(value):
            return True
        try:
            if pd.isna(value):
                return True
        except (ValueError, TypeError):
            pass
        if isinstance(value, str) and not value.strip():
            return True
        return False

    def get_value_case_insensitive(self, product_data: Dict[str, Any], column_name: str) -> Any:
        """Get value from product_data using case-insensitive column name matching."""
        if column_name in product_data:
            return product_data.get(column_name)
        
        column_name_lower = column_name.lower()
        if column_name_lower in product_data:
            return product_data.get(column_name_lower)
        
        for key in product_data.keys():
            if key.lower() == column_name_lower:
                return product_data.get(key)
        
        return None

    def process_products_batch(self, session, products_data: List[Dict[str, Any]]):
        """
        Process multiple products in batch - OPTIMIZED VERSION.
        
        Args:
            session: Neo4j session
            products_data: List of product dictionaries
        """
        # Step 1: Prepare all nodes data (grouped by type)
        nodes_by_type = self._prepare_nodes_batch(products_data)
        
        # Step 2: Create all nodes in batch
        self._create_nodes_batch(session, nodes_by_type)
        
        # Step 3: Prepare all relationships data (grouped by type)
        relationships_by_type = self._prepare_relationships_batch(products_data)
        
        # Step 4: Create all relationships in batch
        self._create_relationships_batch(session, relationships_by_type)
        
        logging.info(f"Completed batch processing of {len(products_data)} products")

    def _prepare_nodes_batch(self, products_data: List[Dict[str, Any]]) -> Dict[NodeTypes, List[Dict[str, Any]]]:
        """Prepare node data grouped by node type."""
        nodes_by_type = {node_type: [] for node_type in NodeTypes}
        # Use sets for O(1) duplicate detection
        seen_identifiers = {node_type: set() for node_type in NodeTypes}
        
        for product_data in products_data:
            siid = self.get_value_case_insensitive(product_data, 'siid')
            product_hash = self.get_value_case_insensitive(product_data, 'product_hash')

            # Prefer product_hash as the product identifier when available (it's globally unique).
            product_identifier = None
            if not self.is_empty_value(product_hash):
                product_identifier = str(product_hash).strip()
            elif not self.is_empty_value(siid):
                product_identifier = str(siid).strip()

            if self.is_empty_value(product_identifier):
                # Skip rows without any product identifier
                continue

            # Product node
            product_properties = self._collect_node_properties(NodeTypes.PRODUCT, product_data)
            nodes_by_type[NodeTypes.PRODUCT].append({
                'identifier': product_identifier,
                'properties': product_properties
            })
            
            # Other nodes from registry
            for node_type, source_column in self.node_source_columns.items():
                if node_type == NodeTypes.PRODUCT:
                    continue
                
                identifier_value = self.get_value_case_insensitive(product_data, source_column)
                
                if self.is_empty_value(identifier_value):
                    continue

                # Check if this node type should split by comma
                should_split = node_type in [NodeTypes.BRAND, NodeTypes.STORE, NodeTypes.FORMAT]

                if should_split:
                    # Split by comma and create separate nodes
                    values = [v.strip() for v in str(identifier_value).replace(';', ',').replace('.', ',').split(',') if v.strip()]
                    for value in values:
                        if not self.is_empty_value(value) and value.lower() != 'nan':
                            if value not in seen_identifiers[node_type]:
                                seen_identifiers[node_type].add(value)
                                properties = self._collect_node_properties(node_type, product_data)
                                node_data = {
                                    'identifier': value,
                                    'properties': properties
                                }
                                nodes_by_type[node_type].append(node_data)
                else:
                    # Single value node
                    properties = self._collect_node_properties(node_type, product_data)

                    # Avoid duplicates using set (O(1) lookup)
                    if identifier_value not in seen_identifiers[node_type]:
                        seen_identifiers[node_type].add(identifier_value)
                        node_data = {
                            'identifier': identifier_value,
                            'properties': properties
                        }
                        nodes_by_type[node_type].append(node_data)

                # Country nodes (can come from multiple sources, comma-separated, dot-separated)
                country = self.get_value_case_insensitive(product_data, 'country')
                if not self.is_empty_value(country):
                    countries = [c.strip() for c in str(country).replace(';', ',').replace('.', ',').split(',') if c.strip()]
                    for country_value in countries:
                        if not self.is_empty_value(country_value) and country_value.lower() != 'nan':
                            if country_value not in seen_identifiers[NodeTypes.COUNTRY]:
                                seen_identifiers[NodeTypes.COUNTRY].add(country_value)
                                properties = self._collect_node_properties(NodeTypes.COUNTRY, product_data)
                                node_data = {'identifier': country_value, 'properties': properties}
                                nodes_by_type[NodeTypes.COUNTRY].append(node_data)

                country_origin = self.get_value_case_insensitive(product_data, 'country_origin')
                if not self.is_empty_value(country_origin):
                    country_origins = [c.strip() for c in str(country_origin).replace(';', ',').replace('.', ',').split(',') if c.strip()]
                    for origin_value in country_origins:
                        if not self.is_empty_value(origin_value) and origin_value.lower() != 'nan':
                            if origin_value not in seen_identifiers[NodeTypes.COUNTRY]:
                                seen_identifiers[NodeTypes.COUNTRY].add(origin_value)
                                properties = self._collect_node_properties(NodeTypes.COUNTRY, product_data)
                                node_data = {'identifier': origin_value, 'properties': properties}
                                nodes_by_type[NodeTypes.COUNTRY].append(node_data)
                
            # Ingredients/Components
            is_food = self._is_food_product(product_data)

            # Allergens
            allergens_str = self.get_value_case_insensitive(product_data, 'allergens')
            if not self.is_empty_value(allergens_str):
                allergens = [a.strip() for a in str(allergens_str).replace(';', ',').replace('.', ',').split(',') if a.strip()]
                for allergen in allergens:
                    if not self.is_empty_value(allergen) and allergen.lower() != 'nan':
                        if allergen not in seen_identifiers[NodeTypes.INGREDIENT]:
                            seen_identifiers[NodeTypes.INGREDIENT].add(allergen)
                            node_data = {'identifier': allergen, 'properties': {}}
                            nodes_by_type[NodeTypes.INGREDIENT].append(node_data)

            if is_food:
                ingredients_str = self.get_value_case_insensitive(product_data, 'ingredients')
                if not self.is_empty_value(ingredients_str):
                    ingredients = [i.strip() for i in re.split(r'[\/,;|]', str(ingredients_str)) if i.strip()]
                    for ingredient in ingredients:
                        if not self.is_empty_value(ingredient) and ingredient.lower() != 'nan':
                            if ingredient not in seen_identifiers[NodeTypes.INGREDIENT]:
                                seen_identifiers[NodeTypes.INGREDIENT].add(ingredient)
                                node_data = {'identifier': ingredient, 'properties': {}}
                                nodes_by_type[NodeTypes.INGREDIENT].append(node_data)
            else:
                components_str = self.get_value_case_insensitive(product_data, 'components')
                if not self.is_empty_value(components_str):
                    components = [c.strip() for c in str(components_str).split('/') if c.strip()]
                    for component in components:
                        if not self.is_empty_value(component) and component.lower() != 'nan':
                            if component not in seen_identifiers[NodeTypes.COMPONENT]:
                                seen_identifiers[NodeTypes.COMPONENT].add(component)
                                node_data = {'identifier': component, 'properties': {}}
                                nodes_by_type[NodeTypes.COMPONENT].append(node_data)

        return nodes_by_type

    def _create_nodes_batch(self, session, nodes_by_type: Dict[NodeTypes, List[Dict[str, Any]]]):
        """Create all nodes in batch using UNWIND."""
        for node_type, nodes_data in nodes_by_type.items():
            if not nodes_data:
                continue
            
            identifier_key = self.NODE_IDENTIFIERS.get(node_type)
            if not identifier_key:
                continue
            
            # Collect ALL unique property keys across all nodes
            all_properties = set()
            for node in nodes_data:
                all_properties.update(node['properties'].keys())
            
            logging.info(f"Processing {node_type.value}: {len(nodes_data)} nodes with properties: {all_properties}")
            logging.info(f"Using identifier key for {node_type.value}: '{identifier_key}'")
            
            # Prepare batch data - each node only includes properties it has
            batch_data = []
            for node in nodes_data:
                node_record = {
                    'identifier': node['identifier'],
                    'properties': node['properties']  # Keep as nested dict
                }
                batch_data.append(node_record)
            
            # Build property SET clauses that check for existence
            set_clauses = []
            for prop in all_properties:
                # Use coalesce to only set if property exists in the node's properties
                escaped_prop = f"`{prop}`"
                set_clauses.append(f"n.{escaped_prop} = coalesce(node.properties.{escaped_prop}, n.{escaped_prop})")
            
            set_clause = f"SET {', '.join(set_clauses)}" if set_clauses else ""
            
            query = f"""
            UNWIND $batch AS node
            MERGE (n:{node_type.value} {{{identifier_key}: node.identifier}})
            {set_clause}
            """
            
            try:
                result = session.run(query, batch=batch_data)
                summary = result.consume()
                logging.info(
                    f"Created {node_type.value} nodes: "
                    f"{summary.counters.nodes_created} created, "
                    f"{summary.counters.properties_set} properties set"
                )
            except Exception as e:
                logging.error(f"Error creating {node_type.value} nodes in batch: {e}")
                logging.error(f"Query: {query}")
                logging.error(f"Sample data: {batch_data[0] if batch_data else 'empty'}")

    def _prepare_relationships_batch(self, products_data: List[Dict[str, Any]]) -> Dict[RelationshipTypes, List[Dict[str, Any]]]:
        """Prepare relationship data grouped by relationship type."""
        relationships_by_type = {rel_type: [] for rel_type in RelationshipTypes}
        
        for product_data in products_data:
            siid = self.get_value_case_insensitive(product_data, 'siid')
            product_hash = self.get_value_case_insensitive(product_data, 'product_hash')

            # Choose same product identifier as in nodes: prefer product_hash, fallback to siid
            if not self.is_empty_value(product_hash):
                product_identifier = str(product_hash).strip()
            else:
                product_identifier = siid

            if self.is_empty_value(product_identifier):
                continue
            
            # Standard relationships from mapping
            for rel_type, (source_node_type, target_node_type) in self.RELATIONSHIP_NODE_MAPPING.items():
                # Skip special relationships
                if rel_type in [
                    RelationshipTypes.ALLERGENS,
                    RelationshipTypes.FIRST_LEVEL_INGREDIENT,
                    RelationshipTypes.SECOND_LEVEL_INGREDIENT,
                    RelationshipTypes.OTHER_INGREDIENT,
                    RelationshipTypes.FIRST_LEVEL_COMPONENT,
                    RelationshipTypes.SECOND_LEVEL_COMPONENT,
                    RelationshipTypes.OTHER_COMPONENT
                ]:
                    continue
                
                # Get source value
                if source_node_type == NodeTypes.PRODUCT:
                    source_value = product_identifier
                else:
                    source_col = self.node_source_columns.get(source_node_type)
                    if not source_col:
                        continue
                    source_value = self.get_value_case_insensitive(product_data, source_col)
                
                # Get target value
                if target_node_type == NodeTypes.PRODUCT:
                    target_value = product_identifier
                else:
                    target_col = self.node_source_columns.get(target_node_type)
                    if not target_col:
                        continue
                    target_value = self.get_value_case_insensitive(product_data, target_col)
                
                if self.is_empty_value(source_value) or self.is_empty_value(target_value):
                    continue
                
                # Check if target node type should split by comma
                should_split_target = target_node_type in [NodeTypes.BRAND, NodeTypes.STORE, NodeTypes.FORMAT, NodeTypes.COUNTRY]
                
                if should_split_target:
                    # Split target values and create separate relationships
                    target_values = [v.strip() for v in str(target_value).replace(';', ',').split(',') if v.strip()]
                    for t_value in target_values:
                        if not self.is_empty_value(t_value) and t_value.lower() != 'nan':
                            properties = self._collect_relationship_properties(rel_type, product_data)
                            relationships_by_type[rel_type].append({
                                'source_id': source_value,
                                'target_id': t_value,
                                'properties': properties
                            })
                else:
                    # Single relationship
                    properties = self._collect_relationship_properties(rel_type, product_data)
                    relationships_by_type[rel_type].append({
                        'source_id': source_value,
                        'target_id': target_value,
                        'properties': properties
                    })
            
            # Special relationships: Allergens
            allergens_str = self.get_value_case_insensitive(product_data, 'allergens')
            if not self.is_empty_value(allergens_str):
                allergens = [a.strip() for a in str(allergens_str).replace(';', ',').split(',') if a.strip()]
                for allergen in allergens:
                    if not self.is_empty_value(allergen) and allergen.lower() != 'nan':
                        relationships_by_type[RelationshipTypes.ALLERGENS].append({
                            'source_id': product_identifier,
                            'target_id': allergen,
                            'properties': {}
                        })
            
            # Special relationships: Ingredients/Components
            is_food = self._is_food_product(product_data)
            
            if is_food:
                ingredients_str = self.get_value_case_insensitive(product_data, 'ingredients')
                if not self.is_empty_value(ingredients_str):
                    ingredients = [i.strip() for i in str(ingredients_str).split('/') if i.strip()]
                    for idx, ingredient in enumerate(ingredients):
                        if self.is_empty_value(ingredient) or ingredient.lower() == 'nan':
                            continue
                        
                        if idx == 0:
                            rel_type = RelationshipTypes.FIRST_LEVEL_INGREDIENT
                        elif idx == 1:
                            rel_type = RelationshipTypes.SECOND_LEVEL_INGREDIENT
                        else:
                            rel_type = RelationshipTypes.OTHER_INGREDIENT
                        
                        properties = self._collect_relationship_properties(rel_type, product_data)
                        
                        relationships_by_type[rel_type].append({
                            'source_id': product_identifier,
                            'target_id': ingredient,
                            'properties': properties
                        })
            else:
                components_str = self.get_value_case_insensitive(product_data, 'components')
                if not self.is_empty_value(components_str):
                    components = [c.strip() for c in str(components_str).split('/') if c.strip()]
                    for idx, component in enumerate(components):
                        if self.is_empty_value(component) or component.lower() == 'nan':
                            continue
                        
                        if idx == 0:
                            rel_type = RelationshipTypes.FIRST_LEVEL_COMPONENT
                        elif idx == 1:
                            rel_type = RelationshipTypes.SECOND_LEVEL_COMPONENT
                        else:
                            rel_type = RelationshipTypes.OTHER_COMPONENT
                        
                        properties = self._collect_relationship_properties(rel_type, product_data)
                        
                        relationships_by_type[rel_type].append({
                            'source_id': product_identifier,
                            'target_id': component,
                            'properties': properties
                        })
        
        return relationships_by_type

    def _create_relationships_batch(self, session, relationships_by_type: Dict[RelationshipTypes, List[Dict[str, Any]]]):
        """Create all relationships in batch using UNWIND."""
        for rel_type, relationships_data in relationships_by_type.items():
            if not relationships_data:
                continue
            
            source_node_type, target_node_type = self.RELATIONSHIP_NODE_MAPPING[rel_type]
            source_id_key = self.NODE_IDENTIFIERS.get(source_node_type)
            target_id_key = self.NODE_IDENTIFIERS.get(target_node_type)
            
            if not source_id_key or not target_id_key:
                continue
            
            # Collect ALL unique property keys across all relationships
            all_properties = set()
            for rel in relationships_data:
                all_properties.update(rel['properties'].keys())
            
            logging.info(f"Processing {rel_type.value}: {len(relationships_data)} relationships with properties: {all_properties}")
            logging.info(f"Using identifier keys for relationship {rel_type.value}: source='{source_id_key}', target='{target_id_key}'")
            # Prepare batch data - each relationship only includes properties it has
            batch_data = []
            for rel in relationships_data:
                rel_record = {
                    'source_id': rel['source_id'],
                    'target_id': rel['target_id'],
                    'properties': rel['properties']  # Keep as nested dict
                }
                batch_data.append(rel_record)
            
            # Build property SET clauses that check for existence
            set_clauses = []
            for prop in all_properties:
                # Use coalesce to only set if property exists in the rel's properties
                escaped_prop = f"`{prop}`"
                set_clauses.append(f"r.{escaped_prop} = coalesce(rel.properties.{escaped_prop}, r.{escaped_prop})")
            
            set_clause = f"SET {', '.join(set_clauses)}" if set_clauses else ""
            
            query = f"""
            UNWIND $batch AS rel
            MATCH (s:{source_node_type.value} {{{source_id_key}: rel.source_id}})
            MATCH (t:{target_node_type.value} {{{target_id_key}: rel.target_id}})
            MERGE (s)-[r:{rel_type.value}]->(t)
            {set_clause}
            """
            
            try:
                result = session.run(query, batch=batch_data)
                summary = result.consume()
                logging.info(
                    f"Created {rel_type.value} relationships: "
                    f"{summary.counters.relationships_created} created, "
                    f"{summary.counters.properties_set} properties set"
                )
            except Exception as e:
                logging.error(f"Error creating {rel_type.value} relationships in batch: {e}")
                logging.error(f"Query: {query}")
                logging.error(f"Sample data: {batch_data[0] if batch_data else 'empty'}")

    def _collect_node_properties(self, node_type: NodeTypes, product_data: Dict[str, Any]) -> Dict[str, Any]:
        """Collect all properties for a node from product_data based on registry."""
        properties = {}
        property_columns = self.node_property_map.get(node_type, [])
        
        for prop_col in property_columns:
            value = self.get_value_case_insensitive(product_data, prop_col)
            if not self.is_empty_value(value):
                properties[prop_col] = value
        
        # If collecting properties for Product, also include the source values
        # of other node labels so Product has those categories as properties too.
        if node_type == NodeTypes.PRODUCT:
            for other_node_type, source_column in self.node_source_columns.items():
                if other_node_type == NodeTypes.PRODUCT:
                    continue
                # Use the same case-insensitive getter
                value = self.get_value_case_insensitive(product_data, source_column)
                if not self.is_empty_value(value):
                    # Do not overwrite existing product property if already present
                    if source_column not in properties:
                        properties[source_column] = value
        
        return properties

    def _collect_relationship_properties(self, rel_type: RelationshipTypes, product_data: Dict[str, Any]) -> Dict[str, Any]:
        """Collect all properties for a relationship from product_data based on registry."""
        properties = {}
        property_columns = self.relationship_property_map.get(rel_type, [])
        
        for prop_col in property_columns:
            value = self.get_value_case_insensitive(product_data, prop_col)
            if not self.is_empty_value(value):
                properties[prop_col] = value
        
        return properties

    def _is_food_product(self, product_data: Dict[str, Any]) -> bool:
        """Determine if a product is Food."""
        internal_type = self.get_value_case_insensitive(product_data, 'internaltype')
        if isinstance(internal_type, str) and internal_type.strip().lower() == str(InternalType.FOOD.value).strip().lower():
            return True
        
        category = self.get_value_case_insensitive(product_data, 'internalcategory')
        # compare categories case-insensitively
        food_categories = [str(cat.value).strip().lower() for cat in InternalCategoryFood]
        if isinstance(category, str) and category.strip().lower() in food_categories:
             return True
        
        return False

    def create_constraints_and_indexes(self):
        """Create necessary constraints and indexes in Neo4j for all node types."""
        
        # Generate indexes dynamically from NODE_IDENTIFIERS
        indexes = []
        
        for node_type, identifier_key in self.NODE_IDENTIFIERS.items():
            # Create index for each node type based on its identifier
            index_name = f"{node_type.value.lower()}_{identifier_key}_idx"
            index_query = f"CREATE INDEX {index_name} IF NOT EXISTS FOR (n:{node_type.value}) ON (n.{identifier_key})"
            indexes.append((index_name, index_query))
        
        logging.info(f"Creating {len(indexes)} indexes for all node types...")
        
        with self.driver.session() as session:
            for index_name, index_query in indexes:
                try:
                    session.run(index_query)
                    logging.info(f"✓ Created index: {index_name}")
                except Exception as e:
                    logging.warning(f"✗ Could not create index {index_name}: {e}")
            
            # Create default "marca_blanca" Brand node if it doesn't exist
            try:
                create_marca_blanca_query = """
                MERGE (b:Brand {name: 'marca_blanca'})
                ON CREATE SET b.created_at = datetime()
                RETURN b
                """
                result = session.run(create_marca_blanca_query)
                if result.single():
                    logging.info("✓ Created or verified 'marca_blanca' Brand node")
            except Exception as e:
                logging.warning(f"✗ Could not create 'marca_blanca' Brand node: {e}")

    def close(self):
        """Close the Neo4j driver connection."""
        if self.driver:
            self.driver.close()