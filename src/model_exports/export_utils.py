"""
Export utilities for cross-store similarity analysis results.

This module provides functions to export similarity analysis results to various formats:
- CSV (simple and detailed)
- Excel with formatting
- SIID pairs for top-ranked matches

Author: Your Name
Date: November 27, 2025
"""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Union

import numpy as np
import pandas as pd


def _get_results_dir() -> Path:
    """Return the canonical results directory and ensure it exists."""
    try:
        from config.settings import PROJECT_ROOT  # type: ignore

        base_dir = Path(PROJECT_ROOT)
    except Exception:
        base_dir = Path.cwd()

    results_dir = base_dir / "data" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir


def _ensure_parent_dir(path: Union[str, Path]) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


def export_siid_pairs_csv(
    results: Dict[str, Dict],
    store_a: str,
    store_b: str,
    output_path: Optional[str] = None,
    metadata: Optional[Dict] = None
) -> Optional[str]:
    """
    Export only SIID pairs for products with rank 1, 2, or 3 to CSV.
    
    This is a lightweight export format containing only the essential
    product identifiers and their ranking, useful for downstream processing
    or validation.
    
    Args:
        results: Results from find_cross_store_similarities()
        store_a: Name of origin store
        store_b: Name of destination store
        output_path: Path to output CSV file (optional, auto-generated if None)
        metadata: Optional metadata dict with configuration parameters
        
    Returns:
        str: Path to CSV file created, or None if error occurred
    """
    # Generate filename if not provided
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = str(_get_results_dir() / f"siid_pairs_{store_a}_to_{store_b}_{timestamp}.csv")
    else:
        output_path = _ensure_parent_dir(output_path)
    
    # Collect SIID pairs for ranks 1-3
    siid_pairs = []
    
    for product_a_id, data in results.items():
        # Skip metadata
        if product_a_id == '_metadata':
            continue
        
        product_a = data.get('product_a')
        similar_b_list = data.get('similar_products_b', [])
        
        # Skip if no product_a or no matches or product not found
        if not product_a or not similar_b_list or data.get('not_found', False):
            continue
        
        # Get siid for product A
        siid_a = product_a.get('siid', '')
        
        # Add only rank 1, 2, and 3
        for rank, prod_b in enumerate(similar_b_list[:5], 1):  # Only first 3 products
            siid_b = prod_b.get('siid', '')
            
            if siid_a and siid_b:
                # Extract content after last "-" in siid for cleaner output
                siid_a_cleaned = siid_a.split('-')[-1] if '-' in siid_a else siid_a
                siid_b_cleaned = siid_b.split('-')[-1] if '-' in siid_b else siid_b
                
                siid_pairs.append({
                    'siid_a': siid_a_cleaned,
                    'siid_b': siid_b_cleaned,
                    'rank': rank
                })
    
    # Write to CSV
    # DISABLED: CSV generation commented out to avoid creating files in repo
    # try:
    #     with open(output_path, 'w', newline='', encoding='utf-8') as f:
    #         writer = csv.writer(f, delimiter=';')
    #         
    #         # Write header
    #         writer.writerow(['siid_a', 'siid_b', 'rank'])
    #         
    #         # Write data
    #         for pair in siid_pairs:
    #             writer.writerow([pair['siid_a'], pair['siid_b'], pair['rank']])
    #     
    #     logging.info(f"✅ SIID pairs CSV created: {output_path}")
    #     logging.info(f"   Total pairs exported: {len(siid_pairs)}")
    #     
    #     if metadata:
    #         logging.info(f"   Score threshold: {metadata.get('score_threshold', 'N/A')}")
    #     
    #     return output_path
    #     
    # except Exception as e:
    #     logging.error(f"❌ Error creating SIID pairs CSV: {e}")
    #     return None
    
    logging.info(f"⚠️ SIID pairs CSV export disabled - {len(siid_pairs)} pairs would have been exported")
    return None


