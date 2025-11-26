import json
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Optional, Union
from enum import Enum
import logging
import sys

# Add the project root to Python path
from config.settings import PROJECT_ROOT
sys.path.insert(0, str(PROJECT_ROOT))

class ColumnPresence(str, Enum):
    """Define si una columna debe aparecer en los datos"""
    REQUIRED = "required"
    OPTIONAL = "optional"

class TranslatableColumn(str, Enum):
    """Define si una columna es traducible"""
    MAY_BE_TRANSLATABLE = "may_be_translatable"
    NON_TRANSLATABLE = "non_translatable"

class DatabaseType(str, Enum):
    """Define if a column should appear one database or another"""
    NEO4J_DB = "neo4j_db"
    MONGO_DB = "mongo_db"

class LLMStatus(str, Enum):
    """Define if the column is going to be processed by the LLM"""
    SOURCE_ONLY = "source_only"  # Only used as input, never output
    TARGET_ONLY = "target_only"  # Only output, never input
    BOTH = "both"  # Can be both input and output
    EXCLUDED = "excluded"  # Should not be processed by LLM

class RoleInGraph(str, Enum):
    """Define si la información de una columna se representa como nodo, relación, propiedad de nodo o propiedad de relación en Neo4j"""
    NODE = "node"  # Represented as a node
    RELATIONSHIP = "relationship"  # Represented as a relationship
    NODE_PROPERTY = "node_property"  # Represented as a property of a node
    RELATIONSHIP_PROPERTY = "relationship_property"  # Represented as a property of a relationship

class NodeTypes(str, Enum):
    """Define los posibles nodos a los que puede pertenecer una columna"""
    PRODUCT = "Product"
    COMPANY = "Company"
    STORE = "Store"
    COUNTRY = "Country"
    INTERNAL_TYPE = "Internal_Type"
    INTERNAL_CATEGORY = "Internal_Category"
    INTERNAL_SUBCATEGORY = "Internal_Subcategory"
    OFFER = "Offer"
    INGREDIENT = "Ingredient"
    COMPONENT = "Component"
    BRAND = "Brand"
    QUANTITY = "Quantity"
    FORMAT = "Format"

class RelationshipTypes(str, Enum):
    HAS_STORE = "HAS_STORE" ## Relationship from Company to Store
    SELLS = "SELLS" ## Relationship from Store to Product
    IS_IN_COUNTRY = "IS_IN_COUNTRY" ## Relationship from Store to Country
    IS_SOLD_IN_COUNTRY = "IS_SOLD_IN_COUNTRY" ## Relationship from Product to Country
    COUNTRY_OF_ORIGIN = "COUNTRY_OF_ORIGIN" ## Relationship from Product to Country
    SELLS_OFFER = "SELLS_OFFER" ## Relationship from Store to Offer
    HAS_OFFER = "HAS_OFFER" ## Relationship from Product to Offer
    ALLERGENS = "ALLERGENS" ## Relationship from Product to Ingredient
    FIRST_LEVEL_INGREDIENT = "FIRST_LEVEL_INGREDIENT" ## Relationship from Product to Ingredient
    SECOND_LEVEL_INGREDIENT = "SECOND_LEVEL_INGREDIENT" ## Relationship from Product to Ingredient
    OTHER_INGREDIENT = "OTHER_INGREDIENT" ## Relationship from Product to Ingredient
    FIRST_LEVEL_COMPONENT = "FIRST_LEVEL_COMPONENT" ## Relationship from Product to Component
    SECOND_LEVEL_COMPONENT = "SECOND_LEVEL_COMPONENT" ## Relationship from Product to Component
    OTHER_COMPONENT = "OTHER_COMPONENT" ## Relationship from Product to Component
    FROM_BRAND = "FROM_BRAND" ## Relationship from Product to Brand
    UNITS = "UNITS" ## Relationship from Product to Quantity
    IS_PACKED_AS = "IS_PACKED_AS" ## Relationship from Product to Format
    HAS_INTERNAL_CATEGORY = "HAS_INTERNAL_CATEGORY" ## Relationship from Product to Internal_Category
    HAS_INTERNAL_SUBCATEGORY = "HAS_INTERNAL_SUBCATEGORY" ## Relationship from Product to Internal_Subcategory
    COVERS = "COVERS" ## Relationship from Product to Internal_Type

