#!/usr/bin/env python3
"""
Standalone script to run CSV completion on a translated CSV file.
This extracts only the CSV completion step from the full pipeline.
"""

import sys
from datetime import datetime
from pathlib import Path
import traceback

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from config.settings import AZURE_OPENAI_CONFIG, PROJECT_ROOT
from src.preprocessors.data_autocompletion.llm_completion import CSVCompleter


def run_csv_completion(input_csv_path: str, output_csv_path: str = None):
    """
    Run CSV completion on a translated CSV file.
    
    Args:
        input_csv_path: Path to the translated CSV file
        output_csv_path: Optional path for output file. If not provided, 
                        will create in data/processed/enriched/
    """
    print(f"\n{'='*60}")
    print("CSV COMPLETION WITH AZURE OPENAI")
    print(f"{'='*60}")
    
    # Validate input file exists
    input_path = Path(input_csv_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_csv_path}")
    
    # Setup output path if not provided
    if output_csv_path is None:
        enriched_dir = Path(PROJECT_ROOT) / 'data' / 'processed' / 'enriched'
        enriched_dir.mkdir(parents=True, exist_ok=True)
        enriched_ts = datetime.now().strftime("%Y%m%d_%H%M")
        enriched_filename = f"{input_path.stem}_enriched_{enriched_ts}.csv"
        output_path = enriched_dir / enriched_filename
    else:
        output_path = Path(output_csv_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"Input:      {input_path}")
    print(f"Output:     {output_path}")
    print(f"Deployment: {AZURE_OPENAI_CONFIG.get('deployment_name')}")
    print(f"\nStarting completion process...\n")
    
    try:
        # Initialize CSV Completer
        completer = CSVCompleter(
            api_key=AZURE_OPENAI_CONFIG.get("api_key"),
            deployment_name=AZURE_OPENAI_CONFIG.get("deployment_name"),
            api_base=AZURE_OPENAI_CONFIG.get("api_base")
        )
        
        # Process the translated CSV to complete empty fields
        completer.process_csv(
            input_filepath=str(input_path),
            output_filepath=str(output_path),
        )
        
        print("\n✓ CSV Completion finished!")
        print(f"  - Enriched CSV saved to: {output_path}")
        print(f"{'='*60}\n")
        
        return str(output_path)
        
    except Exception as e:
        print(f"\n❌ CSV Completion failed: {e}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    # Default file path
    default_input = Path(PROJECT_ROOT) / "data/processed/translated/eroski_01015_01_translated_20251216_1148.csv"
    
    # Check if input path provided as argument
    if len(sys.argv) > 1:
        input_csv = sys.argv[1]
    else:
        input_csv = str(default_input)
    
    # Optional output path
    output_csv = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        result_path = run_csv_completion(input_csv, output_csv)
        print(f"\n✅ SUCCESS: Output file created at {result_path}")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        sys.exit(1)
