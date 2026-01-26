from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import logging


# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.connectors.neo4j_connector import Neo4jConnector
from data.schemas.product_type_patterns import PRODUCT_TYPE_PATTERNS


### STEPS TO DETERMINE CATEGORIES VALUES ###

class CategoryAnalysis:
    def __init__(self):
        self.neo4j_connector = Neo4jConnector()
        self.driver = self.neo4j_connector.get_neo4j_driver()
        # Set driver on connector for execute_query to work
        self.neo4j_connector.driver = self.driver
    
    def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Execute a Cypher query and return results.
        
        Args:
            query: Cypher query string
            parameters: Query parameters dictionary
            
        Returns:
            List of result dictionaries
        """
        return self.neo4j_connector.execute_query(query, parameters or {})
    
    def _detect_product_type(self, product_name: str, product_description: str) -> Optional[str]:
        """
        Detect product type by checking name and description against configured patterns.
        This is done ONCE per product A instead of in every comparison.
        
        Args:
            product_name: Product name
            product_description: Product description
            
        Returns:
            Product type string (e.g., 'olive_oil') or None if no match
        """
        if not product_name:
            return None
        
        product_name_lower = product_name.lower()
        product_desc_lower = (product_description or "").lower()
        
        # Check each product type's patterns
        for product_type, patterns_config in PRODUCT_TYPE_PATTERNS.items():
            all_patterns = patterns_config.get("patterns_en", []) + patterns_config.get("patterns_es", [])
            
            for pattern in all_patterns:
                pattern_lower = pattern.lower()
                # Check in name or description
                if pattern_lower in product_name_lower or pattern_lower in product_desc_lower:
                    return product_type
        
        return None
    
    def get_type_category_subcategory(self, product_a_id: str, store_a_name: str) -> None:
        # First, get the category that product A belongs to
        query_neighbors = """
        MATCH (s:Store {name: $store_a})-[r:SELLS {id: $product_id}]->(p:Product)-[:COVERS]->(subcat:Internal_Subcategory)
        RETURN subcat.name AS subcategory_name
        """
        
        neighbors = self.execute_query(query_neighbors, {"product_id": product_a_id, "store_a": store_a_name})
        
        # Extract category name to narrow the search domain
        subcategory_name = None
        if neighbors and len(neighbors) > 0:
            subcategory_name = neighbors[0].get('subcategory_name')
        else:
            logging.info(f"  ⚠️ No category path found for product {product_a_id}")

        return subcategory_name


# 3. For each category, find the products from the supermarket we are looking for that match the category. Save their siids

    def find_matches_by_category(
        self,
        product_a: Dict[str, Any],
        store_a: str,
        store_b: str,
        min_matches: int,
        weights: dict,
        idx: int,
        total: int,
        cat_score_threshold: float = 0.15,
        min_graph_score: float = 0.7
    ) -> tuple:
        """
        Process a single product from store A to find similar products in store B.
        This method is designed to be called in parallel for multiple products A.
        
        Args:
            product_a: Product data from store A
            store_b: Name of destination store
            min_matches: Minimum number of shared neighbors
            weights: Weight configuration
            idx: Current product index (for logging)
            total: Total number of products (for logging)
            category_filter: Optional category filter
            score_threshold: Maximum score difference from best match (default: 0.15)
            
        Returns:
            Tuple of (product_a_id, product_a, similar_products_b)
        """

        # 1. Identify the node of the product we want to compare in the neo4j database 
        # The product is identified by its siid. 
        product_a_id = product_a.get('id')
        logging.info(f"[{idx}/{total}] Finding similar products for ID: {product_a_id}")

        # PRE-QUERY: Detect product type ONCE for product A (much faster than in-query detection)
        detected_product_type = self._detect_product_type(
            product_a.get('product_name', ''),
            product_a.get('description', '')
        )
        
        if detected_product_type:
            logging.info(f"  Detected product_type for {product_a_id}: {detected_product_type}")

        # OPTIMIZED: Single query that includes categorization + similarity search
        query_similar = """
        // 1) Get product A with its categorization
    MATCH (s:Store {name: $store_a})-[r:SELLS]->(a:Product)
    WHERE r.id = $product_id
    OPTIONAL MATCH (a)-[:COVERS]->(subcat_a:Internal_Subcategory)
    OPTIONAL MATCH (subcat_a)-[:HAS_INTERNAL_SUBCATEGORY]->(cat_a:Internal_Category)
    OPTIONAL MATCH (cat_a)-[:HAS_INTERNAL_CATEGORY]->(type_a:Internal_Type)

        // 2) Get neighbors of A (including aliases)
        WITH a, subcat_a, cat_a, type_a,
            a.measure_value AS a_measure_value,
            a.unit_measure AS a_unit_measure
    
    MATCH (a)-[rel_a]-(x)
    OPTIONAL MATCH (x)-[:ALIAS_OF*0..]->(canonical_x)
    WHERE canonical_x:Ingredient OR canonical_x IS NULL
    WITH a, subcat_a, cat_a, type_a, brand_a, a_is_marca_blanca, a_measure_value, a_unit_measure,
         collect(DISTINCT {
             node: COALESCE(canonical_x, x), 
             relType: type(rel_a),
             original: x
         }) AS neighborsA

    // 3) Get products in store B
    MATCH (sb:Store {name: $store_b})-[rb:SELLS]->(b:Product)
    OPTIONAL MATCH (b)-[:COVERS]->(subcat_b:Internal_Subcategory)
    OPTIONAL MATCH (subcat_b)-[:HAS_INTERNAL_SUBCATEGORY]->(cat_b:Internal_Category)
    OPTIONAL MATCH (cat_b)-[:HAS_INTERNAL_CATEGORY]->(type_b:Internal_Type)

    // EXCLUSION RULE: Internal category types must match if defined
    WHERE type_a IS NULL OR type_b.name = type_a.name"""

        # Add product type filtering if detected (OPTIMIZATION: done once per product A)
        if detected_product_type and detected_product_type in PRODUCT_TYPE_PATTERNS:
            patterns = PRODUCT_TYPE_PATTERNS[detected_product_type]
            all_patterns = patterns.get('patterns_en', []) + patterns.get('patterns_es', [])
            
            # Build simple pattern matching condition for product B
            # Much faster than running CASE statements for every product B
            pattern_conditions = [
                f"(toLower(b.product_name) CONTAINS '{pattern.lower()}' OR toLower(b.description) CONTAINS '{pattern.lower()}')"
                for pattern in all_patterns
            ]
            
            if pattern_conditions:
                query_similar += f"\n      AND ({' OR '.join(pattern_conditions)})"
                logging.info(f"  ✨ Detected '{detected_product_type}' - Added {len(all_patterns)} pattern filters: {all_patterns}")

        query_similar += """

        OPTIONAL MATCH (b)-[:FROM_BRAND]-(brand:Brand)
        OPTIONAL MATCH (b)-[:IS_PACKED_AS]-(fmt:Format)
        // Check brand B for marca_blanca connection
        OPTIONAL MATCH (brand)-[:ALIAS_OF]->(mb_check_b:Brand {name: 'marca_blanca'})
        
        // Filter: 
        // 1) both are marca_blanca OR both are NOT marca_blanca
        // 2) if both are NOT marca_blanca AND both have defined brands (not "no brand" or "unknown"), they must have the same brand
        // 3) if unit_measure is 'L', measure_value must be the same
        WITH a, neighborsA, b, sb, rb, brand, fmt, cat_b, cat_a, subcat_a, brand_a, a_is_marca_blanca, a_measure_value, a_unit_measure,
             mb_check_b IS NOT NULL AS b_is_marca_blanca
        WHERE (
              NOT (toLower(coalesce(a_unit_measure, '')) = 'l' OR toLower(coalesce(b_unit_measure, '')) = 'l')
              OR (a_measure_value = b_measure_value OR (a_measure_value IS NULL AND b_measure_value IS NULL))
          )
        
        WITH a, neighborsA, b, sb, rb, brand, fmt, cat_b, 
             cat_a, subcat_a

    // 4) Calculate overlap (considering aliases)
    UNWIND neighborsA AS n
    MATCH (b)-[rel_b]-(y)
    OPTIONAL MATCH (y)-[:ALIAS_OF*0..]->(canonical_y)
    WHERE canonical_y:Ingredient OR canonical_y IS NULL
    WITH b, sb, rb, brand, fmt, cat_b, cat_a, subcat_a, n,
         COALESCE(canonical_y, y) AS matched_node,
         type(rel_b) AS rel_type
    WHERE b <> a
      AND matched_node = n.node
      AND rel_type = n.relType

    WITH b, sb, rb, brand, fmt,
         cat_b,
         cat_a,
         subcat_a,
         count(DISTINCT n.node) AS overlap,
         collect(DISTINCT n) AS shared_nodes
    WHERE overlap >= $min_matches

    // 5) Calculate weighted score with category bonus
    WITH b, sb, rb, brand, fmt,
         cat_b,
         cat_a,
         subcat_a,
         overlap,
         shared_nodes,
         CASE WHEN cat_a.name = cat_b.name THEN $weight_category ELSE 0.0 END AS category_bonus

    WITH b, sb, rb, brand, fmt, overlap, shared_nodes, category_bonus,
         cat_b.name AS category_b_name,
         reduce(score = 0.0, item IN shared_nodes |
             score +
             CASE
                 WHEN item.node:Brand        THEN $weight_brand
                 WHEN item.node:Format       THEN $weight_format
                 WHEN item.node:Quantity     THEN $weight_quantity
                 WHEN item.node:Ingredient AND item.relType = 'FIRST_LEVEL_INGREDIENT'
                      THEN $weight_component_first
                 WHEN item.node:Ingredient AND item.relType = 'SECOND_LEVEL_INGREDIENT'
                      THEN $weight_component_second
                 WHEN item.node:Ingredient AND item.relType = 'OTHER_INGREDIENT'
                      THEN $weight_component_other
                 WHEN item.node:Ingredient AND item.relType = 'ALLERGENS'
                      THEN $weight_allergen
                 WHEN item.node:Country AND item.relType = 'COUNTRY_OF_ORIGIN'
                      THEN $weight_country_of_origin
                 WHEN item.node:Internal_Subcategory
                      THEN $weight_subcategory
                 ELSE 0.01
             END
         ) + category_bonus AS weighted_score

    // 6) Order and limit
    ORDER BY weighted_score DESC, overlap DESC, b.siid ASC
    LIMIT 300

    // 7) Return all data
    RETURN rb.id          AS id,
           b.siid         AS siid,
           rb.uuid        AS uuid,
           b.product_name AS product_name,
           b.description  AS description,
           rb.url         AS url,
           rb.price       AS price,
           sb.name        AS store,
           brand.name     AS brand,
           fmt.name       AS format,
           category_b_name AS category_name,
           overlap,
           weighted_score,
           [item IN shared_nodes |
                CASE
                    WHEN item.node:Brand
                        THEN 'Brand: ' + item.node.name
                    WHEN item.node:Format
                        THEN 'Format: ' + item.node.name
                    WHEN item.node:Ingredient AND item.relType = 'FIRST_LEVEL_INGREDIENT'
                        THEN 'First_level_ingredient: ' + item.node.name
                    WHEN item.node:Ingredient AND item.relType = 'SECOND_LEVEL_INGREDIENT'
                        THEN 'Second_level_ingredient: ' + item.node.name
                    WHEN item.node:Ingredient AND item.relType = 'OTHER_INGREDIENT'
                        THEN 'Other_ingredient: ' + item.node.name
                    WHEN item.node:Ingredient AND item.relType = 'ALLERGENS'
                        THEN 'Allergen: ' + item.node.name
                    WHEN item.node:Country AND item.relType = 'COUNTRY_OF_ORIGIN'
                        THEN 'Origin_Country: ' + item.node.name
                    WHEN item.node:Internal_Subcategory
                        THEN 'Internal_Subcategory: ' + item.node.name
                    ELSE 'Other: ' + coalesce(item.node.name, 'N/A')
                END
           ] AS shared_nodes_details
    """

        parameters = {
            "product_id": product_a_id,
            "store_a": store_a,
            "store_b": store_b,
            "min_matches": min_matches,
            "weight_brand":  float(weights.get("Brand", 0.25)),
            "weight_format": float(weights.get("Format", 0.15)),
            "weight_pt":     float(weights.get("Product_type", 0.60)),
            "weight_unit_measure": float(weights.get("Unit_measure", 0.10)),
            "weight_component_first":  float(weights.get("First_level_ingredient", 0.20)),
            "weight_component_second": float(weights.get("Second_level_ingredient", 0.10)),
            "weight_allergen": float(weights.get("Allergen", 0.05)),
            "weight_subcategory": float(weights.get("Internal_Subcategory", 0.05)),
            "weight_country_of_origin": float(weights.get("Country_of_Origin", 0.05)),
            "weight_component_other": float(weights.get("Other_Ingredient", 0.05)),
            "weight_quantity": float(weights.get("Quantity", 0.10)),
            "weight_category": float(weights.get("Category", 0.30))
        }



        
        # Build actual query string for logging (replace parameters with actual values)
        actual_query = query_similar.replace("$product_id", str(parameters['product_id']))
        actual_query = actual_query.replace("$store_b", f"'{parameters['store_b']}'")
        actual_query = actual_query.replace("$store_a", f"'{parameters['store_a']}'")
        actual_query = actual_query.replace("$min_matches", str(parameters['min_matches']))
        actual_query = actual_query.replace("$weight_brand", str(parameters['weight_brand']))
        actual_query = actual_query.replace("$weight_format", str(parameters['weight_format']))
        actual_query = actual_query.replace("$weight_pt", str(parameters['weight_pt']))
        actual_query = actual_query.replace("$weight_component_first", str(parameters['weight_component_first']))
        actual_query = actual_query.replace("$weight_component_second", str(parameters['weight_component_second']))
        actual_query = actual_query.replace("$weight_allergen", str(parameters['weight_allergen']))
        actual_query = actual_query.replace("$weight_subcategory", str(parameters['weight_subcategory']))
        actual_query = actual_query.replace("$weight_country_of_origin", str(parameters['weight_country_of_origin']))
        actual_query = actual_query.replace("$weight_component_other", str(parameters['weight_component_other']))
        actual_query = actual_query.replace("$weight_quantity", str(parameters['weight_quantity']))
        actual_query = actual_query.replace("$weight_category", str(parameters['weight_category']))
        if "type_name" in parameters:
            actual_query = actual_query.replace("$type_name", f"'{parameters['type_name']}'")
        
        # logging.info(f"  📋 Query with actual values:\n{actual_query}")

        similar_products_b = self.execute_query(query_similar, parameters)
        
        # Filter products by graph score difference from the best match
        if similar_products_b:
            max_graph_score = similar_products_b[0].get('weighted_score', 0.0)
            filtered_products = [
                prod for prod in similar_products_b 
                if (max_graph_score - prod.get('weighted_score', 0.0)) < cat_score_threshold
                and prod.get('weighted_score', 0.0) >= min_graph_score  # Use parameter instead of hardcoded value
            ]
            similar_products_b = filtered_products
            logging.info(f"[{idx}/{total}] ✅ Similar product found for  ID: {product_a_id}")
        else:
            logging.info(f"  ⚠️ No similar products found in {store_b}")

        return (product_a_id, product_a, similar_products_b)


    def score_candidates_by_category(
        self,
        product_a: Dict[str, Any],
        store_a: str,
        store_b: str,
        candidate_ids: List[str],
        weights: dict,
    ) -> List[Dict[str, Any]]:
        """Score a provided candidate set of store-B products for a given product A.

        This is a *scoring-only* variant of `find_matches_by_category`:
        - Restricts comparison domain to `candidate_ids` (e.g., top-200 from embedding search)
        - Does NOT apply min_matches/cat_score_threshold/min_graph_score filtering
        - Does NOT LIMIT results (other than the provided candidate list)

        Returns:
            List of product_b dicts including `weighted_score` and `overlap`.
        """
        
        product_a_id = product_a.get('id')
        if not product_a_id or not candidate_ids:
            return []

        # Detect product type once for product A and pass patterns into Cypher.
        # If B matches the same detected type, add a fixed +0.3 to the weighted_score.
        detected_product_type = self._detect_product_type(
            product_a.get('product_name', ''),
            product_a.get('description', '')
        )
        product_type_patterns: List[str] = []
        product_type_bonus = 0.0
        if detected_product_type and detected_product_type in PRODUCT_TYPE_PATTERNS:
            patterns_config = PRODUCT_TYPE_PATTERNS[detected_product_type]
            all_patterns = patterns_config.get('patterns_en', []) + patterns_config.get('patterns_es', [])
            product_type_patterns = [p.lower() for p in all_patterns if p]
            product_type_bonus = 0.3

        query = """
        // 1) Load product A and collect its neighbors (including aliases for ingredients)
        MATCH (sa:Store {name: $store_a})-[ra:SELLS]->(a:Product)
        WHERE ra.id = $product_id
        OPTIONAL MATCH (a)-[:COVERS]->(subcat_a:Internal_Subcategory)
        OPTIONAL MATCH (subcat_a)-[:HAS_INTERNAL_SUBCATEGORY]->(cat_a:Internal_Category)

        WITH a, subcat_a, cat_a,
             a.measure_value AS a_measure_value,
             a.unit_measure AS a_unit_measure

        MATCH (a)-[rel_a]-(x)
        OPTIONAL MATCH (x)-[:ALIAS_OF*0..]->(canonical_x)
        WHERE canonical_x:Ingredient OR canonical_x IS NULL

        WITH a, subcat_a, cat_a, a_measure_value, a_unit_measure,
             collect(DISTINCT {
                 node: COALESCE(canonical_x, x),
                 relType: type(rel_a),
                 original: x
             }) AS neighborsA

        WITH a, subcat_a, cat_a, a_measure_value, a_unit_measure,
             CASE WHEN size(neighborsA) = 0
                  THEN [{node: NULL, relType: NULL, original: NULL}]
                  ELSE neighborsA
             END AS neighborsA_exp

        // 2) Match ONLY candidate products in store B
        MATCH (sb:Store {name: $store_b})-[rb:SELLS]->(b:Product)
        WHERE rb.id IN $candidate_ids
        OPTIONAL MATCH (b)-[:COVERS]->(subcat_b:Internal_Subcategory)
        OPTIONAL MATCH (subcat_b)-[:HAS_INTERNAL_SUBCATEGORY]->(cat_b:Internal_Category)
        OPTIONAL MATCH (b)-[:FROM_BRAND]-(brand:Brand)
        OPTIONAL MATCH (b)-[:IS_PACKED_AS]-(fmt:Format)

           // Marca blanca / brand compatibility filter (same idea as find_matches_by_category)
           OPTIONAL MATCH (a)-[:FROM_BRAND]-(brand_a:Brand)
           OPTIONAL MATCH (brand_a)-[:ALIAS_OF]->(mb_check_a:Brand {name: 'marca_blanca'})
           OPTIONAL MATCH (brand)-[:ALIAS_OF]->(mb_check_b:Brand {name: 'marca_blanca'})

           WITH a, subcat_a, cat_a, a_measure_value, a_unit_measure,
               neighborsA_exp,
               b, sb, rb, subcat_b, cat_b, brand, fmt,
               b.measure_value AS b_measure_value,
               b.unit_measure AS b_unit_measure,
               mb_check_a IS NOT NULL AS a_is_marca_blanca,
               mb_check_b IS NOT NULL AS b_is_marca_blanca
           WHERE (
                NOT (toLower(coalesce(a_unit_measure, '')) = 'l' OR toLower(coalesce(b_unit_measure, '')) = 'l')
                OR (a_measure_value = b_measure_value OR (a_measure_value IS NULL AND b_measure_value IS NULL))
            )
          AND a_is_marca_blanca = b_is_marca_blanca

        // 3) Compute overlap + shared nodes (preserving candidates with 0 overlap)
           WITH a, subcat_a, cat_a, a_measure_value, a_unit_measure,
               b, sb, rb, subcat_b, cat_b, brand, fmt, neighborsA_exp
        UNWIND neighborsA_exp AS n
        OPTIONAL MATCH (b)-[rel_b]-(y)
        OPTIONAL MATCH (y)-[:ALIAS_OF*0..]->(canonical_y)
        WHERE canonical_y:Ingredient OR canonical_y IS NULL
        WITH b, sb, rb, subcat_b, cat_b, brand, fmt, cat_a, subcat_a, n,
             COALESCE(canonical_y, y) AS matched_node,
             type(rel_b) AS rel_type

        WITH b, sb, rb, subcat_b, cat_b, brand, fmt, cat_a, subcat_a,
             collect(DISTINCT CASE
                 WHEN n.node IS NOT NULL AND matched_node = n.node AND rel_type = n.relType
                 THEN n
                 ELSE NULL
             END) AS shared_nodes_raw

        WITH b, sb, rb, subcat_b, cat_b, brand, fmt, cat_a, subcat_a,
             [x IN shared_nodes_raw WHERE x IS NOT NULL] AS shared_nodes

        WITH b, sb, rb, brand, fmt,
             cat_b,
             cat_a,
             subcat_a,
             size(shared_nodes) AS overlap,
             shared_nodes,
             CASE WHEN cat_a.name = cat_b.name THEN $weight_category ELSE 0.0 END AS category_bonus,
             CASE
                 WHEN (toLower(coalesce(subcat_a.name, '')) CONTAINS 'frozen' OR toLower(coalesce(cat_a.name, '')) CONTAINS 'frozen')
                      AND (toLower(coalesce(subcat_b.name, '')) CONTAINS 'frozen' OR toLower(coalesce(cat_b.name, '')) CONTAINS 'frozen')
                 THEN 0.4
                 ELSE 0.0
             END AS frozen_bonus,
             CASE
                 WHEN any(p IN $product_type_patterns WHERE
                     toLower(coalesce(b.product_name, '')) CONTAINS p
                     OR toLower(coalesce(b.description, '')) CONTAINS p
                 )
                 THEN $weight_product_type_bonus
                 ELSE 0.0
             END AS product_type_bonus

        WITH b, sb, rb, brand, fmt, overlap, shared_nodes, category_bonus, product_type_bonus, frozen_bonus,
             cat_b.name AS category_b_name,
             reduce(score = 0.0, item IN shared_nodes |
                 score +
                 CASE
                     WHEN item.node:Brand        THEN $weight_brand
                     WHEN item.node:Format       THEN $weight_format
                     WHEN item.node:Quantity     THEN $weight_quantity
                     WHEN item.node:Ingredient AND item.relType = 'FIRST_LEVEL_INGREDIENT'
                          THEN $weight_component_first
                     WHEN item.node:Ingredient AND item.relType = 'SECOND_LEVEL_INGREDIENT'
                          THEN $weight_component_second
                     WHEN item.node:Ingredient AND item.relType = 'OTHER_INGREDIENT'
                          THEN $weight_component_other
                     WHEN item.node:Ingredient AND item.relType = 'ALLERGENS'
                          THEN $weight_allergen
                     WHEN item.node:Country AND item.relType = 'COUNTRY_OF_ORIGIN'
                          THEN $weight_country_of_origin
                     WHEN item.node:Internal_Subcategory
                          THEN $weight_subcategory
                     ELSE 0.01
                 END
             ) + category_bonus + product_type_bonus + frozen_bonus AS weighted_score

        ORDER BY weighted_score DESC, overlap DESC, b.siid ASC

        RETURN rb.id          AS id,
               b.siid         AS siid,
               rb.uuid        AS uuid,
               b.product_name AS product_name,
               b.description  AS description,
               rb.url         AS url,
               rb.price       AS price,
               sb.name        AS store,
               brand.name     AS brand,
               fmt.name       AS format,
               category_b_name AS category_name,
               overlap,
               weighted_score,
               [item IN shared_nodes |
                    CASE
                        WHEN item.node:Brand
                            THEN 'Brand: ' + item.node.name
                        WHEN item.node:Format
                            THEN 'Format: ' + item.node.name
                        WHEN item.node:Ingredient AND item.relType = 'FIRST_LEVEL_INGREDIENT'
                            THEN 'First_level_ingredient: ' + item.node.name
                        WHEN item.node:Ingredient AND item.relType = 'SECOND_LEVEL_INGREDIENT'
                            THEN 'Second_level_ingredient: ' + item.node.name
                        WHEN item.node:Ingredient AND item.relType = 'OTHER_INGREDIENT'
                            THEN 'Other_ingredient: ' + item.node.name
                        WHEN item.node:Ingredient AND item.relType = 'ALLERGENS'
                            THEN 'Allergen: ' + item.node.name
                        WHEN item.node:Country AND item.relType = 'COUNTRY_OF_ORIGIN'
                            THEN 'Origin_Country: ' + item.node.name
                        WHEN item.node:Internal_Subcategory
                            THEN 'Internal_Subcategory: ' + item.node.name
                        ELSE 'Other: ' + coalesce(item.node.name, 'N/A')
                    END
               ] AS shared_nodes_details
        """

        parameters = {
            "product_id": product_a_id,
            "store_a": store_a,
            "store_b": store_b,
            "candidate_ids": candidate_ids,
            "product_type_patterns": product_type_patterns,
            "weight_product_type_bonus": product_type_bonus,
            "weight_brand": float(weights.get("Brand", 0.25)),
            "weight_format": float(weights.get("Format", 0.15)),
            "weight_pt": float(weights.get("Product_type", 0.60)),
            "weight_unit_measure": float(weights.get("Unit_measure", 0.10)),
            "weight_component_first": float(weights.get("First_level_ingredient", 0.20)),
            "weight_component_second": float(weights.get("Second_level_ingredient", 0.10)),
            "weight_allergen": float(weights.get("Allergen", 0.05)),
            "weight_subcategory": float(weights.get("Internal_Subcategory", 0.05)),
            "weight_country_of_origin": float(weights.get("Country_of_Origin", 0.05)),
            "weight_component_other": float(weights.get("Other_Ingredient", 0.05)),
            "weight_quantity": float(weights.get("Quantity", 0.10)),
            "weight_category": float(weights.get("Category", 0.30)),
        }

        return self.execute_query(query, parameters)
    
    def load_products_from_store(
        self, 
        store_a: str, 
        category_filter: str = None, 
        limit_products_a: int = None
    ) -> List[Dict[str, Any]]:
        """
        Load products from a store in Neo4j.
        
        Args:
            store_a: Name of the store to load products from
            category_filter: Optional category prefix to filter products
            limit_products_a: Optional limit on number of products to load
            
        Returns:
            List of product dictionaries
        """
        query = """
        MATCH (s:Store {name: $store_a})-[r:SELLS]->(p:Product)
        """
        
        parameters = {"store_a": store_a}
        
        if category_filter:
            query += """
            MATCH (p)-[:COVERS]->(subcat:Internal_Subcategory)
            WHERE subcat.name STARTS WITH $category_filter
            """
            parameters["category_filter"] = category_filter
        
        query += """
        RETURN r.id AS id,
               p.siid AS siid,
               p.product_name AS product_name,
               p.description AS description,
               r.url AS url
        """
        
        if limit_products_a:
            query += f" LIMIT {limit_products_a}"
        
        products = self.execute_query(query, parameters)
        
        # Add store_name to each product
        for product in products:
            product['store_name'] = store_a
        
        logging.info(f"📦 Loaded {len(products)} products from {store_a}")
        
        return products
    
    def load_products_from_external_ids(
        self,
        external_ids_path: str,
        store_a: str,
        limit_products_a: int = None
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        Load products from Neo4j using external IDs from a file.
        
        Args:
            external_ids_path: Path to file containing product IDs
            store_a: Name of the store
            limit_products_a: Optional limit on number of products to load
            
        Returns:
            Tuple of (products_list, not_found_ids_list)
        """
        import os
        
        # Read product IDs from file
        if not os.path.exists(external_ids_path):
            logging.error(f"❌ External IDs file not found: {external_ids_path}")
            return [], []
        
        with open(external_ids_path, 'r') as f:
            product_ids = [line.strip() for line in f if line.strip()]
        
        if limit_products_a:
            product_ids = product_ids[:limit_products_a]
        
        logging.info(f"📄 Read {len(product_ids)} product IDs from {external_ids_path}")
        
        # OPTIMIZACIÓN: Batch query en lugar de queries individuales
        products = []
        not_found_ids = []
        
        # Keep as strings (SELLS.id is string in Neo4j for all stores)
        product_ids_str = [str(pid).strip() for pid in product_ids]
        
        # Single batch query with IN operator
        query = """
        MATCH (s:Store {name: $store_a})-[r:SELLS]->(p:Product)
        WHERE r.id IN $product_ids
        RETURN r.id AS id,
               p.siid AS siid,
               p.product_name AS product_name,
               p.description AS description,
               r.url AS url
        """
        
        # 🔍 PRINT ACTUAL QUERY WITH REAL VALUES
        sample_ids = product_ids_str[:5] if len(product_ids_str) > 5 else product_ids_str
        sample_ids_quoted = [f'"{id}"' for id in sample_ids]
        logging.info(f"🔍 Query to Neo4j: MATCH (s:Store {{name: '{store_a}'}})-[r:SELLS]->(p:Product) WHERE r.id IN [{', '.join(sample_ids_quoted)}{'...' if len(product_ids_str) > 5 else ''}] RETURN ...")
        logging.info(f"📊 Total IDs: {len(product_ids_str)}, ID type: {type(product_ids_str[0]).__name__ if product_ids_str else 'N/A'}")
        
        results = self.execute_query(query, {"store_a": store_a, "product_ids": product_ids_str})
        
        # Create a set of found IDs for quick lookup
        found_ids = set()
        for result in results:
            result['store_name'] = store_a
            products.append(result)
            found_ids.add(result['id'])
        
        # Identify not found IDs
        for product_id in product_ids_str:
            if product_id not in found_ids:
                not_found_ids.append(product_id)
        
        logging.info(f"✅ Found {len(products)} products in Neo4j")
        if not_found_ids:
            logging.warning(f"⚠️ {len(not_found_ids)} product IDs not found in Neo4j")
        
        return products, not_found_ids


    def load_products_from_id_list(
        self,
        id_list: List[str],
        store_a: str,
        limit_products_a: int = None
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        Load products from Neo4j using a provided list of IDs.
        
        Args:
            id_list: List of product IDs (as strings or integers)
            store_a: Name of the store
            limit_products_a: Optional limit on number of products to load
            
        Returns:
            Tuple of (products_list, not_found_ids_list)
        """
        if not id_list:
            logging.error("❌ Empty ID list provided")
            return [], []
        
        if limit_products_a:
            id_list = id_list[:limit_products_a]
        
        logging.info(f"📋 Processing {len(id_list)} product IDs from provided list")
        
        # OPTIMIZACIÓN: Batch query en lugar de queries individuales
        products = []
        not_found_ids = []
        
        # Keep as strings (SELLS.id is string in Neo4j for all stores)
        product_ids_str = [str(pid).strip() for pid in id_list]
        
        # Single batch query with IN operator
        query = """
        MATCH (s:Store {name: $store_a})-[r:SELLS]->(p:Product)
        WHERE r.id IN $product_ids
        RETURN r.id AS id,
               p.siid AS siid,
               p.product_name AS product_name,
               p.description AS description,
               r.url AS url
        """
        
        # 🔍 PRINT ACTUAL QUERY WITH REAL VALUES
        sample_ids = product_ids_str[:5] if len(product_ids_str) > 5 else product_ids_str
        sample_ids_quoted = [f'"{id}"' for id in sample_ids]
        logging.info(f"🔍 Query to Neo4j: MATCH (s:Store {{name: '{store_a}'}})-[r:SELLS]->(p:Product) WHERE r.id IN [{', '.join(sample_ids_quoted)}{'...' if len(product_ids_str) > 5 else ''}] RETURN ...")
        logging.info(f"📊 Total IDs: {len(product_ids_str)}, ID type: {type(product_ids_str[0]).__name__ if product_ids_str else 'N/A'}")
        
        results = self.execute_query(query, {"store_a": store_a, "product_ids": product_ids_str})
        
        # Create a set of found IDs for quick lookup
        found_ids = set()
        for result in results:
            result['store_name'] = store_a
            products.append(result)
            found_ids.add(result['id'])
        
        # Identify not found IDs
        for product_id in product_ids_str:
            if product_id not in found_ids:
                not_found_ids.append(product_id)
        
        logging.info(f"✅ Found {len(products)} products in Neo4j")
        if not_found_ids:
            logging.warning(f"⚠️ {len(not_found_ids)} product IDs not found in Neo4j")
        
        return products, not_found_ids




