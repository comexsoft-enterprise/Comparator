import pandas as pd
import os
from pathlib import Path
from datetime import datetime

import sys

# Add the project root to Python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

def get_latest_csv_files():
    """Get the latest CSV files from each processed folder"""
    base_path = Path(r"C:/Users/PT578HJ/ai_consumer_goods/data/processed")
    
    folders = ['enriched', 'translated', 'validated']
    csv_files = {}
    
    for folder in folders:
        folder_path = base_path / folder
        csv_files_in_folder = list(folder_path.glob("*.csv"))
        
        if csv_files_in_folder:
            # Get the most recent CSV file based on filename timestamp
            latest_file = max(csv_files_in_folder, key=lambda x: x.stat().st_mtime)
            csv_files[folder] = latest_file
        else:
            print(f"No CSV files found in {folder} folder")
            csv_files[folder] = None
    
    return csv_files

def load_csv_data(file_paths):
    """Load CSV data from file paths"""
    data = {}
    for folder, file_path in file_paths.items():
        if file_path and file_path.exists():
            try:
                df = pd.read_csv(file_path, sep=";", encoding="utf-8")
                data[folder] = df
                print(f"Loaded {folder}: {len(df)} rows, {len(df.columns)} columns")
            except Exception as e:
                print(f"Error loading {folder} file: {e}")
                data[folder] = None
        else:
            data[folder] = None
    return data

def compare_basic_info(data):
    """Compare basic information about the datasets"""
    print("\n" + "="*50)
    print("BASIC COMPARISON")
    print("="*50)
    
    for folder, df in data.items():
        if df is not None:
            print(f"\n{folder.upper()}:")
            print(f"  Shape: {df.shape}")
            print(f"  Columns: {list(df.columns)}")
            print(f"  Memory usage: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

def compare_columns(data):
    """Compare columns across datasets"""
    print("\n" + "="*50)
    print("COLUMN COMPARISON")
    print("="*50)
    
    all_columns = set()
    for folder, df in data.items():
        if df is not None:
            all_columns.update(df.columns)
    
    print(f"\nAll unique columns: {sorted(all_columns)}")
    
    for folder, df in data.items():
        if df is not None:
            print(f"\n{folder.upper()} columns: {list(df.columns)}")
            
            # Find missing columns
            missing_cols = all_columns - set(df.columns)
            if missing_cols:
                print(f"  Missing columns: {sorted(missing_cols)}")

def compare_data_types(data):
    """Compare data types across datasets"""
    print("\n" + "="*50)
    print("DATA TYPE COMPARISON")
    print("="*50)
    
    for folder, df in data.items():
        if df is not None:
            print(f"\n{folder.upper()} data types:")
            print(df.dtypes.to_string())

def compare_null_values(data):
    """Compare null values across datasets"""
    print("\n" + "="*50)
    print("NULL VALUES COMPARISON")
    print("="*50)
    
    for folder, df in data.items():
        if df is not None:
            null_counts = df.isnull().sum()
            null_percentages = (df.isnull().sum() / len(df)) * 100
            
            print(f"\n{folder.upper()} null values:")
            for col in df.columns:
                if null_counts[col] > 0:
                    print(f"  {col}: {null_counts[col]} ({null_percentages[col]:.2f}%)")

def compare_sample_data(data, n_samples=5):
    """Compare sample data from each dataset"""
    print("\n" + "="*50)
    print(f"SAMPLE DATA COMPARISON (First {n_samples} rows)")
    print("="*50)
    
    for folder, df in data.items():
        if df is not None:
            print(f"\n{folder.upper()} sample:")
            print(df.head(n_samples).to_string())

def find_common_columns(data):
    """Find columns that exist in all datasets"""
    datasets_with_data = {k: v for k, v in data.items() if v is not None}
    
    if len(datasets_with_data) < 2:
        return set()
    
    common_columns = set(list(datasets_with_data.values())[0].columns)
    for df in datasets_with_data.values():
        common_columns = common_columns.intersection(set(df.columns))
    
    return common_columns

def compare_common_data(data):
    """Compare data in common columns"""
    common_cols = find_common_columns(data)
    
    if not common_cols:
        print("\nNo common columns found across all datasets")
        return
    
    print("\n" + "="*50)
    print("COMMON COLUMNS DATA COMPARISON")
    print("="*50)
    print(f"Common columns: {sorted(common_cols)}")
    
    datasets_with_data = {k: v for k, v in data.items() if v is not None}
    
    for col in sorted(common_cols):
        print(f"\nColumn: {col}")
        for folder, df in datasets_with_data.items():
            unique_values = df[col].nunique()
            print(f"  {folder}: {unique_values} unique values")

def generate_comparison_report(file_paths, data):
    """Generate a comprehensive comparison report"""
    print("CSV FILES COMPARISON REPORT")
    print("="*50)
    print(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\nFiles being compared:")
    for folder, file_path in file_paths.items():
        if file_path:
            print(f"  {folder}: {file_path.name}")
        else:
            print(f"  {folder}: No file found")
    
    compare_basic_info(data)
    compare_columns(data)
    compare_data_types(data)
    compare_null_values(data)
    compare_common_data(data)
    compare_sample_data(data)

def main():
    """Main function to run the comparison"""
    print("Starting CSV comparison...")
    
    # Get the latest CSV files from each folder
    file_paths = get_latest_csv_files()
    
    # Load the data
    data = load_csv_data(file_paths)
    
    # Generate comparison report
    generate_comparison_report(file_paths, data)
    
    print("\nComparison completed!")

if __name__ == "__main__":
    main()