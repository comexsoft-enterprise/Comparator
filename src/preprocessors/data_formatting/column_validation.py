import logging
import os
import re
import uuid
import sys
import hashlib
import pandas as pd
from typing import Dict, Any, Union
from datetime import datetime
from config.settings import PROJECT_ROOT
sys.path.insert(0, str(PROJECT_ROOT))

from data.schemas.taxonomy import (
    ColumnRegistry, 
    ColumnDefinition, 
    ColumnPresence, 
    RoleInGraph,
    NodeTypes,
    RelationshipTypes,
    TranslatableColumn,
    DatabaseType,
    LLMStatus,
    UptableColumn,
    create_default_registry,
)

from src.preprocessors.data_formatting.path_handling import (
    validate_file_exists,
    identify_file_type,
    get_file_extension,
    get_organized_file_path,
)

# 1. Create dataframe from file
def load_data_file(file_path: str) -> Union[pd.DataFrame, Dict[str, Any], None]:
    """
    Load data from a file based on its type (CSV, Excel, or JSON).
    
    Args:
        file_path (str): Path to the data file
        
    Returns:
        Union[pd.DataFrame, Dict, None]: 
            - DataFrame for CSV and Excel files
            - Dictionary for JSON files
            - None if file cannot be loaded or doesn't exist
            
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file type is not supported
    """
    if not validate_file_exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    file_type = identify_file_type(file_path)
    
    filename = os.path.basename(file_path)
    
    try:
        logging.info(f"📂 Loading file: {filename}")
        logging.info(f"📍 Full path: {file_path}")
        logging.info(f"📋 File type: {file_type}")
        
        if file_type == 'csv':
            data = pd.read_csv(file_path, sep=";", encoding='utf-8')
            logging.info(f"✅ Successfully loaded CSV: {filename}")
            logging.info(f"   - Shape: {data.shape} (rows, columns)")
            return data
        elif file_type == 'xlsx':
            data = pd.read_excel(file_path, dtype={"id": str, "ean": str})
            logging.info(f"✅ Successfully loaded Excel: {filename}")
            logging.info(f"   - Shape: {data.shape} (rows, columns)")
            return data
        elif file_type == 'json':
            data = pd.read_json(file_path)
            logging.info(f"✅ Successfully loaded JSON: {filename}")
            logging.info(f"   - Shape: {data.shape} (rows, columns)")
            return data
        else:
            raise ValueError(f"Unsupported file type: {get_file_extension(file_path)}")
    except Exception as e:
        logging.error(f"❌ Error loading file {filename}: {str(e)}")
        return None


def load_data_from_organized_structure(filename: str, base_path: str = "data/raw") -> Union[pd.DataFrame, Dict[str, Any], None]:
    """
    Load data from a file in the organized directory structure.
    
    Args:
        filename (str): Name of the file to load
        base_path (str): Base path for data directory (default: "data/raw")
        
    Returns:
        Union[pd.DataFrame, Dict, None]: Loaded data or None if file cannot be loaded
    """
    logging.info(f"🔍 Looking for file: {filename}")
    logging.info(f"📁 Base path: {base_path}")
    file_path = get_organized_file_path(filename, base_path)
    return load_data_file(file_path)