def export_cross_store_results_to_csv(
    results: Dict[str, Dict],
    store_a: str,
    store_b: str,
    output_path: str = None
) -> str:
    """
    Export cross-store analysis results to CSV file with only siid_a, siid_b, and rank.
    
    Args:
        results: Results from find_cross_store_similarities()
        store_a: Name of origin store
        store_b: Name of destination store
        output_path: Path to CSV file (optional)
        
    Returns:
        str: Path to created CSV file
    """
    
    # Generate filename if not provided
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = str(_get_results_dir() / f"cross_store_similarity_{store_a}_to_{store_b}_{timestamp}.csv")
    else:
        output_path = _ensure_parent_dir(output_path)
    
    # Prepare data for CSV
    rows = []
    
    for product_a_id, data in results.items():
        # Skip metadata
        if product_a_id == '_metadata':
            continue
        
        product_a = data.get('product_a', {})
        similar_b_list = data.get('similar_products_b', [])
        is_not_found = data.get('not_found', False)
        
        if not similar_b_list:
            # Add row even if no matches found or product not found
            status = 'NOT FOUND IN DATABASE' if is_not_found else 'No matches'
            rows.append({
                'Product_A_ID': product_a_id,
                'Product_A_SIID': product_a.get('siid', ''),
                'Product_A_UUID': product_a.get('uuid', ''),
                'Product_A_Name': product_a.get('product_name', 'N/A'),
                'Product_A_Description': product_a.get('description', ''),
                'Product_A_URL': product_a.get('url', ''),
                'Store_A': store_a,
                'Rank': status,
                'Product_B_ID': '',
                'Product_B_SIID': '',
                'Product_B_UUID': '',
                'Product_B_Name': '',
                'Product_B_Description': '',
                'Product_B_URL': '',
                'Store_B': store_b,
                'Combined_Score': '',
                'Graph_Score': '',
                'Name_Similarity': '',
                'Description_Similarity': '',
                'Embedding_Score': '',
                'Euclidean_Similarity': '',
                'Overlap': '',
                'Brand_B': '',
                'Price_B': '',
                'Category_B': '',
                'Product_Type_B': '',
                'Format_B': '',
                'Shared_Attributes': ''
            })
        else:
            # Add one row per similar product
            for rank, prod_b in enumerate(similar_b_list, 1):
                # Get shared attributes
                shared_attrs = prod_b.get('shared_nodes_details', [])
                # Handle both old format (list of strings) and new format (list of dicts)
                if shared_attrs and isinstance(shared_attrs[0], dict):
                    shared_attrs_str = ', '.join([str(attr.get('value', '')) for attr in shared_attrs])
                else:
                    shared_attrs_str = ', '.join([str(a) for a in shared_attrs]) if shared_attrs else ''
                
                rows.append({
                    'Product_A_ID': product_a_id,
                    'Product_A_SIID': product_a.get('siid', ''),
                    'Product_A_UUID': product_a.get('uuid', ''),
                    'Store_A': store_a,
                    'Product_A_Name': product_a.get('product_name', 'N/A'),
                    'Product_A_Description': product_a.get('description', ''),
                    'Product_A_URL': product_a.get('url', ''),
                    'Rank': rank,
                    'Product_B_ID': prod_b.get('id', ''),
                    'Product_B_SIID': prod_b.get('siid', ''),
                    'Product_B_UUID': prod_b.get('uuid', ''),
                    'Product_B_Name': prod_b.get('product_name', 'N/A'),
                    'Product_B_Description': prod_b.get('description', ''),
                    'Product_B_URL': prod_b.get('url', ''),
                    'Store_B': store_b,
                    'Combined_Score': round(prod_b.get('combined_score', 0.0), 3) if prod_b.get('combined_score') else '',
                    'Graph_Score': round(prod_b.get('weighted_score', 0.0), 3),
                    'Name_Similarity': round(prod_b.get('name_similarity', 0.0), 3) if prod_b.get('name_similarity') else '',
                    'Description_Similarity': round(prod_b.get('description_similarity', 0.0), 3) if prod_b.get('description_similarity') else '',
                    'Embedding_Score': round(prod_b.get('embedding_similarity', 0.0), 3) if prod_b.get('embedding_similarity') else '',
                    'Euclidean_Similarity': round(prod_b.get('euclidean_similarity', 0.0), 3) if prod_b.get('euclidean_similarity') is not None else '',
                    'Overlap': prod_b.get('overlap', ''),
                    'Brand_B': prod_b.get('brand', ''),
                    'Price_B': prod_b.get('price', ''),
                    'Category_B': prod_b.get('category', ''),
                    'Product_Type_B': prod_b.get('product_type', ''),
                    'Format_B': prod_b.get('format', ''),
                    'Shared_Attributes': shared_attrs_str
                })
    
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Replace NaN and infinite values with empty strings
    df.replace([np.inf, -np.inf], '', inplace=True)
    df.fillna('', inplace=True)
    
    # Export to CSV with semicolon delimiter
    # DISABLED: CSV generation commented out to avoid creating files in repo
    # df.to_csv(output_path, index=False, sep=';', encoding='utf-8')
    
    # logging.info(f"✅ CSV file created: {output_path}")
    logging.info(f"⚠️ CSV export disabled - only Excel exports will be generated")
    return None  # Return None instead of path since CSV not created



