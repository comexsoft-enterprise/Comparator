from pydantic import BaseModel, Field
from typing import List, Optional


class PropertyDef(BaseModel):
    """
    Declare a single property mapping from a source column to a graph property.
    - source_column: header name in the CSV/input
    - property_name: property name to store in Neo4j (defaults to source_column)
    - type: "string"|"int"|"float"|"bool"|"list"
    - required: whether this property must be present to create the node/rel
    - unique: marks this property as part of the identity key for merges
    - translatable: application-level flag to indicate translation needed
    """
    source_column: Optional[str] = None
    property_name: Optional[str] = None
    type: Optional[str] = "string"
    required: Optional[bool] = None
    unique: Optional[bool] = None

    def resolved_name(self) -> str:
        return self.property_name or self.source_column


class NodeMapping(BaseModel):
    """
    Mapping for a node label.
    - node_label: Neo4j label (e.g., "Product")
    - identifier: optional property name used as primary id (if not provided, properties with unique=True are used)
    - properties: list of PropertyDef describing what columns become node props
    """
    node_label: str
    identifier: Optional[str] = None
    properties: List[PropertyDef] = Field(default_factory=list)


class RelationshipMapping(BaseModel):
    """
    Mapping for a relationship type.
    - rel_type: Neo4j relationship type (e.g., "SELLS")
    - from_node: source node label (e.g., "Store")
    - to_node: target node label (e.g., "Product")
    - directed: whether relationship is directed (True) or undirected (False)
    - properties: list of PropertyDef for relationship properties (price, currency, url...)
    """
    rel_type: str
    from_node: str
    to_node: str
    directed: bool = True
    properties: List[PropertyDef] = Field(default_factory=list)


class MappingRegistry(BaseModel):
    """
    Top-level registry. Edit this file manually to declare mappings.
    """
    nodes: List[NodeMapping] = Field(default_factory=list)
    relationships: List[RelationshipMapping] = Field(default_factory=list)

    def get_node(self, label: str) -> Optional[NodeMapping]:
        for n in self.nodes:
            if n.node_label.lower() == label.lower():
                return n
        return None

    def get_relationship(self, rel_type: str) -> Optional[RelationshipMapping]:
        for r in self.relationships:
            if r.rel_type.lower() == rel_type.lower():
                return r
        return None


