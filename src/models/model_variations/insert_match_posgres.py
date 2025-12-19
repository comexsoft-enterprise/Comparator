import psycopg2
from psycopg2.extras import execute_values
import logging
import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from config.settings import POSTGRES_DB_CONFIG

logger = logging.getLogger(__name__)

def insert_to_validate(results, initial_validation_status=None):
    """
    Insert top 1 match results into the validation table in Postgres.
    
    Args:
        results: Dictionary with product_a_id as keys and similarity data as values.
                 Format: {product_a_id: {'product_a': {...}, 'similar_products_b': [...]}}
        initial_validation_status: Optional initial validation status for all inserts.
                                   Options: 'perfect', 'correct', 'incorrect', 'uncertain'
                                   If None, records are inserted as unvalidated (validated=FALSE)
    """
    
    if not results or len(results) == 0:
        logger.warning("No results to insert into validation table")
        return
    
    # Filter out metadata
    products_to_insert = {k: v for k, v in results.items() if k != '_metadata'}
    
    if len(products_to_insert) == 0:
        logger.warning("No product matches to insert into validation table")
        return
    
    logger.info(f"📊 Preparing to insert {len(products_to_insert)} product matches to validation table...")
    
    # Prepare data for insertion (only top 1 match per product A)
    data_to_insert = []
    
    for product_a_id, data in products_to_insert.items():
        # Skip products marked as not found
        if data.get('not_found', False):
            continue
            
        similar_products_b = data.get('similar_products_b', [])
        
        # Only insert if there are matches
        if not similar_products_b or len(similar_products_b) == 0:
            continue
        
        # Get top 1 match (already sorted by combined_score in descending order)
        top_match = similar_products_b[0]
        
        product_a = data.get('product_a', {})
        
        # Convert shared_nodes to JSON string for JSONB column
        shared_nodes = top_match.get('shared_nodes_details', [])
        shared_nodes_json = json.dumps(shared_nodes) if shared_nodes else '[]'
        
        # Extract store_a (try multiple sources with priority)
        store_a = data.get('store_a') or product_a.get('store_a') or product_a.get('store')
        
        # Determine validation status
        validated = True if initial_validation_status else False
        validation_result = initial_validation_status  # 'perfect', 'correct', 'incorrect', 'uncertain', or None
        
        if initial_validation_status:
            logger.info(f"Setting validation_result='{validation_result}' for product {product_a_id}")
        
        # Extract relevant fields
        record = (
            product_a_id,                                      # product_a_id
            product_a.get('siid'),                            # product_a_siid
            product_a.get('product_name'),                    # product_a_name
            product_a.get('description'),                     # product_a_description
            product_a.get('url'),                             # product_a_url
            store_a,                                          # store_a
            top_match.get('id'),                              # product_b_id
            top_match.get('siid'),                            # product_b_siid
            top_match.get('product_name'),                    # product_b_name
            top_match.get('description'),                     # product_b_description
            top_match.get('url'),                             # product_b_url
            top_match.get('store'),                           # store_b
            float(top_match.get('weighted_score', 0.0)),     # graph_score
            float(top_match.get('name_similarity', 0.0)),    # name_similarity
            float(top_match.get('description_similarity', 0.0)),  # description_similarity
            float(top_match.get('euclidean_similarity', 0.0)),    # euclidean_similarity
            float(top_match.get('combined_score', 0.0)),     # combined_score
            top_match.get('overlap', 0),                      # overlap_count
            shared_nodes_json,                                # shared_nodes (JSONB as JSON string)
            validated,                                        # validated (TRUE if status provided)
            validation_result                                 # validation_result ('perfect', 'correct', etc.)
        )
        
        data_to_insert.append(record)
    
    if len(data_to_insert) == 0:
        logger.warning("No valid matches to insert after filtering")
        return
    
    logger.info(f"✅ Prepared {len(data_to_insert)} records for insertion")
    
    # Database connection
    try:
        conn = psycopg2.connect(
            host=POSTGRES_DB_CONFIG['host'],
            port=POSTGRES_DB_CONFIG['port'],
            database=POSTGRES_DB_CONFIG['database'],
            user=POSTGRES_DB_CONFIG['user'],
            password=POSTGRES_DB_CONFIG['password']
        )
        cursor = conn.cursor()
        
        # Create table if it doesn't exist
        create_table_query = """
        CREATE TABLE IF NOT EXISTS product_match_validation (
            id SERIAL PRIMARY KEY,
            product_a_id VARCHAR(255) NOT NULL,
            product_a_siid VARCHAR(255),
            product_a_name TEXT,
            product_a_description TEXT,
            product_a_url TEXT,
            store_a VARCHAR(255),
            product_b_id VARCHAR(255) NOT NULL,
            product_b_siid VARCHAR(255),
            product_b_name TEXT,
            product_b_description TEXT,
            product_b_url TEXT,
            store_b VARCHAR(255),
            graph_score FLOAT,
            name_similarity FLOAT,
            description_similarity FLOAT,
            euclidean_similarity FLOAT,
            combined_score FLOAT,
            overlap_count INTEGER,
            shared_nodes JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            validated BOOLEAN DEFAULT FALSE,
            validation_result VARCHAR(50),
            notes TEXT,
            previous_validation_result VARCHAR(50),
            previous_validation_date TIMESTAMP,
            UNIQUE(product_a_siid, store_b)
        );
        
        CREATE INDEX IF NOT EXISTS idx_product_match_validation_product_a_siid 
            ON product_match_validation(product_a_siid);
        CREATE INDEX IF NOT EXISTS idx_product_match_validation_store_b 
            ON product_match_validation(store_b);
        CREATE INDEX IF NOT EXISTS idx_product_match_validation_combined_score 
            ON product_match_validation(combined_score DESC);
        CREATE INDEX IF NOT EXISTS idx_product_match_validation_validated 
            ON product_match_validation(validated);
        """
        
        cursor.execute(create_table_query)
        logger.info("✅ Table 'product_match_validation' verified/created")
        
        # Add new columns if they don't exist (for existing tables)
        alter_table_query = """
        DO $$ 
        BEGIN
            -- Add previous_validation_result column if it doesn't exist
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'product_match_validation' 
                AND column_name = 'previous_validation_result'
            ) THEN
                ALTER TABLE product_match_validation 
                ADD COLUMN previous_validation_result VARCHAR(50);
            END IF;
            
            -- Add previous_validation_date column if it doesn't exist
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'product_match_validation' 
                AND column_name = 'previous_validation_date'
            ) THEN
                ALTER TABLE product_match_validation 
                ADD COLUMN previous_validation_date TIMESTAMP;
            END IF;
        END $$;
        
        -- Drop all old unique constraints that might exist
        DO $$
        BEGIN
            -- Drop the auto-generated constraint name from table definition
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'product_match_validation_product_a_siid_store_b_key'
            ) THEN
                ALTER TABLE product_match_validation 
                DROP CONSTRAINT product_match_validation_product_a_siid_store_b_key;
            END IF;
            
            -- Drop named unique constraint if it exists
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'unique_product_a_siid_store_b'
            ) THEN
                ALTER TABLE product_match_validation 
                DROP CONSTRAINT unique_product_a_siid_store_b;
            END IF;
            
            -- Drop old unique constraint if exists
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'unique_product_match_key'
            ) THEN
                ALTER TABLE product_match_validation 
                DROP CONSTRAINT unique_product_match_key;
            END IF;
        END $$;
        
        -- Add unique constraint on (product_a_id, store_a, store_b, created_at) for ON CONFLICT
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'unique_product_match_key_full'
            ) THEN
                ALTER TABLE product_match_validation 
                ADD CONSTRAINT unique_product_match_key_full 
                UNIQUE (product_a_id, store_a, store_b, created_at);
            END IF;
        END $$;
        """
        
        cursor.execute(alter_table_query)
        logger.info("✅ Schema migration completed - new columns added if needed")
        
        # Filter out records where a 'perfect' match already exists
        # Check for existing perfect matches
        logger.info("🔍 Checking for existing perfect matches...")
        check_query = """
        SELECT DISTINCT product_a_id, store_b
        FROM product_match_validation
        WHERE validation_result = 'perfect'
        """
        cursor.execute(check_query)
        existing_perfect = {(row[0], row[1]) for row in cursor.fetchall()}
        logger.info(f"Found {len(existing_perfect)} existing perfect matches")
        
        # Filter data_to_insert
        original_count = len(data_to_insert)
        data_to_insert = [
            record for record in data_to_insert
            if (record[0], record[11]) not in existing_perfect  # product_a_id, store_b
        ]
        filtered_count = original_count - len(data_to_insert)
        if filtered_count > 0:
            logger.info(f"⚠️  Filtered out {filtered_count} records with existing perfect matches")
        
        if len(data_to_insert) == 0:
            logger.info("ℹ️  No new records to insert after filtering")
            cursor.close()
            conn.close()
            return
        
        # Insert only new records (no ON CONFLICT - let primary key handle duplicates)
        insert_query = """
        INSERT INTO product_match_validation (
            product_a_id, product_a_siid, product_a_name, product_a_description, product_a_url, store_a,
            product_b_id, product_b_siid, product_b_name, product_b_description, product_b_url, store_b,
            graph_score, name_similarity, description_similarity, 
            euclidean_similarity, combined_score, overlap_count, shared_nodes,
            validated, validation_result
        ) VALUES %s
        ON CONFLICT (product_a_id, store_a, store_b, created_at) 
        DO NOTHING
        """
        
        execute_values(cursor, insert_query, data_to_insert)
        
        # Commit the insertion
        conn.commit()
        
        logger.info(f"✅ Successfully inserted {len(data_to_insert)} records to product_match_validation table")
        
        # Auto-validate matches with combined_score >= 0.86 as 'perfect'
        logger.info("🔍 Auto-validating matches with combined_score >= 0.86 as 'perfect'...")
        auto_validate_query = """
        UPDATE product_match_validation
        SET 
            validated = TRUE,
            validation_result = 'perfect',
            notes = COALESCE(notes || ' | ', '') || 'Auto-validated: score >= 0.86'
        WHERE combined_score >= 0.86
        AND (validated = FALSE OR validated IS NULL)
        """
        
        cursor.execute(auto_validate_query)
        auto_validated_count = cursor.rowcount
        
        # Commit the auto-validation
        conn.commit()
        
        if auto_validated_count > 0:
            logger.info(f"✅ Auto-validated {auto_validated_count} matches as 'perfect' (score >= 0.86)")
        else:
            logger.info("ℹ️  No matches found with score >= 0.86 to auto-validate")
        
    except psycopg2.Error as e:
        logger.error(f"❌ Database error: {e}")
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        logger.error(f"❌ Error inserting to validation table: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            logger.info("🔌 Database connection closed")


if __name__ == "__main__":
    """
    Main execution: Load JSON file and insert to PostgreSQL.
    
    Usage:
        python insert_match_posgres.py <json_file_path>
        
    Example:
        python insert_match_posgres.py ../../../data/processed/matches/matches_unknown_to_unknown_20251215_100448.json
    """
    import sys
    
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    if len(sys.argv) < 2:
        # No argument provided, try to find latest JSON file
        matches_dir = project_root / "data" / "processed" / "matches"
        
        if not matches_dir.exists():
            logger.error(f"❌ Matches directory not found: {matches_dir}")
            logger.info("Usage: python insert_match_posgres.py <json_file_path>")
            sys.exit(1)
        
        # Get all JSON files
        json_files = sorted(matches_dir.glob("matches_*.json"), reverse=True)
        
        if not json_files:
            logger.error(f"❌ No JSON files found in: {matches_dir}")
            logger.info("Usage: python insert_match_posgres.py <json_file_path>")
            sys.exit(1)
        
        json_path = json_files[0]
        logger.info(f"🔍 No file specified, using latest: {json_path.name}")
    else:
        json_path = Path(sys.argv[1])
    
    # Check if file exists
    if not json_path.exists():
        logger.error(f"❌ File not found: {json_path}")
        sys.exit(1)
    
    logger.info("=" * 70)
    logger.info("  PROCESS MATCHES JSON TO POSTGRESQL")
    logger.info("=" * 70)
    logger.info(f"📖 Loading: {json_path}")
    
    try:
        # Load JSON file
        with open(json_path, 'r', encoding='utf-8') as f:
            results = json.load(f)
        
        # Count products (exclude metadata)
        product_count = len([k for k in results.keys() if k != '_metadata'])
        logger.info(f"✅ Loaded {product_count} products from JSON")
        
        # Display metadata if available
        if '_metadata' in results:
            metadata = results['_metadata']
            logger.info(f"📊 Metadata:")
            logger.info(f"   Store A: {metadata.get('store_a', 'unknown')}")
            logger.info(f"   Store B: {metadata.get('store_b', 'unknown')}")
            logger.info(f"   Top N: {metadata.get('top_n', 'unknown')}")
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("  INSERTING TO POSTGRESQL")
        logger.info("=" * 70)
        
        # Insert to database
        insert_to_validate(results)
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("✅ PROCESS COMPLETE!")
        logger.info("=" * 70)
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Error processing file: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)