# 2. Prepare DataFrame to be compatible with Neo4j import.
# That is, clean column names, set column names to lowercase, etc.
def prepare_for_taxonomy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare DataFrame for Neo4j node import by adding required headers and formatting.
    
    Args:
        df (pd.DataFrame): Source DataFrame
        id_column (str): Name of the column to use as node ID
        label (str): Neo4j label for the nodes
        
    Returns:
        pd.DataFrame: DataFrame formatted for Neo4j node import
    """
    result_df = df.copy()
    
    # Clean column names (remove spaces and special characters)
    new_columns = {}
    for col in result_df.columns:
        clean_col = col.replace(' ', '_').replace('-', '_').replace('.', '_').lower()
        new_columns[col] = clean_col
    
    result_df = result_df.rename(columns=new_columns)
    logging.info(f"Columns in the input DataFrame after converting to lowercase: {new_columns.values()}")
    return result_df

def clean_cell_characters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean characters in DataFrame cells by converting line breaks to \\n.
    
    Args:
        df (pd.DataFrame): DataFrame to clean
        
    Returns:
        pd.DataFrame: DataFrame with cleaned cell characters
    """
    result_df = df.copy()
    
    # Get all object (string) columns
    string_columns = result_df.select_dtypes(include=['object']).columns
    
    # Clean each string column
    for col in string_columns:
        # Replace actual line breaks with \n string
        result_df[col] = result_df[col].astype(str).str.replace('\r\n', '\\n', regex=False)
        result_df[col] = result_df[col].astype(str).str.replace('\n', '\\n', regex=False)
        result_df[col] = result_df[col].astype(str).str.replace('\r', '\\n', regex=False)
    
    logging.info(f"Cleaned characters in {len(string_columns)} string columns")
    
    return result_df


# 3. Clean null values in the DataFrame
def clean_null_values(df: pd.DataFrame, replace_with: None) -> pd.DataFrame:
    """
    Replace None, NaN, and other null-like values with a specified string.
    
    Args:
        df (pd.DataFrame): DataFrame to clean
        replace_with (str): String to replace null values with (default: "null")
        
    Returns:
        pd.DataFrame: DataFrame with cleaned null values
    """
    result_df = df.copy()
    
    # Count null values before cleaning
    null_counts_before = result_df.isnull().sum()
    total_nulls_before = null_counts_before.sum()
    
    # Replace various null-like values
    result_df = result_df.fillna(replace_with)
    
    # Also replace string representations of null values
    null_strings = ['None', 'nan', 'NaN', 'NULL', 'null', '', ' ']
    for null_str in null_strings:
        result_df = result_df.replace(null_str, replace_with)
    
    # Count changes made
    logging.info(f"Replaced {total_nulls_before} null values with '{replace_with}'")
    
    if total_nulls_before > 0:
        columns_with_nulls = null_counts_before[null_counts_before > 0]
        for col, count in columns_with_nulls.items():
            logging.info(f"  Column '{col}': {count} null values replaced")
    
    return result_df