def export_cross_store_results_to_excel(
    results: Dict[str, Dict],
    store_a: str,
    store_b: str,
    output_path: Optional[str] = None
) -> str:
    """
    Export cross-store analysis results to formatted Excel file.
    
    Creates a well-formatted Excel workbook with:
    - Color-coded rankings (green=1st, yellow=2nd, red=3rd)
    - Frozen header and first columns
    - Proper column widths
    - Conditional formatting
    
    Args:
        results: Results from find_cross_store_similarities()
        store_a: Name of origin store
        store_b: Name of destination store
        output_path: Path to Excel file (optional, auto-generated if None)
        
    Returns:
        str: Path to created Excel file (or CSV if Excel creation fails)
    """
    # Generate filename if not provided
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = str(_get_results_dir() / f"cross_store_similarity_{store_a}_to_{store_b}_{timestamp}.xlsx")
    else:
        output_path = _ensure_parent_dir(output_path)
    
    # Prepare data for Excel
    rows = []
    
    for product_a_id, data in results.items():
        # Skip metadata
        if product_a_id == '_metadata':
            continue
        
        product_a = data.get('product_a', {})
        similar_b_list = data.get('similar_products_b', [])
        is_not_found = data.get('not_found', False)
        
        if not similar_b_list:
            # Add row even if no matches found or product not found
            status = 'NOT FOUND IN DATABASE' if is_not_found else 'No matches'
            rows.append({
                'Product_A_ID': product_a_id,
                'Product_A_SIID': product_a.get('siid', ''),
                'Product_A_Name': product_a.get('product_name', 'N/A'),
                'Product_A_Description': product_a.get('description', ''),
                'Product_A_URL': product_a.get('url', ''),
                'Brand_A': product_a.get('brand', ''),
                'Store_A': store_a,
                'Rank': status,
                'Product_B_ID': '',
                'Product_B_SIID': '',
                'Product_B_Name': '',
                'Product_B_Description': '',
                'Product_B_URL': '',
                'Brand_B': '',
                'Store_B': store_b,
                'Combined_Score': '',
                'Graph_Score': '',
                'Name_Similarity': '',
                'Description_Similarity': '',
                'Embedding_Score': '',
                'Euclidean_Similarity': '',
                'Overlap': '',
                'Brand_B': '',
                'Price_B': '',
                'Category_B': '',
                'Product_Type_B': '',
                'Format_B': '',
                'Shared_Attributes': ''
            })
        else:
            # Add one row per similar product
            for rank, prod_b in enumerate(similar_b_list, 1):
                # Get shared attributes
                shared_attrs = prod_b.get('shared_nodes_details', [])
                # Handle both old format (list of strings) and new format (list of dicts)
                if shared_attrs and isinstance(shared_attrs[0], dict):
                    shared_attrs_str = ', '.join([str(attr.get('value', '')) for attr in shared_attrs])
                else:
                    shared_attrs_str = ', '.join([str(a) for a in shared_attrs]) if shared_attrs else ''
                
                rows.append({
                    'Product_A_ID': product_a_id,
                    'Product_A_SIID': product_a.get('siid', ''),
                    'Store_A': store_a,
                    'Product_A_Name': product_a.get('product_name', 'N/A'),
                    'Product_A_Description': product_a.get('description', ''),
                    'Product_A_URL': product_a.get('url', ''),
                    'Brand_A': product_a.get('brand', ''),
                    'Rank': rank,
                    'Product_B_ID': prod_b.get('id', ''),
                    'Product_B_SIID': prod_b.get('siid', ''),
                    'Product_B_Name': prod_b.get('product_name', 'N/A'),
                    'Product_B_Description': prod_b.get('description', ''),
                    'Product_B_URL': prod_b.get('url', ''),
                    'Brand_B': prod_b.get('brand', ''),
                    'Store_B': store_b,
                    'Combined_Score': round(prod_b.get('combined_score', 0.0), 3) if prod_b.get('combined_score') else '',
                    'Graph_Score': round(prod_b.get('weighted_score', 0.0), 3),
                    'Name_Similarity': round(prod_b.get('name_similarity', 0.0), 3) if prod_b.get('name_similarity') else '',
                    'Description_Similarity': round(prod_b.get('description_similarity', 0.0), 3) if prod_b.get('description_similarity') else '',
                    'Embedding_Score': round(prod_b.get('embedding_similarity', 0.0), 3) if prod_b.get('embedding_similarity') else '',
                    'Euclidean_Similarity': round(prod_b.get('euclidean_similarity', 0.0), 3) if prod_b.get('euclidean_similarity') is not None else '',
                    'Overlap': prod_b.get('overlap', ''),
                    'Brand_B': prod_b.get('brand', ''),
                    'Price_B': prod_b.get('price', ''),
                    'Category_B': prod_b.get('category', ''),
                    'Product_Type_B': prod_b.get('product_type', ''),
                    'Format_B': prod_b.get('format', ''),
                    'Shared_Attributes': shared_attrs_str
                })
    
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Replace NaN and infinite values with empty strings to avoid Excel export errors
    df.replace([np.inf, -np.inf], '', inplace=True)
    df.fillna('', inplace=True)
    
    # Create Excel writer with xlsxwriter engine for formatting
    # Suppress xlsxwriter URL limit warnings
    import warnings
    warnings.filterwarnings('ignore', message='.*Ignoring URL.*exceeds Excel.*', category=UserWarning)
    
    try:
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Cross-Store Similarity', index=False)
            
            # Get workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets['Cross-Store Similarity']
            
            # Define formats
            header_format = workbook.add_format({
                'bold': True,
                'bg_color': '#4472C4',
                'font_color': 'white',
                'border': 1,
                'align': 'center',
                'valign': 'vcenter'
            })
            
            rank1_format = workbook.add_format({
                'bg_color': '#C6EFCE',
                'border': 1
            })
            
            rank2_format = workbook.add_format({
                'bg_color': '#FFEB9C',
                'border': 1
            })
            
            rank3_format = workbook.add_format({
                'bg_color': '#FFC7CE',
                'border': 1
            })
            
            not_found_format = workbook.add_format({
                'bg_color': '#FF9999',
                'font_color': '#800000',
                'bold': True,
                'border': 1
            })
            
            # Set column widths
            worksheet.set_column('A:A', 5)  # Product_A_ID
            worksheet.set_column('B:B', 5)  # Product_A_SIID
            worksheet.set_column('C:C', 15)  # Store_A
            worksheet.set_column('D:D', 20)  # Product_A_Name
            worksheet.set_column('E:E', 15)  # Product_A_Description
            worksheet.set_column('F:F', 15)  # Product_A_URL
            worksheet.set_column('G:G', 5)   # Rank
            worksheet.set_column('H:H', 10)  # Product_B_ID
            worksheet.set_column('I:I', 30)  # Product_B_SIID
            worksheet.set_column('J:J', 40)  # Product_B_Name
            worksheet.set_column('K:K', 60)  # Product_B_Description
            worksheet.set_column('L:L', 50)  # Product_B_URL
            worksheet.set_column('M:M', 15)  # Store_B
            worksheet.set_column('N:N', 15)  # Combined_Score
            worksheet.set_column('O:O', 12)  # Graph_Score
            worksheet.set_column('P:P', 15)  # Name_Similarity
            worksheet.set_column('Q:Q', 18)  # Description_Similarity
            worksheet.set_column('R:R', 15)  # Embedding_Score
            worksheet.set_column('S:S', 18)  # Euclidean_Similarity
            worksheet.set_column('T:T', 10)  # Overlap
            worksheet.set_column('U:U', 20)  # Brand_B
            worksheet.set_column('V:V', 12)  # Price_B
            worksheet.set_column('W:W', 30)  # Category_B
            worksheet.set_column('X:X', 20)  # Product_Type_B
            worksheet.set_column('Y:Y', 15)  # Format_B
            worksheet.set_column('Z:Z', 60)  # Shared_Attributes
            
            # Apply header format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
            
            # Apply conditional formatting for ranks
            for row_num in range(1, len(df) + 1):
                rank_value = df.iloc[row_num - 1]['Rank']
                if rank_value == 'NOT FOUND IN DATABASE':
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], not_found_format)
                elif rank_value == 1:
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], rank1_format)
                elif rank_value == 2:
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], rank2_format)
                elif rank_value == 3:
                    for col_num in range(len(df.columns)):
                        worksheet.write(row_num, col_num, df.iloc[row_num - 1, col_num], rank3_format)
            
            # Freeze panes (freeze header row and first 7 columns: ID, SIID, Store_A, Name, Description, URL, Rank)
            worksheet.freeze_panes(1, 7)
            
        logging.info(f"✅ Excel file created: {output_path}")
        logging.info(f"   Total rows: {len(rows)}")
        return output_path
        
    except Exception as e:
        logging.error(f"❌ Error creating Excel file: {e}")
        # Fallback: save as simple CSV
        csv_path = output_path.replace('.xlsx', '.csv')
        df.to_csv(csv_path, index=False)
        logging.info(f"⚠️ Saved as CSV instead: {csv_path}")
        return csv_path
