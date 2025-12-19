'''
Este script sirve para poder extraer toda la información que se va a querer utilizar para crear los espacios vectoriales. 
Esto se utilizará para los archivos que se encuentren en la carpeta 'ai_consumer_goods/data/processed/validated'

- Columna 'Uuid' y 'Siid' para poder identificar los productos
- Todas las columnas que incluyen información nutricional, para ello se encontrarán todas las columnas que contengan 'nutri'
- Columna de 'Unit_Measure' y 'Measure_value'
- Información de precios. Para ello se buscarán las columnas que contengan 'price'

Una vez que se tenga toda esta información, se guardará en una base de datos PostgreSQL para su posterior uso.
Cuando se tenga toda la información en la base de datos, cuando se ejecute el modelo se procederá a crear los espacios vectoriales.

El script es capaz de:
- Crear la tabla automáticamente si no existe
- Agregar nuevas columnas dinámicamente cuando aparecen en nuevos CSVs
- Actualizar registros existentes basándose en UUID
- Procesar múltiples archivos CSV de manera incremental

Uso:
    python data_explorer.py --input-dir <ruta_csv_o_directorio>
    python data_explorer.py -i data/processed/validated/
'''


import os
import sys
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import numpy as np
from psycopg2 import sql
from psycopg2.extras import execute_batch, Json

