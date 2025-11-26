import os
import urllib3
import sys
from pathlib import Path
from langdetect import DetectorFactory
import logging
from datetime import datetime
import traceback

# Load Azure OpenAI credentials
from dotenv import load_dotenv

# Add the project root to Python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import config.settings as settings
AWS_TRANSLATE_CONFIG = getattr(settings, 'AWS_TRANSLATE_CONFIG', None)


from src.preprocessors.data_formatting.path_handling import discover_files_by_type

# Import validation utilities
from src.preprocessors.data_formatting.column_validation import (
    process_dataframe_standards,
    load_data_from_organized_structure
)

# Import CSV Completion processor
from src.preprocessors.data_autocompletion.llm_processing import CSVCompleter

# Import CSV Fixer
from src.preprocessors.data_autocompletion.csv_fixing import CSVFixer

# from src.preprocessors.data_autocompletion.csv_completion_chunks_client import CSVCompleter

# Disable SSL warnings (only for testing/development)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Set seed for consistent language detection
DetectorFactory.seed = 0

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



try:
    from src.preprocessors.text_translation.intelligent_csv_translator_aws import IntelligentCSVTranslatorAWS
except Exception as e:
    # Provide a helpful error message suggesting the missing dependency
    msg = str(e)
    if 'boto3' in msg or 'No module named' in msg:
        raise ImportError(
            "Failed to import IntelligentCSVTranslatorAWS. It likely requires 'boto3'.\n"
            "Install dependencies with: pip install -r requirements.txt"
        )
    raise ImportError(f"Failed to import IntelligentCSVTranslatorAWS: {e}")