RELATIONSHIP_NODE_MAPPING = {
        RelationshipTypes.HAS_STORE: (NodeTypes.COMPANY, NodeTypes.STORE),
        RelationshipTypes.SELLS: (NodeTypes.STORE, NodeTypes.PRODUCT),
        RelationshipTypes.IS_IN_COUNTRY: (NodeTypes.STORE, NodeTypes.COUNTRY),
        RelationshipTypes.IS_SOLD_IN_COUNTRY: (NodeTypes.COUNTRY, NodeTypes.PRODUCT),
        RelationshipTypes.COUNTRY_OF_ORIGIN: (NodeTypes.PRODUCT, NodeTypes.COUNTRY),
        RelationshipTypes.SELLS_OFFER: (NodeTypes.STORE, NodeTypes.OFFER),
        RelationshipTypes.HAS_OFFER: (NodeTypes.PRODUCT, NodeTypes.OFFER),
        RelationshipTypes.ALLERGENS: (NodeTypes.PRODUCT, NodeTypes.INGREDIENT),
        RelationshipTypes.FIRST_LEVEL_INGREDIENT: (NodeTypes.PRODUCT, NodeTypes.INGREDIENT),
        RelationshipTypes.SECOND_LEVEL_INGREDIENT: (NodeTypes.PRODUCT, NodeTypes.INGREDIENT),
        RelationshipTypes.OTHER_INGREDIENT: (NodeTypes.PRODUCT, NodeTypes.INGREDIENT),
        RelationshipTypes.FIRST_LEVEL_COMPONENT: (NodeTypes.PRODUCT, NodeTypes.COMPONENT),
        RelationshipTypes.SECOND_LEVEL_COMPONENT: (NodeTypes.PRODUCT, NodeTypes.COMPONENT),
        RelationshipTypes.OTHER_COMPONENT: (NodeTypes.PRODUCT, NodeTypes.COMPONENT),
        RelationshipTypes.FROM_BRAND: (NodeTypes.PRODUCT, NodeTypes.BRAND),
        RelationshipTypes.UNITS: (NodeTypes.PRODUCT, NodeTypes.QUANTITY),
        RelationshipTypes.IS_PACKED_AS: (NodeTypes.PRODUCT, NodeTypes.FORMAT),
        RelationshipTypes.HAS_INTERNAL_CATEGORY: (NodeTypes.INTERNAL_TYPE, NodeTypes.INTERNAL_CATEGORY),
        RelationshipTypes.HAS_INTERNAL_SUBCATEGORY: (NodeTypes.INTERNAL_CATEGORY, NodeTypes.INTERNAL_SUBCATEGORY),
        RelationshipTypes.COVERS: (NodeTypes.INTERNAL_SUBCATEGORY, NodeTypes.PRODUCT),
    }

NODE_IDENTIFIERS = {
        NodeTypes.PRODUCT: 'name',
        NodeTypes.COMPANY: 'name',
        NodeTypes.STORE: 'name',
        NodeTypes.COUNTRY: 'name',
        NodeTypes.INTERNAL_TYPE: 'name',
        NodeTypes.INTERNAL_CATEGORY: 'name',
        NodeTypes.INTERNAL_SUBCATEGORY: 'name',
        NodeTypes.OFFER: 'name',
        NodeTypes.INGREDIENT: 'name',
        NodeTypes.COMPONENT: 'name',
        NodeTypes.BRAND: 'name',
        NodeTypes.QUANTITY: 'value',
        NodeTypes.FORMAT: 'name',
    }

BelongsToNodesRelations = Union[NodeTypes, RelationshipTypes]

class UptableColumn(str, Enum):
    """Define si una columna es uptable o no"""
    UPTABLE = "uptable"
    NON_UPTABLE = "non_uptable"

