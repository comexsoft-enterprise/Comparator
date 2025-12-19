import csv
from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from typing import List, Optional, Dict, Any
from fastapi import UploadFile
import pandas as pd
from pydantic import BaseModel

from src.numeric_variables_postgres.postgresql_data_extraction import VectorDataExtractor, ingest_verified_csv
from src.preprocessors.data_autocompletion.llm_completion import CSVCompleter
from src.preprocessors.data_autocompletion.csv_fixing import CSVFixer
from src.preprocessors.data_formatting.product_verification import verify_products
from src.preprocessors.text_translation.translator import TranslatorOpenAI
from config.settings import NEO4J_CONFIG, AZURE_OPENAI_CONFIG, LOGGING_CONFIG
import config.settings as settings
AWS_TRANSLATE_CONFIG = getattr(settings, 'AWS_TRANSLATE_CONFIG', None)
from neo4j import GraphDatabase

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from config.settings import PROJECT_ROOT
from src.preprocessors.data_formatting.column_validation import (
    load_data_from_organized_structure,
    process_dataframe_standards,
)
from src.preprocessors.data_formatting.path_handling import (
    discover_files_by_type,
    identify_file_type,
)

from src.ingestion.neo4j_ingest import Neo4jCSVIngestor
from src.ingestion.neo4j_embeddings import (
    get_embedder,
    generate_embeddings_multithreaded,
    create_vector_index,
)

class PreprocessingResponse(BaseModel):
    """Response model for preprocessing pipeline"""
    status: str
    filename: str
    original_path: str
    processed_path: Optional[str] = None
    rows_processed: Optional[int] = None
    columns_processed: Optional[int] = None
    message: str
    errors: Optional[List[str]] = None
    ingestion_results: Optional[Dict[str, Any]] = None