# 4. Check and split category paths
def check_and_split_category_paths(df: pd.DataFrame, category_column: str = "category") -> pd.DataFrame:
    """
    Check if the category column contains hierarchical paths and split them if found.
    
    Args:
        df (pd.DataFrame): DataFrame to check and process
        category_column (str): Name of the category column to check (default: "category")
        
    Returns:
        Dict[str, Any]: Results containing validation info and processed DataFrame
    """

    # Get valid category data
    category_data = df[category_column]
    if category_data.empty:
        logging.warning(f"Category column '{category_column}' contains no valid data")
        return None
    
    # Normalize and split categories
    processed_df = df.copy()
    
    # Ensure 'category' column exists even if no normalization is applied
    processed_df['category'] = processed_df.get('category', processed_df[category_column])

    if category_data.str.contains('>', na=False).any():
        processed_df['category'] = processed_df[category_column].str.replace('>', '/', regex=False)

    if category_data.str.contains('/', na=False).any():
        category_splits = processed_df['category'].str.split('/', expand=True)

        # Clean and rename split columns
        max_levels = category_splits.shape[1]
        level_columns = [f'category_level_{i+1}' for i in range(max_levels)]
        category_splits.columns = level_columns

        # Clean whitespace and empty strings in one operation
        for col in level_columns:
            category_splits[col] = category_splits[col].str.strip().replace('', None)

        # Combine DataFrames and update result
        processed_df = pd.concat([processed_df, category_splits], axis=1)

        logging.info(f"Split categories into {max_levels} levels: {level_columns}")
    
    return processed_df


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize DataFrame - standardizes nutritional units
    Converts all nutritional values to standard units
    """
    # Standard units for each nutritional component
    unit_conversions = {
        'nutrition_information_calories': {
            'standard_unit': 'kcal',
            'conversions': {
                'kcal': 1,
                'kj': 0.239006,  # kJ to kcal
                'cal': 0.001      # cal to kcal
            }
        },
        'nutrition_information_fat': {
            'standard_unit': 'g',
            'conversions': {
                'g': 1,
                'mg': 0.001,      # mg to g
                'kg': 1000        # kg to g
            }
        },
        'nutrition_information_carbohydrates': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        },
        'nutrition_information_fiber': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        },
        'nutrition_information_protein': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        },
        'nutrition_information_salt': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        },
        'nutrition_information_sugars': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        },
        'nutrition_information_saturatedfattyacids': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        },
        'nutrition_information_monounsaturatedfattyacids': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        },
        'nutrition_information_polyunsaturatedgradeacids': {
            'standard_unit': 'g',
            'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
        }
    }
    
    # Standardize each nutritional component
    for nutrient, config in unit_conversions.items():
        value_col = f"{nutrient}_value"
        unit_col = f"{nutrient}_unit"
        
        # Skip if columns don't exist
        if value_col not in df.columns or unit_col not in df.columns:
            continue
        
        # Create mask for rows that need conversion
        needs_conversion = df[unit_col].notna() & (df[unit_col] != '')
        
        if not needs_conversion.any():
            continue
        
        # Convert values based on their current unit
        for current_unit, conversion_factor in config['conversions'].items():
            mask = needs_conversion & (df[unit_col].str.lower() == current_unit.lower())
            if mask.any():
                # Convert string values to float, handling errors
                values = pd.to_numeric(df.loc[mask, value_col], errors='coerce')
                df.loc[mask, value_col] = values * conversion_factor
                df.loc[mask, unit_col] = config['standard_unit']
        
        logging.info(f"Normalized {nutrient} to {config['standard_unit']}")
    
    # Normalize product measure (unit_measure and measure_value columns)
    if 'unit_measure' in df.columns and 'measure_value' in df.columns:
        logging.info("Normalizing product measures (unit_measure, measure_value)...")
        
        # Define conversions for volume (to Liters) and weight (to grams)
        measure_conversions = {
            # Volume units → L (liters)
            'ml': {'target': 'L', 'factor': 0.001},
            'mililitros': {'target': 'L', 'factor': 0.001},
            'cl': {'target': 'L', 'factor': 0.01},
            'centilitros': {'target': 'L', 'factor': 0.01},
            'dl': {'target': 'L', 'factor': 0.1},
            'decilitros': {'target': 'L', 'factor': 0.1},
            'l': {'target': 'L', 'factor': 1},
            'litros': {'target': 'L', 'factor': 1},
            'litre': {'target': 'L', 'factor': 1},
            'litres': {'target': 'L', 'factor': 1},
            
            # Weight units → g (grams)
            'mg': {'target': 'g', 'factor': 0.001},
            'miligramos': {'target': 'g', 'factor': 0.001},
            'g': {'target': 'g', 'factor': 1},
            'gr': {'target': 'g', 'factor': 1},
            'gramos': {'target': 'g', 'factor': 1},
            'kg': {'target': 'g', 'factor': 1000},
            'kilogramos': {'target': 'g', 'factor': 1000},
            'kgs': {'target': 'g', 'factor': 1000},
        }
        
        # Create mask for rows that need conversion
        needs_conversion = df['unit_measure'].notna() & (df['unit_measure'] != '')
        
        if needs_conversion.any():
            conversions_made = 0
            
            for unit_str, conversion in measure_conversions.items():
                # Match unit case-insensitively
                mask = needs_conversion & (df['unit_measure'].str.lower().str.strip() == unit_str.lower())
                
                if mask.any():
                    # Convert values
                    values = pd.to_numeric(df.loc[mask, 'measure_value'], errors='coerce')
                    df.loc[mask, 'measure_value'] = values * conversion['factor']
                    df.loc[mask, 'unit_measure'] = conversion['target']
                    conversions_made += mask.sum()
            
            if conversions_made > 0:
                logging.info(f"Normalized {conversions_made} product measures to standard units (L/g)")
            else:
                logging.info("No product measure conversions needed")
        else:
            logging.info("No product measures to normalize")
    
    return df


def calculate_price_without_vat(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate price_without_vat based on price and vat columns
    Only calculates when both price and vat are valid (not null, not empty, not 0)
    Also handles unit_price_without_vat and price_without_vat_with_offer
    """
    # Create case-insensitive column mapping
    col_map = {col.lower(): col for col in df.columns}
    
    if 'vat' not in col_map:
        logging.info("No VAT column found, skipping price_without_vat calculation")
        return df
    
    vat_col = col_map['vat']
    
    # Define price pairs: (source_price_col_pattern, target_without_vat_col_pattern)
    price_pairs = [
        ('unit_price', 'unit_price_without_vat'),
        ('price_with_offer', 'price_without_vat_with_offer'),
    ]
    
    # Also handle generic price columns (excluding the specific ones above)
    generic_price_cols = [col for col in df.columns 
                         if 'price' in col.lower() 
                         and 'without_vat' not in col.lower()
                         and col.lower() not in ['unit_price', 'price_with_offer']]
    
    # Process specific price pairs
    for source_pattern, target_pattern in price_pairs:
        if source_pattern not in col_map:
            continue
        
        source_col = col_map[source_pattern]
        target_col = target_pattern
        
        # Create mask for valid calculations
        mask = (
            df[source_col].notna() & 
            (df[source_col] != '') & 
            (df[source_col] != 0) &
            df[vat_col].notna() & 
            (df[vat_col] != '') & 
            (df[vat_col] != 0)
        )
        
        if mask.any():
            # Calculate price without VAT
            price_numeric = pd.to_numeric(df.loc[mask, source_col], errors='coerce')
            vat_numeric = pd.to_numeric(df.loc[mask, vat_col], errors='coerce')
            
            # Create target column if it doesn't exist
            if target_col not in df.columns:
                df[target_col] = None
            
            # Formula: price_without_vat = price / (1 + vat/100)
            df.loc[mask, target_col] = price_numeric / (1 + vat_numeric / 100)
            
            logging.info(f"Calculated {target_col} from {source_col} for {mask.sum()} rows")
    
    # Process generic price columns
    for price_col in generic_price_cols:
        target_col = f"{price_col}_without_vat"
        
        # Create mask for valid calculations
        mask = (
            df[price_col].notna() & 
            (df[price_col] != '') & 
            (df[price_col] != 0) &
            df[vat_col].notna() & 
            (df[vat_col] != '') & 
            (df[vat_col] != 0)
        )
        
        if mask.any():
            # Calculate price without VAT
            price_numeric = pd.to_numeric(df.loc[mask, price_col], errors='coerce')
            vat_numeric = pd.to_numeric(df.loc[mask, vat_col], errors='coerce')
            
            # Create target column if it doesn't exist
            if target_col not in df.columns:
                df[target_col] = None
            
            # Formula: price_without_vat = price / (1 + vat/100)
            df.loc[mask, target_col] = price_numeric / (1 + vat_numeric / 100)
            
            logging.info(f"Calculated {target_col} from {price_col} for {mask.sum()} rows")
    
    return df


