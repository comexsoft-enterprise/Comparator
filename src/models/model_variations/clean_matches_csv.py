"""Simple CSV deduper for cross-store similarity results.

Behaviour:
- Keep only one row per `Product_B_ID`: the row with the highest `Combined_Score`.
- For any `Product_A_ID` that ends with zero matches, add a single row marking no match
    (blank B-related fields and blank scores/rank), so the product remains represented.

Usage:
    - Edit `CSV_PATH` at the bottom of this file and run `python3 clean_matches_csv.py`
"""
from pathlib import Path
import pandas as pd


def load_dataframe(path: Path) -> pd.DataFrame:
    """Load CSV or Excel into a DataFrame, returning strings with no NA values.

    - For .xls/.xlsx uses `pd.read_excel`
    - For other files uses `pd.read_csv` with `;` separator. Tries utf-8 then latin-1.
    """
    suffix = path.suffix.lower()
    if suffix in ('.xls', '.xlsx'):
        df = pd.read_excel(path, dtype=str)
        df = df.fillna('')
        # ensure all values are strings
        df = df.astype(str)
        return df

    # otherwise assume CSV-like
    try:
        df = pd.read_csv(path, sep=';', dtype=str, keep_default_na=False, encoding='utf-8')
    except UnicodeDecodeError:
        # fallback to latin-1
        df = pd.read_csv(path, sep=';', dtype=str, keep_default_na=False, encoding='latin-1')

    df = df.fillna('')
    return df





def write_outputs(df_out: pd.DataFrame, src_path: Path):
    # out_csv = src_path.with_name(src_path.stem + '_cleaned').with_suffix('.csv')
    # Only write Excel output, CSV generation disabled
    out_xlsx = src_path.with_name(src_path.stem + '_cleaned').with_suffix('.xlsx')
    # df_out.to_csv(out_csv, sep=';', index=False)
    df_out.to_excel(out_xlsx, index=False)
    # return out_csv, out_xlsx
    # CSV generation disabled to avoid creating files in repo
    # out_csv = src_path.with_name(src_path.stem + '_cleaned').with_suffix('.csv')
    # df_out.to_csv(out_csv, sep=';', index=False)
    # return out_csv, out_xlsx
    return None, out_xlsx


def main(csv_path: str):
    """Run dedupe on the provided CSV path (relative to repo root or script).

    csv_path: relative or absolute path to source CSV file.
    """
    src = Path(csv_path)
    # try some common resolutions if path is relative
    if not src.exists():
        cand = Path.cwd() / src
        if cand.exists():
            src = cand

    if not src.exists():
        print(f'File not found: {csv_path}')
        return 2

    print(f'Reading {src}')
    df = load_dataframe(src)
    if df.empty:
        print('Input CSV is empty')
        return 1

    header = list(df.columns)
    cs_col = 'Combined_Score'
    b_col = 'Product_B_ID'
    a_col = 'Product_A_ID'

    def parse_cs(x):
        try:
            return float(x)
        except Exception:
            return float('-inf')

    df['_cs_val'] = df.get(cs_col, '').apply(parse_cs)

    # Build mapping of Product_B_ID -> indices (ignore blanks)
    b_to_idxs = {}
    for idx, val in df[b_col].fillna('').astype(str).items():
        if val.strip() == '':
            continue
        b_to_idxs.setdefault(val, []).append(idx)

    # Keep only best row per Product_B_ID
    keep_indices = set()
    for b, idxs in b_to_idxs.items():
        best = max(idxs, key=lambda i: df.at[i, '_cs_val'])
        keep_indices.add(best)

    # Also keep rows that had blank Product_B_ID (they may represent no-match rows already)
    for idx, val in df[b_col].fillna('').astype(str).items():
        if val.strip() == '':
            keep_indices.add(idx)

    df_clean = df.loc[sorted(keep_indices)].copy()

    # For any Product_A_ID that now has zero matches (no rows with a Product_B_ID), add a 'not found' row
    original_as = set(df[a_col].tolist())
    remaining_as = set(df_clean[a_col].tolist())

    added = []
    # Identify B-related columns to blank
    b_cols = [c for c in header if c.startswith('Product_B') or c.startswith('Store_B') or '_B' in c]

    for a_id in sorted(original_as):
        if a_id not in remaining_as:
            # pick first original row for product A
            base = df[df[a_col] == a_id].iloc[0].to_dict()
            for c in b_cols:
                if c in base:
                    base[c] = ''
            if 'Rank' in base:
                base['Rank'] = ''
            if cs_col in base:
                base[cs_col] = ''
            added.append(base)

    # Preserve _cs_val for sorting during rank recalculation
    if added:
        df_added = pd.DataFrame(added, columns=list(df.columns))
        df_out = pd.concat([df_clean, df_added], ignore_index=True)
    else:
        df_out = df_clean.copy()

    # ============================================================
    # RECALCULATE RANK FOR EACH PRODUCT_A_ID
    # ============================================================
    # After removing duplicate Product_B_IDs, some Product_A may have lost matches
    # We need to recalculate the Rank based on remaining matches sorted by Combined_Score
    
    if 'Rank' in df_out.columns and '_cs_val' in df_out.columns:
        # Separate rows with matches from rows without matches (no-match rows)
        has_match = df_out[b_col].fillna('').astype(str).str.strip() != ''
        df_with_matches = df_out[has_match].copy()
        df_no_matches = df_out[~has_match].copy()
        
        # For rows with matches, recalculate rank per Product_A_ID
        if not df_with_matches.empty:
            # Sort by Product_A_ID and Combined_Score descending
            df_with_matches = df_with_matches.sort_values(
                by=[a_col, '_cs_val'], 
                ascending=[True, False]
            )
            
            # Assign new rank within each Product_A_ID group
            df_with_matches['Rank'] = df_with_matches.groupby(a_col).cumcount() + 1
            df_with_matches['Rank'] = df_with_matches['Rank'].astype(str)
        
        # For no-match rows, keep Rank empty
        if not df_no_matches.empty:
            df_no_matches['Rank'] = ''
        
        # Combine back together
        df_out = pd.concat([df_with_matches, df_no_matches], ignore_index=True)
        
        # Sort by Product_A_ID and Rank for better readability
        # Empty ranks go to the end
        df_out['_rank_sort'] = df_out['Rank'].apply(lambda x: float(x) if x != '' else float('inf'))
        df_out = df_out.sort_values(by=[a_col, '_rank_sort'])
        df_out = df_out.drop(columns=['_rank_sort'], errors='ignore')

    # Drop helper columns and keep only original columns
    df_out = df_out.drop(columns=['_cs_val'], errors='ignore')
    
    # Ensure output has only the original columns in the original order
    final_cols = [c for c in header if c in df_out.columns]
    df_out = df_out[final_cols]

    out_csv, out_xlsx = write_outputs(df_out, src)
    if out_csv:
        print(f'Wrote cleaned CSV: {out_csv}')
    else:
        print('CSV generation disabled')
    print(f'Wrote cleaned Excel: {out_xlsx}')
    return 0


if __name__ == '__main__':
    # Simple hardcoded path for easy testing: set your relative CSV path here
    CSV_PATH = 'cross_store_similarity_eroski-01013_to_makro-01013_20251217_142238.xlsx'
    print(f'Using CSV_PATH = {CSV_PATH}')
    raise SystemExit(main(CSV_PATH))