def process_uploaded_file(
    file: UploadFile,
    base_path: str = "data/raw"
) -> Dict[str, Any]:
    """
    Process an uploaded supermarket file through the preprocessing pipeline.
    
    Args:
        file: Uploaded file
        base_path: Base directory for raw data files
        
    Returns:
        Dictionary with processing results
        
    Raises:
        ValueError: If file type is not supported
    """

    translator_llm = TranslatorOpenAI(
        predetermined_languages=['es', 'en'],
        llm_api_key=AZURE_OPENAI_CONFIG.get("api_key"),
        llm_base_url=AZURE_OPENAI_CONFIG.get("api_base"),
        llm_model=AZURE_OPENAI_CONFIG.get("deployment_name")
    )
    
    try:

        # ============================================================
        # STEP 1: VALIDATION AND STANDARDIZATION
        # ============================================================

        # Normalize base_path to a Path under project_root and ensure the directory exists
        base_dir = Path(base_path)
        if not base_dir.is_absolute():
            base_dir = Path(PROJECT_ROOT) / base_dir
        base_dir.mkdir(parents=True, exist_ok=True)

        # Save uploaded file to base_dir
        input_file = file.filename
        input_file_path = base_dir / input_file
        with open(input_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        # Identify file type using the saved file and discover files in the base directory
        file_type = identify_file_type(str(input_file_path))

        # Ensure the file is placed under base_dir/<file_type> so downstream discovery/parsers find it
        target_subdir = base_dir / file_type
        target_subdir.mkdir(parents=True, exist_ok=True)
        target_path = target_subdir / input_file
        if input_file_path.exists() and input_file_path.resolve() != target_path.resolve():
            shutil.move(str(input_file_path), str(target_path))
            input_file_path = target_path
            logging.info(f"Moved uploaded file to type subfolder: {input_file_path}")

        files = discover_files_by_type(file_extension=file_type, base_path=str(base_dir))
        print(f"Discovered {len(files)} .{file_type} files for processing: {files}")

        logging.info(f"Saved uploaded file to: {input_file_path}")

        try:
            print("Attempting standard file parsing...")
            # pass the directory where the uploaded file was saved
            df = load_data_from_organized_structure(filename=input_file, base_path=str(base_dir))
        except Exception as e:
            raise RuntimeError(f"File parsing failed: {e}")

        # 2. Run standardization (adds missing required columns, processes category paths, cleans nulls)
        df_validated = process_dataframe_standards(df, null_replacement='', filename=input_file)

        # 3. Save validated CSV to data/processed/validated with timestamped filename
        
        validated_dir = Path(PROJECT_ROOT) / 'data' / 'processed' / 'validated'
        validated_dir.mkdir(parents=True, exist_ok=True)
        validated_ts = datetime.now().strftime("%Y%m%d_%H%M")
        validated_filename = f"{Path(input_file).stem}_validated_{validated_ts}.csv"
        validated_path = validated_dir / validated_filename
        df_validated.to_csv(validated_path, sep=";", index=False, encoding='utf-8')
        print(f"✓ Validation completed. Validated CSV saved to: {validated_path}")


        # ============================================================
        # STEP 2: PRODUCT VERIFICATION
        # ============================================================

        verified_dir = Path(PROJECT_ROOT) / 'data' / 'processed' / 'verified'
        verified_dir.mkdir(parents=True, exist_ok=True)
        verified_ts = datetime.now().strftime("%Y%m%d_%H%M")
        verified_filename = f"{Path(input_file).stem}_verified_{verified_ts}.csv"
        verified_path = verified_dir / verified_filename

        # Run verification (compares with MongoDB, updates MongoDB, deletes from Neo4j)
        try:
            success = verify_products(
                str(validated_path),
                str(verified_path)
            )
            
            if not success:
                raise RuntimeError("Product verification failed")
            
            print("\n✓ Product verification completed!")
            print(f"  - Verified CSV saved to: {verified_path}")
            print(f"  - MongoDB: New products inserted, modified products updated")
            print(f"  - Neo4j: Modified products deleted (ready for re-ingestion)")
            # Insert new/modified products into PostgreSQL using centralized helper
            try:
                inserted = ingest_verified_csv(str(verified_path))
                print(f"  - PostgreSQL: Inserted/updated records: {inserted}")
            except Exception as e:
                print(f"  - PostgreSQL ingestion failed: {e}")
                traceback.print_exc()
            
        except Exception as e:
            print(f"\n❌ Product verification step failed: {e}")
            print(f"  Continuing with validated CSV: {validated_path}")
            # If verification fails, use validated file as verified file
            shutil.copy2(validated_path, verified_path)
            traceback.print_exc()

        # If the verified CSV contains no products (only header or empty), stop the pipeline
        try:
            if verified_path.exists():
                with open(verified_path, newline='', encoding='utf-8') as vf:
                    reader = csv.reader(vf, delimiter=';')
                    rows = list(reader)
                    # If file has <=1 rows, it means no data rows (only header) or empty
                    if len(rows) <= 1:
                        print(f"\nℹ️  No NEW or MODIFIED products found in verification step for {input_file}.")
                        print("    Skipping remaining pipeline steps for this file.")
                        return None
            else:
                # If verified file doesn't exist, treat as no products to ingest
                print(f"\n⚠️  Verified file not found at {verified_path}, skipping pipeline for this file.")
                return None
        except Exception as e:
            print(f"\n⚠️  Could not read verified file ({verified_path}): {e}")
            traceback.print_exc()

        
        # ============================================================
        # STEP 3: TRANSLATION
        # ============================================================
        print(f"\n{'='*60}")
        print("STEP 3: TRANSLATION")
        print(f"{'='*60}")

        translated_dir = Path(PROJECT_ROOT) / 'data' / 'processed' / 'translated'
        translated_dir.mkdir(parents=True, exist_ok=True)
        translated_ts = datetime.now().strftime("%Y%m%d_%H%M")
        translated_filename = f"{Path(input_file).stem}_translated_{translated_ts}.csv"
        translated_path = translated_dir / translated_filename

        # Check if translated file already exists
        if translated_path.exists():
            print(f"⏭️  Translation already exists, skipping translation step...")
            print(f"  - Using existing file: {translated_path}")
        else:
            print("Starting translation...")
            print(f"  - Passing verified file to translator: {verified_path}")
            
            # Translate using AWS translator (use verified file as input)
            results = translator_llm.translate_csv(
                str(verified_path),
                str(translated_path),
                columns_with_language={},
                predetermined_languages=None
            )

            print("✓ Translation completed!")
            print(f"  - Total translations made: {results.get('total_translations', 0)}")
            print(f"  - Expected translated CSV path: {translated_path}")

        # If translator returned without creating the translated file (no columns to translate
        # or an internal early exit), ensure we still have a translated file for downstream steps.
        if not translated_path.exists():
            print(f"⚠️  Translated file not found at {translated_path}. Falling back to verified CSV.")
            try:
                shutil.copy2(verified_path, translated_path)
                print(f"✓ Copied verified CSV to translated path: {translated_path}")
            except Exception as e:
                print(f"❌ Failed to create fallback translated file: {e}")
                raise


        # ============================================================
        # STEP 4: CSV COMPLETION WITH AZURE OPENAI
        # ============================================================
        print(f"\n{'='*60}")
        print("STEP 4: CSV COMPLETION WITH AZURE OPENAI")
        print(f"{'='*60}")

        enriched_ts = datetime.now().strftime("%Y%m%d_%H%M")
        # Setup enriched output path
        enriched_dir = Path(PROJECT_ROOT) / 'data' / 'processed' / 'enriched'
        enriched_dir.mkdir(parents=True, exist_ok=True)
        enriched_filename = f"{Path(input_file).stem}_enriched_{enriched_ts}.csv"
        enriched_path = enriched_dir / enriched_filename
        
        print(f"Input: {translated_path}")
        print(f"Output: {enriched_path}")
        print(f"Deployment: {AZURE_OPENAI_CONFIG.get('deployment_name')}")
        
        # Initialize CSV Completer
        try:
            completer = CSVCompleter(
                api_key=AZURE_OPENAI_CONFIG.get("api_key"),
                deployment_name=AZURE_OPENAI_CONFIG.get("deployment_name"),
                api_base=AZURE_OPENAI_CONFIG.get("api_base")
            )
            
            # Process the translated CSV to complete empty fields
            completer.process_csv(
                input_filepath=str(translated_path),
                output_filepath=str(enriched_path),
            )
            
            print("✓ CSV Completion finished!")
            print(f"  - Enriched CSV saved to: {enriched_path}")
            
        except Exception as e:
            print(f"❌ CSV Completion step failed: {e}")
            print(f"  Translated CSV is still available at: {translated_path}")
            raise

        # ============================================================
        # STEP 5: CSV FIXING POST LLM COMPLETION
        # ============================================================
        print(f"\n{'='*60}")
        print("STEP 5: CSV FIXING POST LLM COMPLETION")
        print(f"{'='*60}")

        fixed_dir = Path(PROJECT_ROOT) / 'data' / 'processed' / 'fixed'
        fixed_dir.mkdir(parents=True, exist_ok=True)
        fixed_ts = datetime.now().strftime("%Y%m%d_%H%M")
        fixed_filename = f"{enriched_filename.split('.')[0]}_fixed_{fixed_ts}.csv"
        fixed_path = fixed_dir / fixed_filename
        
        print(f"Input: {enriched_path}")
        print(f"Output: {fixed_path}")
        print(f"Deployment: {AZURE_OPENAI_CONFIG.get('deployment_name')}")

        # Extract postcode from filename if available
        try:
            postcode = Path(input_file).stem.split('_')[1]
            print(f"Extracted postcode: {postcode}")
        except IndexError:
            postcode = None


        # Initialize CSV fixer
        try:
            fixer = CSVFixer()
            
            # Process the translated CSV to complete empty fields
            fixer.fix_csv(
                enriched_file=str(enriched_path),
                translated_file=str(translated_path),
                output_path=str(fixed_path),
                store_name=Path(input_file).stem.split('_')[0],
                postcode = postcode
            )
            
            print("✓ CSV fixing finished!")
            print(f"  - Fixed CSV saved to: {fixed_path}")
            
        except Exception as e:
            print(f"❌ CSV Fixing step failed: {e}")
            raise
        # Column name lowercasing moved to CSVFixer.fix_csv implementation

        # ============================================================
        # STEP 6: DATA EXTRACTION (postgresql_data_extraction)
        # ============================================================
        print(f"\n{'='*60}")
        print("STEP 6: POSTGRESQL DATA EXTRACTION")
        print(f"{'='*60}")
        print(f"Processing translated file: {fixed_path}")
        
        try:
            # Initialize VectorDataExtractor with PostgreSQL mode
            extractor = VectorDataExtractor(use_csv=False)
            
            # Process the translated CSV file
            records_processed = extractor.process_input(
                input_path=str(fixed_path), 
                drop_table=False
            )
            
            print("✓ Data extraction completed!")
            print(f"  - Records processed: {records_processed}")
            print(f"  - Data saved to PostgreSQL database: product_vector_data table")
            
        except Exception as e:
            print(f"❌ Data extraction step failed: {e}")
            print(f"  Fixed CSV is still available at: {fixed_path}")
            # Continue with pipeline even if extraction fails
            traceback.print_exc()

        # ============================================================
        # PIPELINE COMPLETED
        # ============================================================
        print(f"\n{'='*60}")
        print("PIPELINE COMPLETED SUCCESSFULLY!")
        print(f"{'='*60}")
        print("Final outputs:")
        print(f"  1. Validated:  {validated_path}")
        print(f"  2. Verified:   {verified_path}")
        print(f"  3. Translated: {translated_path}")
        print(f"  4. Enriched:   {enriched_path}")
        print(f"  5. Fixed:      {fixed_path}")
        print(f"{'='*60}")

        print(f"{'='*60}\n")



    except Exception as e:
        print(f"\n❌ Pipeline Error: {e}")
        traceback.print_exc()
        raise


    logging.info("🚀 Starting Neo4j CSV Ingestion Process...")
    print("=" * 50)
    
    # Create ingestor instance
    ingestor = Neo4jCSVIngestor()
    
    # Only process the specific file that was just created
    csv_file = str(fixed_path)
    
    if not Path(csv_file).exists():
        logging.warning(f"❌ Fixed CSV file not found: {csv_file}")
        return
    
    logging.info(f"📄 Processing newly created file: {Path(csv_file).name}")
    
    logging.info("🔗 Attempting to connect to Neo4j database...")
    
    try:
        # Connect once to use check_value_exists
        if not ingestor.connect_to_neo4j():
            logging.error("❌ Could not connect to Neo4j. Aborting checks.")
            return

        # Check existence by product_hash for the specific file
        try:
            df = pd.read_csv(csv_file, sep=";", dtype=str, low_memory=False)
            
            if 'product_hash' in df.columns:
                total = len(df)
                existing = 0

                for val in df['product_hash'].dropna().astype(str):
                    if ingestor.check_value_exists('Product', 'product_hash', val):
                        existing += 1

                logging.info(f"{Path(csv_file).name}: {existing}/{total} products already in DB")

                if existing == total:
                    logging.info(f"Skipping ingestion for {Path(csv_file).name} because all products already exist")
                    return
            else:
                logging.warning(f"No 'product_hash' column in {Path(csv_file).name}; proceeding with ingestion.")
                
        except Exception as e:
            logging.error(f"Error reading {csv_file}: {e}")
            return

        # Proceed with the ingestion process for this specific file
        success = ingestor.ingest_csv_file(csv_file, batch_size=500)

        generate_embeddings(embedder="openai")
    
            
    except KeyboardInterrupt:
        logging.warning("⚠️  Ingestion interrupted by user")
    except Exception as e:
        logging.error(f"❌ Unexpected error: {e}")
        logging.error("   Check your Neo4j connection settings and ensure the database is running.")


def generate_embeddings(embedder: str = None):
    start = time.time()
    suffix = "hf" if embedder == "hf" else "openai"
    logging.info("Starting embeddings generation using: %s", embedder)

    embedder = get_embedder()
    
    # Create Neo4j driver
    driver = GraphDatabase.driver(
        NEO4J_CONFIG["uri"],
        auth=(
            NEO4J_CONFIG.get("user") or NEO4J_CONFIG.get("username"),
            NEO4J_CONFIG["password"]
        )
    )
    
    try:
        # Configuration
        batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "50"))
        max_workers = int(os.getenv("EMBEDDING_MAX_WORKERS", "10"))
        
        logging.info(f"Configuration: batch_size={batch_size}, max_workers={max_workers}")
        
        # Process product_name embeddings
        logging.info("=" * 70)
        logging.info("Generating product_name embeddings")
        logging.info("=" * 70)
        
        generate_embeddings_multithreaded(
            driver=driver,
            embedder=embedder,
            node_label="Product",
            text_property="product_name",
            embedding_property=f"product_name_embedding_{suffix}",
            batch_size=batch_size,
            max_workers=max_workers
        )
        
        create_vector_index(
            driver=driver,
            index_name=f"product_name_embedding_{suffix}",
            node_label="Product",
            embedding_property=f"product_name_embedding_{suffix}"
        )
        
        # Process description embeddings
        logging.info("=" * 70)
        logging.info("Generating description embeddings")
        logging.info("=" * 70)
        
        generate_embeddings_multithreaded(
            driver=driver,
            embedder=embedder,
            node_label="Product",
            text_property="description",
            embedding_property=f"description_embedding_{suffix}",
            batch_size=batch_size,
            max_workers=max_workers
        )
        
        create_vector_index(
            driver=driver,
            index_name=f"description_embedding_{suffix}",
            node_label="Product",
            embedding_property=f"description_embedding_{suffix}"
        )
        
        total = time.time() - start
        logging.info("=" * 70)
        logging.info(f"✅ Embeddings generation finished in {total:.2f} seconds ({total/60:.2f} minutes)")
        logging.info("=" * 70)
        
    finally:
        driver.close()