def check_extra_column_type(df: pd.DataFrame, column_name: str) -> str:
    """
    Infer what is the type of a given column in a DataFrame.
    Args:
        df (pd.DataFrame): DataFrame containing the column
        column_name (str): Name of the column to check
    Returns:
        str: Inferred type of the column (e.g., 'string', 'integer', 'float', 'boolean', 'datetime')
    """

    column = df[column_name]
    
    # Get the pandas dtype
    dtype = column.dtype
    
    # Check for numeric types
    if pd.api.types.is_integer_dtype(dtype):
        return 'integer'
    elif pd.api.types.is_float_dtype(dtype):
        return 'float'
    elif pd.api.types.is_bool_dtype(dtype):
        return 'boolean'
    elif pd.api.types.is_datetime64_any_dtype(dtype):
        return 'datetime'
    
    # For object dtype, need to inspect actual values
    if dtype == 'object':
        # Remove null values for inspection
        non_null_values = column.dropna()

        if len(non_null_values) == 0:
            return 'none'  # Default to string if all null
        
        # Sample first non-null value
        sample_value = non_null_values.iloc[0]
        
        # Check if it's a dict
        if isinstance(sample_value, dict):
            return 'dict'
        
        # Check if it's a list
        if isinstance(sample_value, (list, tuple)):
            return 'list'
        
        # Check if it's a boolean stored as string
        unique_values = non_null_values.unique()
        if len(unique_values) <= 2 and all(str(v).lower() in ['true', 'false', '1', '0', 'yes', 'no'] for v in unique_values):
            return 'boolean'
        
        # Check for URLs - more robust pattern matching
        # Sample multiple values to be more confident
        sample_size = min(10, len(non_null_values))
        sample_values = non_null_values.sample(n=sample_size, random_state=42)
        
        # URL regex pattern (covers http, https, ftp, www, and domain.com patterns)
        url_pattern = re.compile(
            r'^(?:(?:https?|ftp)://)?'  # Optional protocol
            r'(?:www\.)?'  # Optional www
            r'(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}'  # Domain name
            r'(?:/[^\s]*)?$',  # Optional path
            re.IGNORECASE
        )
        
        # Check if majority of sampled values are URLs
        url_count = sum(1 for val in sample_values if url_pattern.match(str(val)))
        if url_count >= sample_size * 0.8:  # 80% threshold
            return 'url'
        
        # Try to convert to numeric
        try:
            pd.to_numeric(non_null_values, errors='raise')
            # If all values are integers
            if all(float(x).is_integer() for x in non_null_values if pd.notna(x)):
                return 'integer'
            return 'float'
        except (ValueError, TypeError):
            pass

        return 'string'