NEO4J_ONTOLOGY = MappingRegistry(
    nodes=[
        NodeMapping(
            node_label="Product",
            identifier="product_hash",
            properties=[
                PropertyDef(source_column="uuid", property_name="uuid", type="string", required=False),
                PropertyDef(source_column="Product_Name", property_name="product_name", type="string", required=True),
                PropertyDef(source_column="Description", property_name="description", type="string", required=True),
                PropertyDef(source_column="Product_Hash", property_name="product_hash", type="string", unique=True),
                PropertyDef(source_column="siid", property_name="siid", type="string", required=True),
                PropertyDef(source_column="Unit_Measure", property_name="unit_measure", type="string", required=True),
                PropertyDef(source_column="Measure_Value", property_name="measure_value", type="float"),
                PropertyDef(source_column="Model", property_name="model", type="string", required=True),
                PropertyDef(source_column="Colour", property_name="colour", type="string", required=True)
            ],
        ),
        NodeMapping(
            node_label="Store",
            identifier="name",
            properties=[
                PropertyDef(source_column="Postcode", property_name="postcode", type="string", required=True),
                PropertyDef(source_column="store", property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Brand",
            identifier="name",
            properties=[
                PropertyDef(source_column="brand", property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Company",
            identifier="name",
            properties=[
                PropertyDef(property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Component",
            identifier="name",
            properties=[
                PropertyDef(property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Country",
            identifier="name",
            properties=[
                PropertyDef(property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Format",
            identifier="name",
            properties=[
                PropertyDef(source_column="Format", property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Ingredient",
            identifier="name",
            properties=[
                PropertyDef(property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Internal_Category",
            identifier="name",
            properties=[
                PropertyDef(source_column="InternalCategory", property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Internal_Subcategory",
            identifier="name",
            properties=[
                PropertyDef(source_column="InternalSubcategory", property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Internal_Type",
            identifier="name",
            properties=[
                PropertyDef(source_column="InternalType", property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Offer",
            identifier="name",
            properties=[
                PropertyDef(source_column="Offer", property_name="name", type="string", unique=True),
            ],
        ),
        NodeMapping(
            node_label="Quantity",
            identifier="name",
            properties=[
                PropertyDef(source_column="Quantity", property_name="name", type="string", unique=True),
            ],
        ),
    ],
    relationships=[
        RelationshipMapping(
            rel_type="SELLS",
            from_node="Store",
            to_node="Product",
            properties=[
                PropertyDef(source_column="price", type="float", property_name="price"),
                PropertyDef(source_column="seller", type="string", property_name="seller"),
                PropertyDef(source_column="url", type="string", property_name="url"),
                PropertyDef(source_column="category", type="string", property_name="category"),
                PropertyDef(source_column="unit_price", type="string", property_name="unit_price"),
                PropertyDef(source_column="image_list", type="string", property_name="image_list"),
                PropertyDef(source_column="id", type="float", property_name="id"),
                PropertyDef(source_column="conservation_characteristics", type="string", property_name="conservation_characteristics"),
                PropertyDef(source_column="price_without_vat", type="string", property_name="price_without_vat"),
            ],
        ),
        RelationshipMapping(
            rel_type="IS_SOLD_IN_COUNTRY",
            from_node="Product",
            to_node="Country",
            properties=[
                PropertyDef(source_column="currency", type="string", property_name="currency"),
                PropertyDef(source_column="vat", type="string", property_name="vat"),
                
            ],
        ),
        RelationshipMapping(
            rel_type="ALLERGENS",
            from_node="Product",
            to_node="Ingredient",
            properties=[],
        ),
        RelationshipMapping(
            rel_type="COUNTRY_OF_ORIGIN",
            from_node="Product",
            to_node="Country",
        ),
        RelationshipMapping(
            rel_type="COVERS",
            from_node="Product",
            to_node="Internal_Subcategory",
        ),
        RelationshipMapping(
            rel_type="FIRST_LEVEL_COMPONENT",
            from_node="Product",
            to_node="Component",
        ),
        RelationshipMapping(
            rel_type="FIRST_LEVEL_INGREDIENT",
            from_node="Product",
            to_node="Ingredient",
        ),
        RelationshipMapping(
            rel_type="FROM_BRAND",
            from_node="Product",
            to_node="Brand",
        ),
        RelationshipMapping(
            rel_type="HAS_INTERNAL_CATEGORY",
            from_node="Internal_Category",
            to_node="Internal_Type",
        ),
        RelationshipMapping(
            rel_type="HAS_INTERNAL_SUBCATEGORY",
            from_node="Internal_Subcategory",
            to_node="Internal_Category",
        ),
        RelationshipMapping(
            rel_type="HAS_OFFER",
            from_node="Product",
            to_node="Offer",
        ),
        RelationshipMapping(
            rel_type="IS_IN_COUNTRY",
            from_node="Store",
            to_node="Country",
        ),
        RelationshipMapping(
            rel_type="UNITS",
            from_node="Product",
            to_node="Quantity",
        ),
        RelationshipMapping(
            rel_type="IS_PACKED_AS",
            from_node="Product",
            to_node="Format",
        ),
        RelationshipMapping(
            rel_type="OTHER_COMPONENT",
            from_node="Product",
            to_node="Component",
        ),
        RelationshipMapping(
            rel_type="OTHER_INGREDIENT",
            from_node="Product",
            to_node="Ingredient",
        ),
        RelationshipMapping(
            rel_type="SECOND_LEVEL_COMPONENT",
            from_node="Product",
            to_node="Component",
        ),
        RelationshipMapping(
            rel_type="SECOND_LEVEL_INGREDIENT",
            from_node="Product",
            to_node="Ingredient",
        ),
        RelationshipMapping(
            rel_type="SELLS_OFFER",
            from_node="Store",
            to_node="Offer",
        )
    ]
)