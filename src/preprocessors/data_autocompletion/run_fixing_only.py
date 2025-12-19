#!/usr/bin/env python3
"""
Standalone script to run CSV fixing on enriched and translated CSV files.
This extracts only the CSV fixing step from the full pipeline.
"""

import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from config.settings import PROJECT_ROOT
from src.preprocessors.data_autocompletion.csv_fixing import CSVFixer


def run_csv_fixing(
    enriched_file: str,
    translated_file: str,
    output_file: str = None,
    store_name: str = None,
    postcode: str = None
):
    """
    Run CSV fixing on enriched and translated CSV files.
    
    Args:
        enriched_file: Path to the enriched CSV file
        translated_file: Path to the translated CSV file
        output_file: Optional path for output file. If not provided,
                     will create in data/processed/fixed/
        store_name: Name of the store (extracted from filename if not provided)
        postcode: Postcode (extracted from filename if not provided)
    """
    print(f"\n{'='*60}")
    print("CSV FIXING POST LLM COMPLETION")
    print(f"{'='*60}")
    
    # Validate input files exist
    enriched_path = Path(enriched_file)
    translated_path = Path(translated_file)
    
    if not enriched_path.exists():
        raise FileNotFoundError(f"Enriched file not found: {enriched_file}")
    
    if not translated_path.exists():
        raise FileNotFoundError(f"Translated file not found: {translated_file}")
    
    # Setup output path if not provided
    if output_file is None:
        fixed_dir = Path(PROJECT_ROOT) / 'data' / 'processed' / 'fixed'
        fixed_dir.mkdir(parents=True, exist_ok=True)
        fixed_ts = datetime.now().strftime("%Y%m%d_%H%M")
        fixed_filename = f"{enriched_path.stem}_fixed_{fixed_ts}.csv"
        output_path = fixed_dir / fixed_filename
    else:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Extract store_name and postcode from filename if not provided
    if store_name is None:
        try:
            # Extract from translated filename (format: store_postcode_01_translated_timestamp.csv)
            filename_parts = translated_path.stem.split('_')
            store_name = filename_parts[0]
            print(f"Extracted store name: {store_name}")
        except IndexError:
            print("Could not extract store name from filename")
            store_name = None
    
    if postcode is None:
        try:
            # Extract from translated filename
            filename_parts = translated_path.stem.split('_')
            postcode = filename_parts[1]
            print(f"Extracted postcode: {postcode}")
        except IndexError:
            print("Could not extract postcode from filename")
            postcode = None
    
    print(f"\nInput (enriched):  {enriched_path}")
    print(f"Input (translated): {translated_path}")
    print(f"Output:             {output_path}")
    print(f"Store name:         {store_name}")
    print(f"Postcode:           {postcode}")
    print(f"\nStarting fixing process...\n")
    
    try:
        # Initialize CSV fixer
        fixer = CSVFixer()
        
        # Process the CSV files
        fixer.fix_csv(
            enriched_file=str(enriched_path),
            translated_file=str(translated_path),
            output_path=str(output_path),
            store_name=store_name,
            postcode=postcode
        )
        
        print("\n✓ CSV fixing finished!")
        print(f"  - Fixed CSV saved to: {output_path}")
        print(f"{'='*60}\n")
        
        return str(output_path)
        
    except Exception as e:
        print(f"\n❌ CSV fixing failed: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    # Default file paths (absolute paths from PROJECT_ROOT)
    default_enriched = Path(PROJECT_ROOT) / "data/processed/enriched/makro_01013_01_enriched_20251215_1341.csv"
    default_translated = Path(PROJECT_ROOT) / "data/processed/translated/makro_01013_01_translated_20251215_1323.csv"
    
    # Check if input paths provided as arguments
    if len(sys.argv) > 2:
        enriched_csv = sys.argv[1]
        translated_csv = sys.argv[2]
    else:
        enriched_csv = str(default_enriched)
        translated_csv = str(default_translated)
    
    # Optional output path
    output_csv = sys.argv[3] if len(sys.argv) > 3 else None
    
    # Optional store name and postcode
    store = sys.argv[4] if len(sys.argv) > 4 else None
    postcode = sys.argv[5] if len(sys.argv) > 5 else None
    
    try:
        result_path = run_csv_fixing(
            enriched_file=enriched_csv,
            translated_file=translated_csv,
            output_file=output_csv,
            store_name=store,
            postcode=postcode
        )
        print(f"\n✅ SUCCESS: Fixed CSV created at {result_path}")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        sys.exit(1)