# 5. Full processing pipeline
def process_dataframe_standards(df: pd.DataFrame, 
                                null_replacement: str = None, filename: str = None) -> pd.DataFrame:
    """
    Apply all standardization processes to a DataFrame:
    1. Add missing required columns
    2. Process and split category paths if found
    3. Clean null values
    
    Args:
        df (pd.DataFrame): DataFrame to process
        required_columns (List[str]): List of required column names
        null_replacement (str): String to replace null values with
        process_categories (bool): Whether to automatically process category paths
        
    Returns:
        pd.DataFrame: Processed and standardized DataFrame
    """

    # 1. Create the file where column names will be saved. Save there the default required columns
    create_default_registry()

    # 1.5. Convert all column names to lowercase
    logging.debug("Converting all column names to lowercase...")
    df.columns = df.columns.str.lower()
    logging.debug(f"✓ Converted {len(df.columns)} column names to lowercase")

    # 2. The registry is loaded from the file that has been just created
    registry_path = PROJECT_ROOT / "data" / "schemas" / "column_registry.json"
    registry = ColumnRegistry.load_from_json(registry_path)

    # 3. Validation of dataframe columns: find missing required columns and extra columns
    result_df = prepare_for_taxonomy(df)
    received_columns = result_df.columns.tolist()

    validation_result = registry.validate_dataframe_columns(received_columns)
    logging.info(f"Column validation - Missing required: {validation_result.missing_required}, Extra: {validation_result.extra_columns}")
    
    # 4. Add missing required columns
    for missing_col in validation_result.missing_required:
        result_df[missing_col.lower()] = null_replacement  # O algún valor por defecto

    # 5. Process and split category paths if found
    if 'category' in result_df.columns:
        result_df = check_and_split_category_paths(result_df, category_column='category')

    # 5.5. Clean special characters in cells
    result_df = clean_cell_characters(result_df)

    # 6. Clean null values
    result_df = clean_null_values(result_df, null_replacement)
    
    # 7. Add UUID column to each row
    logging.debug("Adding UUID column to each row...")
    result_df.insert(0, 'uuid', [str(uuid.uuid4()) for _ in range(len(result_df))])

    # 7.5 Add internal id for each supermarket
    logging.debug("Create unique_id with each supermarket name and its internal id...")

    if "asin" in result_df.columns:
        result_df['id'] = result_df['asin']

    supername = os.path.splitext(os.path.basename(filename))[0]
    logging.debug(supername)

    result_df['siid'] = supername + "-" + result_df["id"].astype(str)

    # 8. result_df Confirm that the EAN has 13 digits
    resultado = result_df['ean'].astype(str).str.fullmatch(r'\d{13}').all()

    if resultado :
        logging.debug("✓ All EANs have 13 digits")
    else:   
        logging.debug("⚠ Some EANs do not have 13 digits")

    # 10. Save the new columns in the registry

    for extra_col in validation_result.extra_columns:
        column_type = check_extra_column_type(result_df, extra_col)
        
        if column_type=='string':
            # Added with these properties by default
            new_column = ColumnDefinition(
                name=extra_col,
                presence=ColumnPresence.OPTIONAL,
                translatable=TranslatableColumn.MAY_BE_TRANSLATABLE,
                databasetype=DatabaseType.MONGO_DB,
                llmstatus=LLMStatus.SOURCE_ONLY,
                representation=RoleInGraph.RELATIONSHIP_PROPERTY, # property of product
                belongs_to=RelationshipTypes.SELLS,
                uptable=UptableColumn.UPTABLE,
                description="Columna añadida automáticamente",
                type = result_df[extra_col].dtype.name
            )
        else:
            # Added with these properties by default
            new_column = ColumnDefinition(
                name=extra_col,
                presence=ColumnPresence.OPTIONAL,
                translatable=TranslatableColumn.NON_TRANSLATABLE,
                databasetype=DatabaseType.MONGO_DB,
                llmstatus=LLMStatus.EXCLUDED,
                representation=RoleInGraph.RELATIONSHIP_PROPERTY,
                belongs_to=RelationshipTypes.SELLS,
                uptable=UptableColumn.UPTABLE,
                description="Columna añadida automáticamente",
                type = result_df[extra_col].dtype.name
            )
        registry.add_column(new_column)
    columna_uuid = ColumnDefinition(
            name="uuid",
            presence=ColumnPresence.OPTIONAL,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE_PROPERTY,
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.UPTABLE,
            description="Identificador interno del producto comun para mongo y neo4j",
            type ="string"
        )
    registry.add_column(columna_uuid)
    columna_siid = ColumnDefinition(
            name="siid",
            presence=ColumnPresence.OPTIONAL,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.NODE_PROPERTY,
            belongs_to=NodeTypes.PRODUCT,
            uptable=UptableColumn.UPTABLE,
            description="Lista de imágenes del producto",
            type ="string"
        )
    registry.add_column(columna_siid)
    columna_image_list = ColumnDefinition(
            name="image_list",
            presence=ColumnPresence.OPTIONAL,
            translatable=TranslatableColumn.NON_TRANSLATABLE,
            databasetype=DatabaseType.NEO4J_DB,
            llmstatus=LLMStatus.EXCLUDED,
            representation=RoleInGraph.RELATIONSHIP_PROPERTY,
            belongs_to=RelationshipTypes.SELLS,
            uptable=UptableColumn.UPTABLE,
            description="Lista de imágenes del producto",
            type ="string"  
        )
    registry.add_column(columna_image_list)


     # 9. Find all columns containing "image" and create image_list column
    logging.debug("Creating image_list column from image columns...")
    image_columns = [col for col in result_df.columns if 'image' in col.lower()]
    
    if image_columns:
        logging.debug(f"Found {len(image_columns)} image columns: {image_columns}")
        
        def combine_image_values(row):
            """Combine all image column values into a single dict with original column names as keys"""
            images = {}
            for col in image_columns:
                value = row[col]
                # Skip null, None, empty strings, and 'null' string values
                if pd.notna(value) and value not in [None, '', 'null', 'None']:
                    images[col] = str(value)
            return images if images else None
        
        # Apply the function to create image_list
        result_df['image_list'] = result_df.apply(combine_image_values, axis=1)
        logging.debug(f"✓ Created image_list column with combined values from {len(image_columns)} image columns")
        
    else:
        logging.debug("⚠ No columns containing 'image' found in the DataFrame")
        result_df['image_list'] = None

    
    logging.info(f"Finished processing. Final shape: {result_df.shape[0]} rows, {result_df.shape[1]} columns")
    logging.info(f"Final columns: {result_df.columns.tolist()}")
    

    # Paso final: Generar columna de hash sobre el DataFrame final

    
    # Cargar el registro de columnas para obtener las columnas uptable
    uptable_cols = registry.get_uptable_columns()
    exclude_cols = [col.name.lower() for col in uptable_cols]
    
    # Obtener las columnas del DataFrame que NO están en la lista de exclusión
    hash_cols = [col for col in result_df.columns if col.lower() not in exclude_cols]
    
    logging.debug(f"Columns excluded from hash: {exclude_cols}")
    logging.debug(f"Columns included in hash: {hash_cols}")
    
    def row_hash(row):
        values = [str(row[col]) for col in hash_cols]
        concat = '|'.join(values)
        return hashlib.sha256(concat.encode('utf-8')).hexdigest()
    
    result_df.insert(0, 'product_hash', result_df.apply(row_hash, axis=1))

    product_hash = ColumnDefinition(
        name="product_hash",
        presence=ColumnPresence.OPTIONAL,
        translatable=TranslatableColumn.NON_TRANSLATABLE,
        databasetype=DatabaseType.NEO4J_DB,
        llmstatus=LLMStatus.EXCLUDED,
        representation=RoleInGraph.NODE_PROPERTY,
        belongs_to=NodeTypes.PRODUCT,
        uptable=UptableColumn.NON_UPTABLE,
        description="Hash único generado a partir de las columnas del producto",
        type ="string"  
    )
    registry.add_column(product_hash)
    registry.save_to_json(registry_path)
    
    # Normalize nutritional units and calculate prices without VAT
    logging.info("Normalizing nutritional units...")
    result_df = normalize_dataframe(result_df)
    
    logging.info("Calculating prices without VAT...")
    result_df = calculate_price_without_vat(result_df)
    
    return result_df


def save_neo4j_csv(df: pd.DataFrame, output_path: str, filename: str = None) -> bool:
    """
    Save DataFrame as CSV formatted for Neo4j import.
    
    Args:
        df (pd.DataFrame): DataFrame to save
        output_path (str): Output file path or directory path
        filename (str, optional): Filename to use if output_path is a directory
    Returns:
        bool: True if successful, False otherwise
    """
    
    try:
        # If output_path is a directory, generate a filename
        if os.path.isdir(output_path):
            if filename is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"neo4j_data_{timestamp}.csv"
            full_path = os.path.join(output_path, filename)
        else:
            full_path = output_path
            
        # Ensure the directory exists
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        # Save the DataFrame
        df.to_csv(full_path, sep=";", index=False, encoding='utf-8')
        logging.info(f"Successfully saved Neo4j formatted CSV to: {full_path}")
        return True
    except Exception as e:
        logging.error(f"Error saving file {output_path}: {str(e)}")
        return False