# Importar el conector centralizado
from src.connectors.postgresql_connector import get_postgresql_connection

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from config.settings import PROJECT_ROOT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VectorDataExtractor:
    """Extract and store product data for vector embedding generation"""

    def __init__(self, db_config: Optional[Dict] = None, use_csv: bool = False):
        """
        Initialize the extractor with database configuration

        Args:
            db_config: Dictionary with PostgreSQL connection parameters (no longer used, kept for compatibility)
            use_csv: If True, use CSV file instead of database
        """
        self.use_csv = use_csv
        self.csv_path = Path(__file__).parent / 'product_vector_data.csv'

        # Columns to exclude from extraction (case-insensitive)
        self.excluded_columns = [
            'always_good_price',
        ]

        # El conector centralizado gestiona la configuración
        self.conn = None
        self.cursor = None
        
    def connect_db(self) -> bool:
        """Establish connection to PostgreSQL database using the centralized connector"""
        if self.use_csv:
            logger.info(f"📁 Using CSV mode: {self.csv_path}")
            return True

        try:
            self.conn = get_postgresql_connection()
            self.cursor = self.conn.cursor()
            logger.info(f"✅ Connected to PostgreSQL database")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to PostgreSQL: {e}")
            return False
    
    def close_db(self):
        """Close database connection"""
        if self.use_csv:
            return
        
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
            logger.info("🔌 PostgreSQL connection closed")
    
    def table_exists(self) -> bool:
        """Check if product_vector_data table exists"""
        if self.use_csv:
            return self.csv_path.exists()
        
        try:
            self.cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'product_vector_data'
                )
            """)
            return self.cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"❌ Failed to check table existence: {e}")
            return False
    
    def get_existing_columns(self) -> List[str]:
        """Get list of existing columns in the table"""
        if self.use_csv:
            if self.csv_path.exists():
                df = pd.read_csv(self.csv_path, nrows=0, sep=';')
                return df.columns.tolist()
            return []
        
        try:
            self.cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'product_vector_data'
                ORDER BY ordinal_position
            """)
            return [row[0] for row in self.cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Failed to get existing columns: {e}")
            return []
    
    def get_column_type(self, column_name: str) -> str:
        """Determine the appropriate SQL data type for a column"""
        col_lower = column_name.lower()
        
        # Integer columns (whole numbers without decimals)
        integer_indicators = [
            'vat',  # VAT is typically a whole percentage
        ]
        
        # Check if column should be integer
        if any(indicator == col_lower for indicator in integer_indicators):
            return 'INTEGER'
        
        # Numeric columns with decimals: nutritional values, prices, measures
        numeric_indicators = [
            '_value',  # nutrition_information_*_value, measure_value
            'price',   # any price column
        ]
        
        # Check if column should be numeric with decimals
        if any(indicator in col_lower for indicator in numeric_indicators):
            return 'NUMERIC(12,4)'  # Allows for large numbers with 4 decimal precision
        
        # Text columns: IDs, units, descriptions
        return 'TEXT'
    
    def add_columns_if_missing(self, new_columns: List[str]):
        """Add new columns to the table if they don't exist"""
        if self.use_csv:
            existing_cols = self.get_existing_columns()
            new_cols = [col for col in new_columns if col not in existing_cols]
            if new_cols:
                logger.info(f"➕ New columns detected: {new_cols}")
            return
        
        try:
            existing_cols = self.get_existing_columns()
            new_cols = [col for col in new_columns if col not in existing_cols]
            
            if not new_cols:
                return
            
            logger.info(f"➕ Adding new columns: {new_cols}")
            
            for col in new_cols:
                col_type = self.get_column_type(col)
                self.cursor.execute(
                    sql.SQL("ALTER TABLE product_vector_data ADD COLUMN IF NOT EXISTS {} {}")
                    .format(sql.Identifier(col), sql.SQL(col_type))
                )
                logger.info(f"   Added column '{col}' as {col_type}")

            # If the exact 'price' column is among the new columns, ensure a single `price_history` JSONB column exists
            if any(c.lower() == 'price' for c in new_cols):
                try:
                    self.cursor.execute(
                        sql.SQL("ALTER TABLE product_vector_data ADD COLUMN IF NOT EXISTS price_history JSONB DEFAULT '[]'::jsonb")
                    )
                    logger.info("   Ensured 'price_history' JSONB column exists")
                except Exception as e:
                    logger.warning(f"⚠️ Could not ensure 'price_history' column: {e}")
            self.conn.commit()
            logger.info(f"✅ Successfully added {len(new_cols)} new column(s)")
            
        except Exception as e:
            logger.error(f"❌ Failed to add columns: {e}")
            self.conn.rollback()
            raise
    
    def create_table_if_not_exists(self, columns: list):
        """
        Create the product_vector_data table if it doesn't exist
        If it exists, add any missing columns
        Args:
            columns: List of column names to create/ensure exist
        """
        if self.use_csv:
            if not self.table_exists():
                logger.info("📋 CSV file doesn't exist, will be created on first insert")
            else:
                logger.info("📋 CSV file exists, checking for new columns...")
                self.add_columns_if_missing(columns)
                logger.info("✅ CSV structure verified")
            return True
        
        try:
            if not self.table_exists():
                logger.info("📋 Creating product_vector_data table...")
                
                # Build column definitions - only include columns found in CSV
                col_defs = ["id SERIAL PRIMARY KEY"]
                
                # Only add id columns if they exist in the data
                if 'uuid' in columns:
                    col_defs.append("uuid TEXT")
                if 'product_hash' in columns:
                    col_defs.append("product_hash TEXT")
                if 'siid' in columns:
                    col_defs.append("siid TEXT")
                
                # Add all other columns with appropriate data types
                for col in columns:
                    if col not in ['uuid', 'product_hash', 'siid']:
                        col_type = self.get_column_type(col)
                        col_defs.append(f"{col} {col_type}")
                        logger.info(f"   Column '{col}' will be created as {col_type}")

                # If the exact 'price' column is present, add a global price_history JSONB column
                if any(c.lower() == 'price' for c in columns):
                    col_defs.append("price_history JSONB DEFAULT '[]'::jsonb")
                    logger.info("   Column 'price_history' will be created as JSONB (price history)")
                
                # Add created_at and updated_at timestamps
                col_defs.append("created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                col_defs.append("updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                
                create_query = f"""
                    CREATE TABLE product_vector_data (
                        {', '.join(col_defs)}
                    )
                """
                
                self.cursor.execute(create_query)
                self.conn.commit()
                logger.info("✅ Table created successfully")
                # If product_hash column exists, ensure a unique index for upsert operations
                if 'product_hash' in columns:
                    try:
                        self.cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_product_hash ON product_vector_data (product_hash)")
                        self.conn.commit()
                        logger.info("🔐 Created unique index on product_hash")
                    except Exception as e:
                        logger.warning(f"⚠️ Could not create unique index on product_hash: {e}")

                # Also create regular indexes (not UNIQUE) for uuid and siid for faster lookups
                # Only product_hash should be UNIQUE for upsert conflict resolution
                try:
                    if 'uuid' in columns:
                        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_uuid ON product_vector_data (uuid)")
                        self.conn.commit()
                        logger.info("🔍 Created index on uuid (non-unique, for fast lookups)")
                    if 'siid' in columns:
                        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_siid ON product_vector_data (siid)")
                        self.conn.commit()
                        logger.info("🔍 Created index on siid (non-unique, for fast lookups)")
                except Exception as e:
                    logger.warning(f"⚠️ Could not create indexes on uuid/siid: {e}")
            else:
                logger.info("📋 Table exists, checking for new columns...")
                self.add_columns_if_missing(columns)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to create/update table: {e}")
            self.conn.rollback()
            return False
    
    def extract_columns_from_csv(self, csv_path: str) -> pd.DataFrame:
        """
        Extract relevant columns from a CSV file
        Only extracts: uuid, siid, measure_value, unit_measure, and all columns containing 'nutri' or 'price' (case-insensitive)
        Returns DataFrame with lowercased columns for consistency.
        """
        try:
            # Read CSV with flexible separator detection
            try:
                df = pd.read_csv(csv_path, sep=';', low_memory=False)
            except Exception:
                df = pd.read_csv(csv_path, low_memory=False)

            logger.info(f"📄 Reading {csv_path}: {df.shape}")

            # Lowercase columns for matching and consistency
            df.columns = [col.lower() for col in df.columns]

            # Identify columns to extract
            required_cols = []

            # UUID, Product_hash and SIID
            for col in df.columns:
                if col in ['uuid', 'product_hash', 'siid']:
                    required_cols.append(col)

            # Measure columns
            for col in df.columns:
                if col in ['measure_value', 'unit_measure']:
                    required_cols.append(col)
            
            # VAT column
            for col in df.columns:
                if col == 'vat':
                    required_cols.append(col)

            # Nutritional columns (contain 'nutri')
            nutri_cols = [col for col in df.columns if 'nutri' in col]
            required_cols.extend(nutri_cols)

            # Price columns (contain 'price')
            price_cols = [col for col in df.columns if 'price' in col]
            required_cols.extend(price_cols)

            # Remove duplicates while preserving order
            required_cols = list(dict.fromkeys(required_cols))
            
            # Filter out excluded columns (case-insensitive)
            excluded_lower = [col.lower() for col in self.excluded_columns]
            required_cols = [col for col in required_cols if col.lower() not in excluded_lower]

            # Extract only the columns that exist
            available_cols = [col for col in required_cols if col in df.columns]

            if not available_cols:
                logger.warning(f"⚠️  No relevant columns found in {csv_path}")
                return pd.DataFrame()

            extracted_df = df[available_cols].copy()

            logger.info(f"✅ Extracted {len(available_cols)} columns, {len(extracted_df)} rows")
            id_cols = [c for c in available_cols if c in ['uuid', 'siid', 'product_hash']]
            logger.info(f"📋 Columnas UUID/SIID/PRODUCT_HASH encontradas: {id_cols}")
            logger.info(f"📋 Columnas MEASURE encontradas: {[c for c in available_cols if 'measure' in c]}")
            logger.info(f"📋 Columnas NUTRI encontradas ({len(nutri_cols)}): {nutri_cols}")
            logger.info(f"📋 Columnas PRICE encontradas ({len(price_cols)}): {price_cols}")

            return extracted_df

        except Exception as e:
            logger.error(f"❌ Failed to extract columns from {csv_path}: {e}")
            return pd.DataFrame()
    
    def insert_data(self, df: pd.DataFrame, batch_size: int = 500) -> int:
        """
        Insert normalized data into CSV or PostgreSQL as normal columns
        Args:
            df: DataFrame with all columns
            batch_size: Number of rows to insert per batch
        Returns:
            Number of rows inserted
        """
        if df.empty:
            logger.warning("⚠️  No data to insert")
            return 0
        
        if self.use_csv:
            try:
                # Read existing CSV if it exists
                if self.csv_path.exists():
                    existing_df = pd.read_csv(self.csv_path, sep=';')
                    logger.info(f"📁 Existing CSV has {len(existing_df)} records")
                    
                    # Merge with existing data (update/insert based on uuid)
                    # Remove duplicates keeping new data
                    combined_df = pd.concat([existing_df, df], ignore_index=True)
                    combined_df = combined_df.drop_duplicates(subset=['uuid'], keep='last')
                    
                    logger.info(f"📊 After merge: {len(combined_df)} total records")
                else:
                    combined_df = df
                    logger.info(f"📁 Creating new CSV with {len(combined_df)} records")
                
                # Save to CSV
                combined_df.to_csv(self.csv_path, index=False, sep=';')
                logger.info(f"✅ Saved to CSV: {self.csv_path}")
                return len(df)
                
            except Exception as e:
                logger.error(f"❌ Failed to save to CSV: {e}")
                return 0
        
        try:
            # Normalize decimal separators (comma to dot) for all numeric columns BEFORE conversion
            logger.info("🔢 Normalizing decimal separators (comma → dot) for numeric columns...")
            for col in df.columns:
                col_type = self.get_column_type(col)
                if col_type.startswith('NUMERIC') or col_type == 'INTEGER':
                    # Replace comma with dot for decimal normalization
                    df[col] = df[col].apply(
                        lambda x: str(x).replace(',', '.') if isinstance(x, str) and ',' in str(x) else x
                    )
            
            # Convert numeric columns to proper numeric types
            for col in df.columns:
                col_type = self.get_column_type(col)
                if col_type == 'INTEGER':
                    # Convert to integer, replacing empty strings and non-numeric values with None
                    df[col] = pd.to_numeric(df[col].replace(['', ' '], None), errors='coerce').astype('Int64')
                elif col_type.startswith('NUMERIC'):
                    # Convert to numeric, replacing empty strings and non-numeric values with None
                    df[col] = pd.to_numeric(df[col].replace(['', ' '], None), errors='coerce')
            
            # Replace NaN with None for proper NULL insertion
            df = df.replace({np.nan: None})
            
            # Get column names
            columns = df.columns.tolist()

            # Detect price columns (only exact 'price')
            price_cols = [c for c in columns if c.lower() == 'price']

            history_col = 'price_history'
            history_in_df = history_col in columns
            # If there are price columns and no history column in the DataFrame, append it to columns for insertion
            if price_cols and not history_in_df:
                columns.append(history_col)

            # Prepare INSERT query (recompute placeholders/columns_str if columns changed)
            placeholders = ','.join(['%s'] * len(columns))
            columns_str = ','.join([f'"{col}"' for col in columns])

            # Build records: if history column is newly added, populate it with [{price, timestamp}] JSON or NULL
            records = []
            for row in df.to_dict(orient='records'):
                rec = []
                for col in columns:
                    if col == history_col and not history_in_df:
                        # pick first available price value
                        price_val = None
                        for pc in price_cols:
                            v = row.get(pc)
                            if v is not None and v != '':
                                price_val = v
                                break
                        if price_val is None:
                            rec.append(None)
                        else:
                            try:
                                price_f = float(str(price_val).replace(',', '.').replace('€', '').replace('$', '').strip())
                                from datetime import datetime
                                timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                                rec.append(Json([{"price": price_f, "timestamp": timestamp}]))
                            except Exception:
                                from datetime import datetime
                                timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                                rec.append(Json([{"price": price_val, "timestamp": timestamp}]))
                    else:
                        rec.append(row.get(col))
                records.append(tuple(rec))

            # Use product_hash as the conflict column for upsert (matching products from verification step)
            # The verification step identifies new/modified products by product_hash
            conflict_col = 'product_hash' if 'product_hash' in columns else None
            
            if conflict_col:
                logger.info(f"🔄 Using '{conflict_col}' for upsert conflict resolution")
                # Build SET clause for update: update all columns except id and the conflict column
                update_parts = []
                for col in columns:
                    if col in ['id', conflict_col]:
                        continue
                    if col == history_col:
                        # For price_history: only append if the new price is different from the last one
                        # Compare price values inside JSON objects: {"price": X, "timestamp": "..."}
                        # Build a CASE expression for JSONB price history that safely
                        # appends a new price entry only when it's different from the last one.
                        update_parts.append(
                            f'"{col}" = CASE '
                            f'WHEN EXCLUDED."{col}" IS NULL THEN product_vector_data."{col}" '
                            f"WHEN product_vector_data.\"{col}\" IS NULL OR product_vector_data.\"{col}\" = '[]'::jsonb THEN EXCLUDED.\"{col}\" "
                            f"WHEN (product_vector_data.\"{col}\" -> -1 -> 'price')::text::numeric != (EXCLUDED.\"{col}\" -> 0 -> 'price')::text::numeric THEN product_vector_data.\"{col}\" || EXCLUDED.\"{col}\" "
                            f"ELSE product_vector_data.\"{col}\" END"
                        )
                    else:
                        update_parts.append(f'"{col}" = EXCLUDED."{col}"')

                if update_parts:
                    set_clause = ', '.join(update_parts)
                else:
                    set_clause = f'{conflict_col} = EXCLUDED.{conflict_col}'

                insert_query = f"""
                    INSERT INTO product_vector_data ({columns_str})
                    VALUES ({placeholders})
                    ON CONFLICT ({conflict_col}) DO UPDATE SET {set_clause}
                        , updated_at = CURRENT_TIMESTAMP
                """
            else:
                insert_query = f"""
                    INSERT INTO product_vector_data ({columns_str})
                    VALUES ({placeholders})
                """

            # Insert in batches
            total_inserted = 0
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                execute_batch(self.cursor, insert_query, batch)
                self.conn.commit()
                total_inserted += len(batch)
                
                if conflict_col:
                    logger.info(f"📝 Upserted batch: {total_inserted}/{len(records)} records (new products inserted, modified products updated)")
                else:
                    logger.info(f"📝 Inserted batch: {total_inserted}/{len(records)} records")

            if conflict_col:
                logger.info(f"✅ Successfully processed {total_inserted} records (upserted based on '{conflict_col}')")
            else:
                logger.info(f"✅ Successfully inserted {total_inserted} records")
            return total_inserted
            
        except Exception as e:
            logger.error(f"❌ Failed to insert data: {e}")
            self.conn.rollback()
            return 0
    
    def _to_float(self, value) -> Optional[float]:
        """Convert value to float, handling various formats"""
        if pd.isna(value) or value == '':
            return None
        try:
            if isinstance(value, str):
                # Remove currency symbols, spaces, and convert comma to dot
                value = value.replace(',', '.').replace('€', '').replace('$', '').strip()
            return float(value)
        except Exception:
            return None
    
    def process_input(self, input_path: str, drop_table: bool = False) -> int:
        """
        Process a single CSV file
        Args:
            input_path: Path to a CSV file
            drop_table: If True, drops and recreates the table
        Returns:
            Total number of records inserted
        """
        path = Path(input_path)
        if not path.exists():
            logger.error(f"❌ Path not found: {input_path}")
            return 0
        
        if not path.is_file():
            logger.error(f"❌ Input must be a file, not a directory: {input_path}")
            return 0
        
        if path.suffix.lower() != '.csv':
            logger.error(f"❌ File must be a .csv file: {input_path}")
            return 0
        
        csv_files = [path]
        logger.info(f"📄 Processing file: {path.name}")
        # Get all columns from all files (union)
        all_columns = set()
        dfs = []
        for csv_file in csv_files:
            df = self.extract_columns_from_csv(str(csv_file))
            if not df.empty:
                all_columns.update(df.columns)
                dfs.append(df)
        if not dfs:
            logger.warning("⚠️  No data extracted from any CSV file")
            return 0
        # Ensure consistent column order: uuid, product_hash, siid, measure_value, unit_measure, 
        # then rest alphabetically with value before unit for each nutrient
        priority_cols = ['uuid', 'product_hash', 'siid', 'measure_value', 'unit_measure']
        other_cols = [col for col in all_columns if col not in priority_cols]
        
        # Group by nutrient base name and order value before unit
        nutrient_pairs = {}
        standalone_cols = []
        
        for col in other_cols:
            if '_unit' in col:
                base = col.replace('_unit', '')
                if base not in nutrient_pairs:
                    nutrient_pairs[base] = {}
                nutrient_pairs[base]['unit'] = col
            elif '_value' in col:
                base = col.replace('_value', '')
                if base not in nutrient_pairs:
                    nutrient_pairs[base] = {}
                nutrient_pairs[base]['value'] = col
            else:
                standalone_cols.append(col)
        
        # Build ordered list: value then unit for each nutrient, sorted by base name
        ordered_other_cols = []
        for base in sorted(nutrient_pairs.keys()):
            if 'value' in nutrient_pairs[base]:
                ordered_other_cols.append(nutrient_pairs[base]['value'])
            if 'unit' in nutrient_pairs[base]:
                ordered_other_cols.append(nutrient_pairs[base]['unit'])
        
        # Add standalone columns at the end, sorted
        ordered_other_cols.extend(sorted(standalone_cols))
        
        all_columns = priority_cols + ordered_other_cols
        # Reindex all dataframes to have the same columns
        dfs = [df.reindex(columns=all_columns) for df in dfs]
        full_df = pd.concat(dfs, ignore_index=True)
        
        # Note: Normalization and price calculation now done in column_validation.py
        # during the VALIDATION AND STANDARDIZATION step
        
        # Connect to database
        if not self.connect_db():
            return 0
        # Safety: prevent accidental full table drop unless explicitly allowed
        allow_replace = os.getenv('ALLOW_POSTGRES_REPLACE', 'false').lower() in ('1', 'true', 'yes')
        if drop_table and not allow_replace:
            logger.error("Refusing to drop/recreate table: drop_table=True but ALLOW_POSTGRES_REPLACE is not set")
            self.close_db()
            return 0

        # Create table if not exists or add missing columns
        if not self.create_table_if_not_exists(columns=all_columns):
            self.close_db()
            return 0
        # Insert into database
        inserted = self.insert_data(full_df)
        self.close_db()
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ Processing complete!")
        logger.info(f"   Total files processed: {len(csv_files)}")
        logger.info(f"   Total records inserted/updated: {inserted}")
        logger.info(f"{'='*60}")
        return inserted


def ingest_verified_csv(verified_csv_path: str) -> int:
    """
    Convenience wrapper to ingest a verified CSV into PostgreSQL using VectorDataExtractor.

    Args:
        verified_csv_path: Path to the verified CSV file produced by the pipeline

    Returns:
        int: Number of records inserted/updated
    """
    extractor = VectorDataExtractor(use_csv=False)
    try:
        inserted = extractor.process_input(input_path=str(verified_csv_path), drop_table=False)
        logger.info(f"✅ Ingested verified CSV into PostgreSQL: {verified_csv_path} (inserted={inserted})")
        return inserted
    except Exception as e:
        logger.error(f"❌ Failed to ingest verified CSV into PostgreSQL: {e}")
        raise
    
def get_stats(self) -> Dict:
    """Get statistics about stored data"""
    if self.use_csv:
        try:
            if self.csv_path.exists():
                df = pd.read_csv(self.csv_path, sep=';')
                return {'total_records': len(df)}
            return {'total_records': 0}
        except Exception as e:
            logger.error(f"❌ Failed to get CSV stats: {e}")
            return {}
    
    try:
        self.cursor.execute("SELECT COUNT(*) FROM product_vector_data")
        total = self.cursor.fetchone()[0]
        return {'total_records': total}
    except Exception as e:
        logger.error(f"❌ Failed to get stats: {e}")
        return {}


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Extract product data for vector embeddings and store in PostgreSQL'
    )
    parser.add_argument(
        '--input-dir',
        '-i',
        default='.',
        help='Input directory or CSV file (default: current directory)'
    )
    return parser.parse_args()