class ColumnDefinition(BaseModel):
    """Definición completa de una columna"""
    name: str = Field(..., description="Nombre de la columna")
    presence: ColumnPresence = Field(..., description="Si la columna es requerida u opcional")
    translatable: TranslatableColumn = Field(..., description="Si la columna es traducible")
    databasetype: DatabaseType = Field(..., description="Tipo de base de datos")
    llmstatus: LLMStatus = Field(..., description="Estado de LLM de la columna")
    representation: RoleInGraph = Field(..., description="Tipo de representación en neo4j")
    belongs_to: BelongsToNodesRelations = Field(..., description="La información de la columna pertenece a qué nodo o relación")
    uptable: UptableColumn = Field(..., description="Si la columna es uptable o no")
    description: Optional[str] = Field(None, description="Descripción de la columna")
    type: Optional[str] = Field(None, description="Tipo de datos esperado")
    
    @field_validator('name')
    @classmethod
    def name_must_be_lowercase(cls, v):
        """Asegurar que los nombres de columnas están en lowercase"""
        return v.lower().strip()

class ColumnRegistry(BaseModel):
    """Registro central de todas las definiciones de columnas"""
    columns: Dict[str, ColumnDefinition] = Field(default_factory=dict)
    
    def add_column(self, column: ColumnDefinition) -> None:
        """Añadir una columna al registro"""
        self.columns[column.name] = column

    def get_required_columns(self) -> List[ColumnDefinition]:
        """Obtener todas las columnas requeridas"""
        return [col for col in self.columns.values() if col.presence == ColumnPresence.REQUIRED]
    
    def get_optional_columns(self) -> List[ColumnDefinition]:
        """Obtener todas las columnas opcionales"""
        return [col for col in self.columns.values() if col.presence == ColumnPresence.OPTIONAL]
    
    def get_translatable_columns(self) -> List[ColumnDefinition]:
        """Obtener todas las columnas traducibles"""
        return [col for col in self.columns.values() if col.translatable == TranslatableColumn.MAY_BE_TRANSLATABLE]
    
    def get_non_translatable_columns(self) -> List[ColumnDefinition]:
        """Obtener todas las columnas no traducibles"""
        return [col for col in self.columns.values() if col.translatable == TranslatableColumn.NON_TRANSLATABLE]
    
    def get_neo4j_columns(self) -> List[ColumnDefinition]:
        """Get Neo4j database columns"""
        return [col for col in self.columns.values() if col.databasetype == DatabaseType.NEO4J_DB]

    def get_mongo_columns(self) -> List[ColumnDefinition]:
        """Get MongoDB database columns"""
        return [col for col in self.columns.values() if col.databasetype == DatabaseType.MONGO_DB]

    def get_llm_complete_columns(self) -> List[ColumnDefinition]:
        """Get all columns that are complete for LLM"""
        return [col for col in self.columns.values() if col.llmstatus != LLMStatus.EXCLUDED]

    def get_not_llm_complete_columns(self) -> List[ColumnDefinition]:
        """Get all columns that are not complete for LLM"""
        return [col for col in self.columns.values() if col.llmstatus == LLMStatus.EXCLUDED]
    
    def get_llm_target_only_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que son solo objetivo"""
        return [col for col in self.columns.values() if col.llmstatus == LLMStatus.TARGET_ONLY]

    def get_llm_source_only_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que son solo fuente"""
        return [col for col in self.columns.values() if col.llmstatus == LLMStatus.SOURCE_ONLY]
    
    def get_llm_both_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que son tanto fuente como objetivo"""
        return [col for col in self.columns.values() if col.llmstatus == LLMStatus.BOTH]

    def get_node_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que se representan como nodos"""
        return [col for col in self.columns.values() if col.representation == RoleInGraph.NODE]
    
    def get_relationship_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que se representan como relaciones"""
        return [col for col in self.columns.values() if col.representation == RoleInGraph.RELATIONSHIP]
    
    def get_node_property_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que se representan como propiedades de nodos"""
        return [col for col in self.columns.values() if col.representation == RoleInGraph.NODE_PROPERTY]
    
    def get_relationship_property_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que se representan como propiedades de relaciones"""
        return [col for col in self.columns.values() if col.representation == RoleInGraph.RELATIONSHIP_PROPERTY]
    
    def get_uptable_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que son uptable"""
        return [col for col in self.columns.values() if col.uptable == UptableColumn.UPTABLE]
    
    def get_non_uptable_columns(self) -> List[ColumnDefinition]:
        """Obtener columnas que no son uptable"""
        return [col for col in self.columns.values() if col.uptable == UptableColumn.NON_UPTABLE]

    def get_description_from_columns(self, cols: List[str]) -> Dict[str, str]:
        """Obtener las descripciones de las columnas como diccionario"""
        return {col.name: col.description for col in self.columns.values() if col.description and col.name in cols}

    def validate_dataframe_columns(self, df_columns: List[str]) -> 'ColumnValidationResult':
        """Validar columnas de un DataFrame"""
        df_columns_set = set(col.lower().strip() for col in df_columns)
        required_columns = {col.name for col in self.get_required_columns()}
        
        missing_required = required_columns - df_columns_set
        extra_columns = df_columns_set - set(self.columns.keys())
        
        return ColumnValidationResult(
            missing_required=list(missing_required),
            extra_columns=list(extra_columns),
        )
    
    def to_json_dict(self) -> Dict[str, Dict[str, str]]:
        """Convertir el registro a diccionario para JSON"""
        return {
            col_name: {
                "presence": col_def.presence.value,
                "translatable": col_def.translatable.value,
                "databasetype": col_def.databasetype.value,
                "llmstatus": col_def.llmstatus.value,
                "representation": col_def.representation.value,
                "belongs_to": col_def.belongs_to.value,
                "uptable": col_def.uptable.value,
                "description": col_def.description,
                "type": col_def.type
            }
            for col_name, col_def in self.columns.items()
        }
    
    @classmethod
    def from_json_dict(cls, data: Dict[str, Dict[str, str]]) -> 'ColumnRegistry':
        """Crear registro desde diccionario JSON"""
        registry = cls()
        
        for col_name, col_data in data.items():

            belongs_to_value = col_data.get("belongs_to")
            try:
                belongs_to = NodeTypes(belongs_to_value)
            except ValueError:
                    # If that fails, try RelationshipTypes
                    try:
                        belongs_to = RelationshipTypes(belongs_to_value)
                    except ValueError:
                        # If both fail, log a warning and keep as string
                        logging.warning(f"Could not parse belongs_to value '{belongs_to_value}' for column '{col_name}'")
                        belongs_to = belongs_to_value

            column = ColumnDefinition(
                name=col_name,
                presence=ColumnPresence(col_data["presence"]),
                translatable=TranslatableColumn(col_data["translatable"]),
                databasetype=DatabaseType(col_data["databasetype"]),
                llmstatus=LLMStatus(col_data["llmstatus"]),
                representation=RoleInGraph(col_data["representation"]),
                belongs_to=belongs_to,
                uptable=UptableColumn(col_data["uptable"]),
                description=col_data.get("description"),
                type=col_data.get("type")
            )
            registry.add_column(column)
        
        return registry
    
    def save_to_json(self, file_path: str | Path) -> None:
        """Guardar el registro en un archivo JSON"""
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:  # Changed 'a' to 'w'
            json.dump(self.to_json_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_json(cls, file_path: str | Path) -> 'ColumnRegistry':
        """Cargar el registro desde un archivo JSON"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return cls.from_json_dict(data)
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON from {file_path}: {e}")
            raise
        except Exception as e:
            logging.error(f"Unexpected error loading JSON from {file_path}: {e}")
            raise

class ColumnValidationResult(BaseModel):
    """Resultado de la validación de columnas"""
    missing_required: List[str] = Field(default_factory=list)
    extra_columns: List[str] = Field(default_factory=list)
    
    def get_columns_to_generate(self, registry: ColumnRegistry) -> List[ColumnDefinition]:
        """Obtener columnas que deben generarse con valores por defecto"""
        return [registry.columns[col_name] for col_name in self.missing_required 
                if col_name in registry.columns]

# Configuración predefinida basada en tu RequiredCategories original
def create_default_registry() -> ColumnRegistry:
    """Crear registro con configuración por defecto"""
    registry = ColumnRegistry()
    
    # Definir columnas basadas en tu clase original
    default_columns = [
        ColumnDefinition(
            name="ID",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of relation store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Unique identifier for the product.",
            type="string"
        ),
        ColumnDefinition(
            name="EAN",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.UPTABLE,
            description="European Article Number",
            type="string"
        ),
        ColumnDefinition(
            name="GTIN",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.UPTABLE,
            description="Global Trade Item Number.",
            type="string"
        ),
        ColumnDefinition(
            name="Category",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.SOURCE_ONLY,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of relation store - product #TODO: ¿Repetimos las categorías en la red o las dejamos como parte de la relación?
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.NON_UPTABLE,
            description="Extract the product category if it is available in the row. " \
            "If missing, do not infer it, just leave it empty. The category can be expressed as " \
            "category/subcategory hierarchy often using a forward slash (/) as a separator. " \
            "If the category comes in other format, rexpress it separating subcategories with /. " \
            "Example 1: No Food/Cleaning Supplies" \
            "Example 2: {description: bakery, fresh bread, other breads} -> General food/Bakery/Fresh bread/other breads.",
            type="string"
        ),
        ColumnDefinition(
            name="Brand",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.NODE, # brand node
            belongs_to=NodeTypes.BRAND,
            uptable=UptableColumn.NON_UPTABLE,
            description="Use the value from the dedicated Brand column if it exists. " \
            "If empty, extract the brand name from the Description-like columns. " \
            "Example 1: COCA COLA -> Coca Cola; " \
            "Example 2: {description: Fine wine Bodegas el Toro} -> Bodegas el Toro.",
            type="string"
        ),
        ColumnDefinition(
            name="Product_Name",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Extract the simple, recognizable, and short name of the product. " \
            "This is not a description. Use only the information available in the data rows. " \
            "Example: Microwave prepared broccoli (75%) cooked with ham (10%) and olive oil (5%) -> Microwave prepared broccoli cooked with ham.",
            type="string"
        ),
        ColumnDefinition(
            name="InternalType",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.INTERNAL_TYPE,
            uptable=UptableColumn.NON_UPTABLE,
            description="Classify the product as 'Food' or 'Non-Food'. "
            "Example: Apple Magic Keyboard -> Non-Food; Granadilla tray 500 g -> Food.",
            type="string"
        ),
        ColumnDefinition(
            name="InternalCategory",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.INTERNAL_CATEGORY,
            uptable=UptableColumn.NON_UPTABLE,
            description="Classify the product as one of the internal categories based on its type."
            "Example: Apple Magic Keyboard -> Computing; Granadilla tray 500 g -> Fruit and Vegetables.",
            type="string"
        ),
        ColumnDefinition(
            name="InternalSubcategory",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.INTERNAL_SUBCATEGORY,
            uptable=UptableColumn.NON_UPTABLE,
            description="Classify the product as one of the internal subcategories based on its category."
            "Example: Apple Magic Keyboard -> Computer Accessories; Granadilla tray 500 g -> Fruit.",
            type="string"
        ),
        ColumnDefinition(
            name="URL",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.NON_UPTABLE,
            description="Extract the direct URL pointing to the product page. " \
            "Example: https://supermercado.eroski.es/en/productdetail/24691412-aperitivo-de-yorkqueso-jumpers-bolsa-100-g/.",
            type="string"
        ),
        ColumnDefinition(
            name="Description",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.NON_UPTABLE,
            description="Concatenate ALL available textual information from source columns "
            "(e.g., description, denomination, characteristics, properties, product name, category, etc.) " \
            "into a single, organized string. Crucially, avoid repeating any information and do not use " \
            "line breaks, emojis, or non-standard UTF-8 symbols. " \
            "Example 1: Original data -> {'description': 'Exotic fruit ideal for use as fresh produce, pastries and cocktails.', 'denomination': 'Granadilla tray 500 g'} " \
            "Output -> {'description': 'Exotic fruit ideal for use as fresh produce, pastries and cocktails. Granadilla tray 500 g.'} " \
            "Example 2: Original data -> {'product': 'LG XBOOM XL7S The Beast High Power Speaker, with 250 W of Power, Up to 20 Hours of Battery and IPX4 Water Resistance, with Screen and LED Lighting, Have Fun Sending Messages', 'description': None} " \
            "Output -> {'description': 'LG XBOOM XL7S The Beast High Power Speaker, with 250 W of Power, Up to 20 Hours of Battery and IPX4 Water Resistance, with Screen and LED Lighting, Have Fun Sending Messages.'}",
            type="string"
        ),
        ColumnDefinition(
            name="Format",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.FORMAT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Identify the packaging type. Translate to English if necessary " \
            "Example: BOTTLE -> Bottle; BAG/BOLSON -> Bag; TARRINA -> Tub.",
            type="string"
        ),
        ColumnDefinition(
            name="Unit_measure",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Identify the unit of measure for the product quantity (e.g., kilograms, grams, liters, units). " \
            "This unit is often found next to the numerical weight/volume value. " \
            "Convert all to lowercase. Example: KG -> kg, G -> g, L -> l, UD -> ud.",
            type="string"
        ),
        ColumnDefinition(
            name="Measure_value",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Extract the numerical value representing the product weight/volume. " \
            "Use a dot (.) as the decimal separator." \
            "If the product as pack of more than one units, just extract and calculate the value of a single unit. " \
            "Example 1: 3x200 g -> 200. " ,
            type="float"
        ),
        ColumnDefinition(
            name="Quantity",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.QUANTITY,
            uptable=UptableColumn.NON_UPTABLE,
            description="Extract the numerical value representing the product quantity. " \
            "Specially when it is a pack of more than one unit, just extract and number of product units. " \
            "Example 1: 3x200 g -> 3. " ,
            type="integer"
        ),
        ColumnDefinition(
            name="Price",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of product node
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Express the numerical value of the price (with VAT) with two decimal places, " \
            "using a dot (.) as the decimal separator. Do not use commas for thousands. " \
            "Example: 1,8 euros -> 1.80.",
            type="float"
        ),
        ColumnDefinition(
            name="Unit_Price",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of product node
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Express the numerical value of the price per unit (with VAT) with two decimal places, " \
            "using a dot (.) as the decimal separator. Do not use commas for thousands. " \
            "Example: 1,8 euros -> 1.80 " \
            "Example: 2x4 euros -> 2.00",
            type="float"
        ),
        ColumnDefinition(
            name="Price_with_offer",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Price of the product including any offer discounts.",
            type="float"
        ),
        ColumnDefinition(
            name="Offer",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.OFFER,
            uptable=UptableColumn.UPTABLE,
            description="Find information about offers such as 20% / 3x2/ compra más por menos",
            type="string"
        ),
        ColumnDefinition(
            name="Currency",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of country - product
            belongs_to=RelationshipTypes.IS_SOLD_IN_COUNTRY,
            uptable=UptableColumn.UPTABLE,
            description="Select the currency. Express it in the ISO 4217 3-letter uppercase format. " \
            "Example: EUR for euro.",
            type="string"
        ),
        ColumnDefinition(
            name="VAT",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of country - product
            belongs_to=RelationshipTypes.IS_SOLD_IN_COUNTRY,
            uptable=UptableColumn.UPTABLE,
            description="Extract the VAT amount as a float, without the percentage symbol. Output must be a number (e.g., 21 for 21%). No symbols, text, or extra formatting.",
            type="float"
        ),
        ColumnDefinition(
            name="Price_without_VAT",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store-product relation
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Price of the product excluding VAT.",
            type="float"
        ),
        ColumnDefinition(
            name="Postcode",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE_PROPERTY, # property of store node
            belongs_to=NodeTypes.STORE,
            uptable=UptableColumn.NON_UPTABLE,
            description="Postal code of the supermarket location, " \
            "Example: EROSKI in Vitoria 01013 -> 01013.", 
            type="string"
        ),
        ColumnDefinition(
            name="Store",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.STORE,
            uptable=UptableColumn.NON_UPTABLE,
            description="Name of the store selling the product. This store includes postcode and location name if available in the following format: (company_name)-[postcode]-[location_name] " \
            "Add underscores (_) to separate words in the location name. " \
            "If there is no location because it is an online store, often named as marketplaces, just use (company_name)-Online. " \
            "Examples: Makro 010013 Vitoria Polígono-> Makro-010013-Vitoria_Polígono; PcComponentes España -> PcComponentes-Online.",
            type="string"
        ),
        ColumnDefinition(
            name="Company",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.NODE,
            belongs_to=NodeTypes.COMPANY,
            uptable=UptableColumn.NON_UPTABLE,
            description="Name of the company operating the store. Do not include other information besides the company name." \
            "Example: Eroski Super in Vitoria (physical store with online sales) -> Eroski; Amazon Spain (only online sales) -> Amazon.",
            type="string"
        ),
        ColumnDefinition(
            name="Shipping_Cost",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Cost of shipping for the product.",
            type="float"
        ),
        ColumnDefinition(
            name="Instalation_Cost",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Cost of installation for the product.",
            type="float"
        ),
        ColumnDefinition(
            name="Seller",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.NON_UPTABLE,
            description="Name of the product seller.",
            type="string"
        ),
        ColumnDefinition(
            name="Days_of_shipping",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Estimated days for shipping the product.",
            type="int"
        ),
        ColumnDefinition(
            name="Country",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE, # country node
            belongs_to=NodeTypes.COUNTRY,
            uptable=UptableColumn.NON_UPTABLE,
            description="Provide the Country Code using the ISO 3166-1 Alpha-2 standard. If it is explicitly in data, try to infer based on the location where it is sold, etc." \
            "Example: Spain -> ES; United States of America -> US.",
            type="string"
        ),
        # TODO: DECIDE THE DATABASE TYPE 
        # ColumnDefinition(
        #     name="Nutritional_Value",
        #     presence=ColumnPresence.OPTIONAL,
        #     translatable=TranslatableColumn.NON_TRANSLATABLE,
        #     databasetype=DatabaseType.NEO4J_DB,
        #     llmstatus=LLMStatus.LLM_COMPLETE,
        #     representation=RoleInGraph.NODE_PROPERTY, # property of product
        #     relation=ProductRelation.PRODUCT_ATTRIBUTE,
        #     description="Información Nutricional / Cantidad 100 gramos / Energía 463 kilocaloría it (international table) / Energía 1926 kilojulios / Grasas 39 gramos / Ácidos grasos saturados 28 gramos / Hidratos de carbono 2.2 gramos / Azúcares 1 gramosroteínas 25 gramos / Sal 1.9 gramos",
        #     type="string"
        # ),
        ColumnDefinition(
            name="Ingredients",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.RELATIONSHIP, # product - ingredients relation
            belongs_to=RelationshipTypes.OTHER_INGREDIENT,
            uptable=UptableColumn.NON_UPTABLE,
            description="If Food, detail all ingredients with their percentages (if available), separated by commas. " \
            "Example: Microwave prepared broccoli (75%) cooked with ham (10%) and olive oil (5%) -> Broccoli (75%), Ham (10%), Olive Oil (5%)",
            type="string"
        ),
        ColumnDefinition(
            name="Components",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.RELATIONSHIP, # product - components relation
            belongs_to=RelationshipTypes.OTHER_COMPONENT,
            uptable=UptableColumn.NON_UPTABLE,
            description="If Non-Food, detail all main specifications/components, separated by commas. " \
            "Example: HP AMD Ryzen 7 5800x 16GB RAM with LED and Bluetooth -> AMD Ryzen 7 5800x, 16GB RAM, LED lights, Bluetooth.",
            type ="string"
        ),
        ColumnDefinition(
            name="First_level_components",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP, # product - ingredients/components relation
            belongs_to=RelationshipTypes.FIRST_LEVEL_COMPONENT,
            uptable=UptableColumn.NON_UPTABLE,
            description="If Food, extract the main ingredient. " \
            "If Non-Food, extract the main component (e.g., CPU chip). " \
            "Only one component/ingredient name. NO percentages, numbers, or extra text. " \
            "Example: Microwave prepared broccoli (75%) cooked with ham (10%) and olive oil (5%) -> Broccoli; " \
            "Example: HP AMD Ryzen 7 5800x 16GB RAM with LED and Bluetooth -> AMD Ryzen 7.",
            type ="string"
        ),
        ColumnDefinition(
            name="First_component_extra_info",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of product - ingredients/components relation
            belongs_to=RelationshipTypes.FIRST_LEVEL_COMPONENT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Save detail information related with the first_level_component. " \
            "Extract the numerical percentage or single descriptive quality. " \
            "Example: Microwave prepared broccoli (75%) cooked with ham (10%) and olive oil (5%) -> 75%, Microwave prepared ;" \
            "Example: HP AMD Ryzen 7 5800x 16GB RAM with LED and Bluetooth -> 5800x.",
            type="string"
        ),
        ColumnDefinition(
            name="Second_level_components",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP, # product - ingredients/components relation
            belongs_to=RelationshipTypes.SECOND_LEVEL_COMPONENT,
            uptable=UptableColumn.NON_UPTABLE,
            description="If Food, extract the second most important ingredient. " \
            "If Non-Food, extract the second most important component (e.g., RAM memory). " \
            "Only one component/ingredient name. NO percentages, numbers, or extra text. " \
            "Example: Microwave prepared broccoli (75%) cooked with ham (10%) and olive oil (5%) -> Ham; " \
            "Example: HP AMD Ryzen 7 5800x 16GB RAM with LED and Bluetooth -> 16GB RAM.",
            type ="string"
        ),
        ColumnDefinition(
            name="Second_component_extra_info",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of product - ingredients/components relation
            belongs_to=RelationshipTypes.SECOND_LEVEL_COMPONENT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Save the percentage or other specified qualities related to the second ingredient/component. " \
            "Extract the numerical percentage or single descriptive quality. " \
            "Use None if no info is available. " \
            "Example: Microwave prepared broccoli (75%) cooked with ham (10%) and olive oil (5%) -> 10%; " \
            "HP AMD Ryzen 7 5800x 16GB RAM with LED and Bluetooth -> None.",
            type="string"
        ),
        ColumnDefinition(
            name="General_characteristics",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.MONGO_DB,
            llmstatus=LLMStatus.SOURCE_ONLY,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.NON_UPTABLE,
            description="General characteristics of the product.",
            type="string"
        ),
        ColumnDefinition(
            name="Conservation_characteristics",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.NON_UPTABLE,
            description="Conservation characteristics of the product. " \
            "Example: Conserve between +1ºC and +8º C. Keep in a cool, dry place away from sunlight.",
            type="string"
        ),
        ColumnDefinition(
            name="Manufacturer",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of product - brand
            belongs_to=RelationshipTypes.FROM_BRAND,
            uptable=UptableColumn.NON_UPTABLE,
            description="Manufacturer of the product following the format: Name, Direction. " \
            "Example: Lactalis Forlasa S.L.U, Avda.Reyes Católicos.135.02600 Villarrobledo",
            type="string"
        ),
        ColumnDefinition(
            name="Country_origin", 
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP, # relation between product - country
            belongs_to=RelationshipTypes.COUNTRY_OF_ORIGIN,
            uptable=UptableColumn.NON_UPTABLE,
            description="Country of origin of the product (express the country following ISO 3166-1 alpha-2 standard). " \
            "Example: ES, FR, IT.",
            type="string"
        ),
        ColumnDefinition(
            name="Colour",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Extract the colour of the product as a single word in Title Case. No extra words, symbols, or numbers. Example: Red, Blue, Green.",
            type="string"
        ),
        ColumnDefinition(
            name="Model",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.BOTH,
            representation=RoleInGraph.NODE_PROPERTY, # property of product node
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Model of the product. Example : Iphone 17 Pro Max -> 17 Pro Max",
            type="string"
        ),
        ColumnDefinition(
            name="Reviews",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.MONGO_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of product - store
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Customer reviews of the product.",
            type="string"
        ),
        ColumnDefinition(
            name="Allergens", # TODO: MIRAR COMO HACERLO SEPARABLE
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.TARGET_ONLY,
            representation=RoleInGraph.RELATIONSHIP, # relation between product - ingredients
            belongs_to=RelationshipTypes.ALLERGENS,
            uptable=UptableColumn.NON_UPTABLE,
            description="List all the allergens present in the product separated by commas. " \
            "Example: Contains gluten, milk 90%, and Nuts 10% -> gluten, milk, nuts.",
            type="string"
        ),
        ColumnDefinition(
            name="Specifications",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.MONGO_DB,
            llmstatus=LLMStatus.SOURCE_ONLY,
            representation=RoleInGraph.NODE_PROPERTY, # property of product
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Specifications of the product.",
            type="string"
        ),
        ColumnDefinition(
            name="Documents_Safety_instructions",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.MONGO_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE_PROPERTY, # property of product
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.NON_UPTABLE,
            description="Documents and safety instructions for the product.",
            type="string"
        ),
        ColumnDefinition(
            name="Additional_information_More_information",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.MONGO_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Additional information about the product.",
            type="string"
        ),
        ColumnDefinition(
            name="Sponsored_products",
            presence=ColumnPresence.REQUIRED,
            translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
            databasetype=DatabaseType.MONGO_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of store - product
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Sponsored products related to the product.",
            type="string"
        )
    ]
    
    for column in default_columns:
        registry.add_column(column)

    # Use absolute path based on the project root
    registry_path = PROJECT_ROOT / "data" / "schemas" / "column_registry.json"
    registry.save_to_json(registry_path)
    
    return registry