def main():
    """Main pipeline: Validation -> Translation -> CSV Completion with LLM"""
    load_dotenv()
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
    AZURE_OPENAI_API_BASE = os.getenv("AZURE_OPENAI_API_BASE")
    AZURE_OPENAI_API_TYPE = os.getenv("AZURE_OPENAI_API_TYPE")
    DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

    # Load AWS creds from config or environment and initialize AWS translator
    if AWS_TRANSLATE_CONFIG:
        aws_access_key_id = AWS_TRANSLATE_CONFIG.get("access_key_id")
        aws_secret_access_key = AWS_TRANSLATE_CONFIG.get("secret_access_key")
        aws_region = AWS_TRANSLATE_CONFIG.get("region")
    else:
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_DEFAULT_REGION")

    translator = IntelligentCSVTranslatorAWS(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=aws_region,
        predetermined_languages=['es', 'en'],
        use_llm=True,
        llm_api_key=AZURE_OPENAI_API_KEY,
        llm_base_url=AZURE_OPENAI_API_BASE,
        llm_model=DEPLOYMENT_NAME
    )

    try:
        # ============================================================
        # STEP 1: VALIDATION AND STANDARDIZATION
        # ============================================================

        base_path = Path(project_root) / 'data' / 'raw'
        files = discover_files_by_type(file_extension='xlsx', base_path=base_path)
        print(f"Discovered {len(files)} .xlsx files for processing: {files}")

        for input_file in files:
            ts = 2

            print("="*60)
            print("STEP 1: VALIDATION AND STANDARDIZATION")
            print("="*60)
            print(f"Validating and standardizing CSV: {input_file}...")

            # 1. Load original CSV into a DataFrame with robust parsing
            try:
                print("Attempting standard file parsing...")
                df = load_data_from_organized_structure(filename=input_file, base_path=base_path)
            except Exception as e:
                logger.error(f"Auto-detect delimiter failed: {e}")

            # 2. Run standardization (adds missing required columns, processes category paths, cleans nulls)
            df_validated = process_dataframe_standards(df, null_replacement='', filename=input_file)

            # 3. Save validated CSV to data/processed/validated with timestamped filename
            
            validated_dir = Path(project_root) / 'data' / 'processed' / 'validated'
            validated_dir.mkdir(parents=True, exist_ok=True)
            validated_filename = f"{Path(input_file).stem}_validated_{ts}.csv"
            validated_path = validated_dir / validated_filename
            df_validated.to_csv(validated_path, sep=";", index=False, encoding='utf-8')
            print(f"✓ Validation completed. Validated CSV saved to: {validated_path}")

            # ============================================================
            # STEP 2: TRANSLATION
            # ============================================================
            print(f"\n{'='*60}")
            print("STEP 2: TRANSLATION")
            print(f"{'='*60}")
            
            translated_dir = Path(project_root) / 'data' / 'processed' / 'translated'
            translated_dir.mkdir(parents=True, exist_ok=True)
            translated_filename = f"{Path(input_file).stem}_translated_{ts}.csv"
            translated_path = translated_dir / translated_filename
            
            # Check if translated file already exists
            if translated_path.exists():
                print(f"⏭️  Translation already exists, skipping translation step...")
                print(f"  - Using existing file: {translated_path}")
            else:
                print("Starting translation...")
                
                # Translate using AWS translator
                results = translator.translate_csv(
                    str(validated_path),
                    str(translated_path),
                    columns_with_language={},
                    predetermined_languages=None
                )

                print("✓ Translation completed!")
                print(f"  - Total translations made: {results['total_translations']}")
                print(f"  - Translated CSV saved to: {translated_path}")

            # ============================================================
            # STEP 3: CSV COMPLETION WITH AZURE OPENAI
            # ============================================================
            print(f"\n{'='*60}")
            print("STEP 4: CSV FIXING POST LLM COMPLETION")
            print(f"{'='*60}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Setup enriched output path
            enriched_dir = Path(project_root) / 'data' / 'processed' / 'enriched'
            enriched_dir.mkdir(parents=True, exist_ok=True)
            enriched_filename = f"{Path(input_file).stem}_enriched_2.csv"
            enriched_path = enriched_dir / enriched_filename
            
            print(f"Input: {translated_path}")
            print(f"Output: {enriched_path}")
            print(f"Deployment: {DEPLOYMENT_NAME}")
            
            # Initialize CSV Completer
            try:
                completer = CSVCompleter(
                    api_key=AZURE_OPENAI_API_KEY,
                    deployment_name=DEPLOYMENT_NAME,
                    api_base=AZURE_OPENAI_API_BASE
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
            # STEP 4: CSV fixing post LLM completion
            # ============================================================
            print(f"\n{'='*60}")
            print("STEP 4: CSV FIXING POST LLM COMPLETION")
            print(f"{'='*60}")

            fixed_dir = Path(project_root) / 'data' / 'processed' / 'fixed'
            fixed_dir.mkdir(parents=True, exist_ok=True)
            fixed_filename = f"{enriched_filename.split('.')[0]}_fixed.csv"
            fixed_path = fixed_dir / fixed_filename
            
            print(f"Input: {enriched_path}")
            print(f"Output: {fixed_path}")
            print(f"Deployment: {DEPLOYMENT_NAME}")

            # Extract postcode from filename if available
            try:
                postcode = Path(input_file).stem.split('_')[1]
                print(f"Extracted postcode: {postcode}")
            except IndexError:
                postcode = None


            # Initialize CSV fixer
            try:
                fixer = CSVFixer(
                )
                
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



            # ============================================================
            # PIPELINE COMPLETED
            # ============================================================
            print(f"\n{'='*60}")
            print("PIPELINE COMPLETED SUCCESSFULLY!")
            print(f"{'='*60}")
            print("Final outputs:")
            print(f"  1. Validated: {validated_path}")
            print(f"  2. Translated: {translated_path}")
            print(f"  3. Enriched: {enriched_path}")
            print(f"  4. Fixed: {fixed_path}")
            print(f"{'='*60}\n")

    except Exception as e:
        print(f"\n❌ Pipeline Error: {e}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()