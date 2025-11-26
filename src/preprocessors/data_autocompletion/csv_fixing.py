import pandas as pd
import re
from dotenv import load_dotenv
import logging
import sys
from pathlib import Path

# Load environment variables from .env file
load_dotenv()

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.preprocessors.data_autocompletion.stopwords import process_allergen_column, find_unknown_allergens, ALLERGEN_STOPWORDS

from config.settings import PROJECT_ROOT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CSVFixer:
    def __init__(self, ):        
        # Columns to compare and potentially fill
        self.columns_to_compare = [
            'id', 'ean', 'gtin', 'url', 'category', 'product_type', 
            'brand', 'price', 'currency', 'siid', 'image_list'
        ]

    # --- INGREDIENTS CLEANING LOGIC ---
    def is_only_stopwords(self, text):
        """
        Check if a text contains only stopwords (no actual ingredient names).
        Returns True if all words are stopwords.
        """
        if not text or pd.isna(text):
            return True
        
        text_lower = str(text).lower()
        # Remove punctuation and split into words
        words = re.findall(r'\b\w+\b', text_lower)
        
        if not words:
            return True
        
        # Check if all words are stopwords
        return all(word in ALLERGEN_STOPWORDS for word in words)
    
    def remove_stopwords_from_text(self, text):
        """
        Remove stopwords from a text string while preserving non-stopword content.
        Returns the cleaned text with stopwords removed.
        """
        if not text or pd.isna(text):
            return ''
        
        text_str = str(text).strip()
        words = text_str.split()
        filtered_words = []
        
        for word in words:
            word_clean = word.strip('.,;:()[]{}').lower()
            # Keep the word if it's not a stopword
            if word_clean and word_clean not in ALLERGEN_STOPWORDS:
                filtered_words.append(word)
        
        return ' '.join(filtered_words)
    
    def extract_ingredient_and_info(self, ingredient_text):
        if not ingredient_text or pd.isna(ingredient_text):
            return ('', '')
        ingredient_text = str(ingredient_text).strip()
        ingredient_text = re.sub(r'\bcontains?\b', '', ingredient_text, flags=re.IGNORECASE).strip()
        colon_match = re.match(r'^(.*?):\s*([E]-?\d{3}(?:\s*(?:,|and)\s*[E]-?\d{3})*)$', ingredient_text)
        if colon_match:
            main_name = colon_match.group(1).strip()
            additives = colon_match.group(2).strip()
            additives = re.sub(r'\s*and\s*', ', ', additives)
            return (main_name, additives)
        percentage_pattern = r'[\(\[]?\d+[\.,]?\d*\s*%[\)\]]?'
        parentheses_pattern = r'\(([^)]+)\)|\[([^\]]+)\]'
        extra_info_parts = []
        matches = list(re.finditer(parentheses_pattern, ingredient_text))
        replacements = []
        for match in matches:
            content = (match.group(1) or match.group(2)).strip()
            full_match = match.group(0)
            # Si el contenido tiene porcentaje, añádelo siempre a extra_info_parts
            if re.search(r'\d+[\.,]?\d*\s*%', content):
                extra_info_parts.append(content)
                replacements.append((full_match, ''))
                continue
            keep_in_ingredient_patterns = [
                r'\b(extra virgin|virgin|refined|unrefined)\b',
                r'\b(whole|skimmed|semi-skimmed|full fat|low fat)\b',
                r'\b(powder|concentrate|extract|puree|juice)\b',
                r'\b(from concentrate|from concentrates)\b',
            ]
            content_lower = content.lower()
            should_keep = any(re.search(pattern, content_lower) for pattern in keep_in_ingredient_patterns)
            if should_keep and len(content.split()) <= 3:
                replacements.append((full_match, f' {content} '))
            else:
                extra_info_parts.append(content)
                replacements.append((full_match, ''))
        for old, new in replacements:
            ingredient_text = ingredient_text.replace(old, new, 1)
        percentages = re.findall(percentage_pattern, ingredient_text)
        for pct in percentages:
            extra_info_parts.append(pct.strip('()[] '))
            ingredient_text = ingredient_text.replace(pct, '', 1)
        ingredient_name = ingredient_text.strip()
        ingredient_name = re.sub(r'\bcontains?\b', '', ingredient_name, flags=re.IGNORECASE).strip()
        
        # Remove stopwords from ingredient name
        ingredient_name = self.remove_stopwords_from_text(ingredient_name)
        
        ingredient_name = re.sub(r'\s+', ' ', ingredient_name)
        ingredient_name = re.sub(r'\s*([/,:;])\s*', r'\1', ingredient_name)
        ingredient_name = ingredient_name.strip('.,;:[]() ')
        ingredient_name = ingredient_name.rstrip('.')
        def normalize_extra_info(info):
            info = re.sub(r'\bcontains?\b', '', info, flags=re.IGNORECASE).strip()
            info = re.sub(r'(E-?\d{3})\s*and\s*(E-?\d{3})', r'\1, \2', info)
            
            # Remove stopwords from extra_info
            info = self.remove_stopwords_from_text(info)
            
            info = info.strip()
            info = re.sub(r'\s+', ' ', info)
            info = re.sub(r'\s*([/,:;])\s*', r'\1', info)
            info = info.strip('.,;:[]() ')
            info = info.rstrip('.')
            return info
        extra_info = ', '.join([normalize_extra_info(e) for e in extra_info_parts]) if extra_info_parts else ''
        return (ingredient_name, extra_info)

    def process_ingredients_column(self, df, column_name='ingredients'):
        if column_name not in df.columns:
            return df
        df = df.copy()
        ingredients_clean = []
        ingredients_extra = []
        for value in df[column_name]:
            if pd.isna(value) or not str(value).strip():
                ingredients_clean.append('')
                ingredients_extra.append('')
                continue
            
            # Remove all asterisks (*) from the ingredients text before processing
            value_cleaned = str(value).replace('*', '')
            
            def split_ingredients(text):
                # Primero reemplazar " and " por una coma cuando está fuera de paréntesis
                result = []
                buf = ''
                paren_level = 0
                i = 0
                
                while i < len(text):
                    char = text[i]
                    
                    if char in '([':
                        paren_level += 1
                        buf += char
                        i += 1
                    elif char in ')]':
                        paren_level = max(paren_level - 1, 0)
                        buf += char
                        i += 1
                    # Detectar " and " cuando está fuera de paréntesis
                    elif paren_level == 0 and i + 5 <= len(text) and text[i:i+5].lower() == ' and ':
                        if buf.strip():
                            result.append(buf.strip())
                        buf = ''
                        i += 5  # Saltar " and "
                    # Otros separadores: ',;/.'
                    elif char in ',;/.' and paren_level == 0:
                        if buf.strip():
                            result.append(buf.strip())
                        buf = ''
                        i += 1
                    else:
                        buf += char
                        i += 1
                
                if buf.strip():
                    result.append(buf.strip())
                return result
            parts = split_ingredients(value_cleaned)
            clean_names = []
            extra_infos = []
            for part in parts:
                if part.strip():
                    # Skip parts that are only stopwords (like "May contain")
                    if self.is_only_stopwords(part):
                        continue
                    
                    name, info = self.extract_ingredient_and_info(part)
                    if name and not self.is_only_stopwords(name):
                        clean_names.append(name)
                    extra_infos.append(info if info else '')
            ingredients_clean.append('/'.join(clean_names))
            ingredients_extra.append('/'.join(extra_infos))
        df[column_name] = ingredients_clean
        extra_info_col = f'{column_name}_extra_info'
        df[extra_info_col] = ingredients_extra
        cols = list(df.columns)
        if extra_info_col in cols and column_name in cols:
            cols.remove(extra_info_col)
            idx = cols.index(column_name)
            cols.insert(idx + 1, extra_info_col)
            df = df[cols]
        return df

    # --- ALLERGENS CLEANING LOGIC ---
    def process_allergens_column(self, df, column_name='allergens', verbose=True):
        if column_name not in df.columns:
            return df
        df = process_allergen_column(df, column_name)
        unknown = find_unknown_allergens(df, column_name)
        if verbose and unknown:
            print(f"\n⚠ Found {len(unknown)} unknown allergens:")
            for allergen in sorted(unknown):
                print(f"  - {allergen}")
        return df

    def analyze_column_content(self, df: pd.DataFrame, df_name: str):
        """
        Analyze content count for each column.
        
        Args:
            df: DataFrame to analyze
            df_name: Name of the DataFrame (for logging)
        
        Returns:
            dict: Dictionary with column names and their non-null counts
        """
        stats = {}
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 Content Analysis for {df_name}")
        logger.info(f"{'='*60}")
        
        for col in self.columns_to_compare:
            if col in df.columns:
                non_null_count = df[col].notna().sum()
                null_count = df[col].isna().sum()
                total = len(df)
                percentage = (non_null_count / total * 100) if total > 0 else 0
                
                stats[col] = {
                    'non_null': non_null_count,
                    'null': null_count,
                    'percentage': percentage
                }
                
                logger.info(f"  {col:20s}: {non_null_count:5d}/{total:5d} ({percentage:5.1f}%) non-null")
            else:
                logger.warning(f"  {col:20s}: ❌ Column not found")
                stats[col] = None
        
        return stats

    def compare_and_fill(self, df_enriched: pd.DataFrame, df_translated: pd.DataFrame) -> pd.DataFrame:
        """
        Compare enriched and translated DataFrames and fill missing values.
        
        Args:
            df_enriched: Enriched DataFrame
            df_translated: Translated DataFrame
            
        Returns:
            pd.DataFrame: Updated enriched DataFrame with filled values
        """
        logger.info(f"\n{'='*60}")
        logger.info("🔄 Comparing and Filling Data")
        logger.info(f"{'='*60}")
        
        # Validate UUID column exists in both DataFrames
        if 'uuid' not in df_enriched.columns:
            logger.error("❌ 'uuid' column not found in enriched DataFrame")
            return df_enriched
        
        if 'uuid' not in df_translated.columns:
            logger.error("❌ 'uuid' column not found in translated DataFrame")
            return df_enriched
        
        # Analyze both DataFrames
        stats_enriched = self.analyze_column_content(df_enriched, "ENRICHED")
        stats_translated = self.analyze_column_content(df_translated, "TRANSLATED")

        # Create a copy for modifications
        df_result = df_enriched.copy()

        # Set uuid as index for faster lookups
        df_translated_indexed = df_translated.set_index('uuid')
        
        # Track filling statistics
        fill_stats = {col: 0 for col in self.columns_to_compare}

        
        # Process each column
        for col in self.columns_to_compare:
            # Check if column exists in both DataFrames
            if col not in df_enriched.columns:
                logger.warning(f"⚠️  Column '{col}' not in enriched DataFrame, skipping...")
                continue
            
            if col not in df_translated.columns:
                logger.warning(f"⚠️  Column '{col}' not in translated DataFrame, skipping...")
                continue
            
            # Compare counts
            enriched_count = stats_enriched[col]['non_null']
            translated_count = stats_translated[col]['non_null']
            
            logger.info(f"    Column: {col}")
            logger.info(f"    Enriched:   {enriched_count} non-null values")
            logger.info(f"    Translated: {translated_count} non-null values")
            
            # If enriched has less data than translated, fill from translated
            if enriched_count < translated_count:
                logger.info(f"    ✅ Will fill from translated (difference: {translated_count - enriched_count})")
                
                # Iterate through enriched rows
                for idx, row in df_result.iterrows():
                    uuid_value = row['uuid']
                    
                    # Check if current value is null or empty
                    current_value = row[col]
                    is_empty = pd.isna(current_value) or (isinstance(current_value, str) and current_value.strip() == '')
                    
                    if is_empty and uuid_value in df_translated_indexed.index:
                        # Get value from translated DataFrame
                        translated_value = df_translated_indexed.loc[uuid_value, col]
                        
                        # Fill only if translated value is not null
                        if pd.notna(translated_value) and (not isinstance(translated_value, str) or translated_value.strip() != ''):
                            df_result.at[idx, col] = translated_value
                            fill_stats[col] += 1
                
                logger.info(f"    📝 Filled {fill_stats[col]} values from translated")
            else:
                logger.info(f"    ⏭️  Enriched has equal or more data, skipping...")
        
        # Print summary
        logger.info(f"\n{'='*60}")
        logger.info("📊 Fill Summary")
        logger.info(f"{'='*60}")
        
        total_filled = sum(fill_stats.values())
        logger.info(f"  Total values filled: {total_filled}")
        
        for col, count in fill_stats.items():
            if count > 0:
                logger.info(f"    {col:20s}: {count} values filled")
        
        return df_result

    def fix_csv(self, enriched_file: str, translated_file: str, output_path: str, store_name: str = None, postcode: str = None):
        """
        Fix CSV file format and structure.
        
        Args:
            enriched_file: Path to enriched CSV file
            translated_file: Path to translated CSV file
            output_path: Path to save fixed CSV file
            store_name: Name of the store to fill in the 'store' column
            postcode: Postcode to fill in the 'postcode' column
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"🚀 Starting CSV Fixing Process")
        logger.info(f"{'='*60}")
        logger.info(f"Enriched file:  {enriched_file}")
        logger.info(f"Translated file: {translated_file}")
        logger.info(f"Output file:     {output_path}")
        logger.info(f"Store name:      {store_name}")
        logger.info(f"Postcode:        {postcode}")
        
        try:
            # Read CSV files
            
            df_enriched = pd.read_csv(
                enriched_file, 
                sep=";", 
                encoding='utf-8',
                dtype=str,
                na_values=['', 'NA', 'N/A', 'null', 'NULL'],
                keep_default_na=True
            )

            df_translated = pd.read_csv(
                translated_file, 
                sep=";", 
                encoding='utf-8',
                dtype=str,
                na_values=['', 'NA', 'N/A', 'null', 'NULL'],
                keep_default_na=True
            )
            
            # Basic cleaning of enriched DataFrame
            logger.info(f"\n🧹 Cleaning enriched DataFrame...")
            
            # Strip whitespace from column names
            df_enriched.columns = df_enriched.columns.str.strip()
            df_translated.columns = df_translated.columns.str.strip()
            
            print(df_enriched.columns.tolist())
            print(df_translated.columns.tolist())     

            # Strip whitespace from string values
            for col in df_enriched.select_dtypes(include=['object']).columns:
                df_enriched[col] = df_enriched[col].str.strip() if df_enriched[col].dtype == 'object' else df_enriched[col]
            
            # Remove completely empty rows
            df_enriched = df_enriched.dropna(how='all')
            
            # Remove duplicate rows
            original_rows = len(df_enriched)
            df_enriched = df_enriched.drop_duplicates()
            duplicates_removed = original_rows - len(df_enriched)
            

            if duplicates_removed > 0:
                logger.info(f"  ✅ Removed {duplicates_removed} duplicate rows")

            # Compare and fill data from translated
            df_result = self.compare_and_fill(df_enriched, df_translated)

            # --- LIMPIEZA DE INGREDIENTES Y ALÉRGENOS ---
            if 'ingredients' in df_result.columns:
                logger.info(f"\n🧹 Cleaning ingredients column...")
                df_result = self.process_ingredients_column(df_result, 'ingredients')
            if 'allergens' in df_result.columns:
                logger.info(f"\n🧹 Cleaning allergens column...")
                df_result = self.process_allergens_column(df_result, 'allergens', verbose=False)

            # Update store column with store_name
            if store_name:
                if 'store' in df_result.columns:
                    # Construct store value with optional postcode
                    store_value = store_name
                    if postcode is not None:
                        store_value = f"{store_name}-{postcode}"
                    df_result['store'] = store_value
                    logger.info(f"  ✅ Updated 'store' column with: {store_value}")
                else:
                    logger.warning(f"  ⚠️  'store' column not found in DataFrame")

            # Update company column with store_name
            if store_name:
                if 'company' in df_result.columns:
                    df_result['company'] = store_name
                    logger.info(f"  ✅ Updated 'company' column with: {store_name}")
                else:
                    logger.warning(f"  ⚠️  'company' column not found in DataFrame")

            # Update postcode column if postcode is provided
            if postcode is not None:
                if 'postcode' in df_result.columns:
                    df_result['postcode'] = str(postcode)
                    logger.info(f"  ✅ Updated 'postcode' column with: {postcode}")
                else:
                    logger.warning(f"  ⚠️  'postcode' column not found in DataFrame")

            # --- CALCULAR price_without_vat SI FALTA ---
            if 'price' in df_result.columns and 'vat' in df_result.columns:
                # Ensure the price_without_vat column exists
                if 'price_without_vat' not in df_result.columns:
                    df_result['price_without_vat'] = pd.NA

                # Prepare sanitized numeric columns
                # Remove percent sign and normalize decimal comma to dot for VAT
                vat_series = df_result['vat'].astype(str).str.strip().replace({'nan': ''})
                vat_series = vat_series.str.replace('%', '', regex=False).str.replace(',', '.', regex=False)
                vat_num = pd.to_numeric(vat_series, errors='coerce')
                # Count VAT == 0 rows (we will ignore them for computation but do not modify original column)
                try:
                    zeros_count = int((vat_num == 0).sum())
                except Exception:
                    zeros_count = 0
                if zeros_count > 0:
                    logger.info(f"  ⚠️ Ignoring {zeros_count} rows where 'vat' == 0 for the computation (vat column unchanged)")

                # Sanitize price: remove any non-numeric except dot and minus, and convert comma to dot
                price_series = df_result['price'].astype(str).str.strip().replace({'nan': ''})
                price_series = price_series.str.replace(',', '.', regex=False)
                price_series = price_series.str.replace(r'[^0-9\.\-]', '', regex=True)
                price_num = pd.to_numeric(price_series, errors='coerce')

                pwv_series = df_result['price_without_vat'].astype(str).str.strip().replace({'nan': ''})
                pwv_series = pwv_series.str.replace(',', '.', regex=False)
                pwv_num = pd.to_numeric(pwv_series, errors='coerce')

                # If there's a `price_without_tax` column, treat rows where it's present as already completed
                price_without_tax_present = 'price_without_tax' in df_result.columns
                if price_without_tax_present:
                    pwt_series = df_result['price_without_tax'].astype(str).str.strip().replace({'nan': ''})
                    pwt_series = pwt_series.str.replace(',', '.', regex=False)
                    pwt_num = pd.to_numeric(pwt_series, errors='coerce')
                else:
                    pwt_num = pd.Series([pd.NA] * len(df_result), index=df_result.index)

                # Compute where price_without_vat is missing but price and vat are present
                # Also exclude rows where VAT == 0 (ignore them for computation)
                # Only compute when target is missing AND no price_without_tax present for that row
                mask_compute = pwv_num.isna() & price_num.notna() & vat_num.notna() & (vat_num != 0) & (pwt_num.isna())
                if mask_compute.any():
                    # price_without_vat = price / (1 + vat/100)
                    computed = price_num[mask_compute] / (1 + vat_num[mask_compute] / 100.0)
                    # Round to 2 decimals
                    computed = computed.round(2)
                    df_result.loc[mask_compute, 'price_without_vat'] = computed
                    logger.info(f"  ✅ Calculated 'price_without_vat' for {mask_compute.sum()} rows from 'price' and 'vat'")

            # --- TRANSFORMACIÓN FINAL: MINÚSCULAS Y COMPONENTS ---
            lowercase_cols = [
                'ingredients', 'ingredients_extra_info', 'allergens',
                'first_level_components', 'first_component_extra_info',
                'second_level_component', 'second_component_extra_info', 'components'
            ]
            for col in lowercase_cols:
                if col in df_result.columns:
                    df_result[col] = df_result[col].apply(lambda x: x.lower() if isinstance(x, str) else x)

            # Reemplazar ',' por '/' en la columna 'components'
            if 'components' in df_result.columns:
                df_result['components'] = df_result['components'].apply(
                    lambda x: x.replace(',', '/') if isinstance(x, str) else x
                )

            # Create output directory if it doesn't exist
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)

            # Save fixed CSV
            logger.info(f"\n💾 Saving fixed CSV...")
            df_result.to_csv(
                output_path,
                sep=';',
                encoding='utf-8',
                index=False,
                quoting=1  # QUOTE_ALL
            )

            logger.info(f"  ✅ Fixed CSV saved to: {output_path}")
            
        except FileNotFoundError as e:
            logger.error(f"❌ File not found: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Error fixing CSV: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ CSV Fixing Process Completed")
        logger.info(f"{'='*60}\n")

def main():
    enriched_file = str(PROJECT_ROOT / "data" / "processed" / "enriched" / "carrefour_enriched_2.csv")
    translated_file = enriched_file  # Si no hay archivo traducido, se puede usar el mismo
    output_file = str(PROJECT_ROOT / "data" / "processed" / "fixed" / "carrefour_enriched_2_fixed.csv")
    fixer = CSVFixer()
    fixer.fix_csv(enriched_file=enriched_file, translated_file=translated_file, output_path=output_file)

if __name__ == "__main__":
    main()