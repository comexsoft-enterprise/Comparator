import pandas as pd
import re
from dotenv import load_dotenv
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm

# Load environment variables from .env file
load_dotenv()

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.preprocessors.data_autocompletion.stopwords import process_allergen_column, find_unknown_allergens, ALLERGEN_STOPWORDS

from config.settings import PROJECT_ROOT

from data.schemas.categories import (
    InternalCategoryNonFood, 
    InternalType, 
    InternalCategoryFood, 
    get_valid_subcategories_for_category,
    get_category_subcategory_map,
    get_all_subcategories
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


product_types = [t.value for t in InternalType]
food_categories = [c.value for c in InternalCategoryFood]
non_food_categories = [c.value for c in InternalCategoryNonFood]
all_categories = food_categories + non_food_categories
subcategories_by_category: Dict[str, List[str]] = {
    category: get_valid_subcategories_for_category(category)
    for category in all_categories
}


class CSVFixer:
    def __init__(self, ):        
        # Columns to compare and potentially fill
        self.columns_to_compare = [
            'id', 'ean', 'gtin', 'url', 'category', 'product_type', 
            'brand', 'price', 'currency', 'siid', 'image_list'
        ]
        
        # Thread lock for safe DataFrame updates
        self.lock = Lock()
        
        # Load valid categorization values
        self.valid_types = [t.value for t in InternalType]
        self.valid_categories_food = [c.value for c in InternalCategoryFood]
        self.valid_categories_non_food = [c.value for c in InternalCategoryNonFood]
        self.valid_categories_all = self.valid_categories_food + self.valid_categories_non_food
        
        # Get all valid subcategories
        self.category_subcategory_map = get_category_subcategory_map()
        self.valid_subcategories_all = get_all_subcategories()

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
    
    # --- CATEGORIZATION VALIDATION AND FIXING ---
    def validate_categorization_value(self, value: str, valid_values: list) -> tuple:
        """
        Validate if a value is in the list of valid values.
        Returns (is_valid, cleaned_value)
        """
        if pd.isna(value) or not str(value).strip():
            return False, ''
        
        value_clean = str(value).strip()
        
        # Exact match
        if value_clean in valid_values:
            return True, value_clean
        
        # Case-insensitive match
        value_lower = value_clean.lower()
        for valid in valid_values:
            if value_lower == valid.lower():
                return True, valid
        
        return False, value_clean
    
    def validate_type_category_consistency(self, type_val: str, category_val: str) -> bool:
        """
        Validate that category matches the type (Food categories only for Food type, etc.)
        Returns True if consistent, False otherwise
        """
        if pd.isna(type_val) or pd.isna(category_val):
            return False
        
        type_clean = str(type_val).strip().lower()
        category_clean = str(category_val).strip().lower()
        
        # Check if type is "food"
        if type_clean == 'food':
            # Category must be in food categories
            food_categories_lower = [c.lower() for c in self.valid_categories_food]
            return category_clean in food_categories_lower
        
        # Check if type is "non-food"
        elif type_clean == 'non-food':
            # Category must be in non-food categories
            non_food_categories_lower = [c.lower() for c in self.valid_categories_non_food]
            return category_clean in non_food_categories_lower
        
        return False
    
    def validate_subcategory_for_category(self, category: str, subcategory: str) -> bool:
        """
        Validate if subcategory is valid for the given category.
        """
        if pd.isna(category) or pd.isna(subcategory):
            return False
        
        category_clean = str(category).strip()
        subcategory_clean = str(subcategory).strip()
        
        if category_clean not in self.category_subcategory_map:
            return False
        
        valid_subcats = [sc.value for sc in self.category_subcategory_map[category_clean]]
        
        # Case-insensitive match
        subcategory_lower = subcategory_clean.lower()
        for valid_sc in valid_subcats:
            if subcategory_lower == valid_sc.lower():
                return True
        
        return False
    
    def fix_single_categorization_with_llm(
        self, 
        row_data: dict,
        api_key: str,
        deployment_name: str,
        api_base: str
    ) -> dict:
        """
        Fix a single row's categorization using LLM.
        Returns dict with corrected values: {type, internal_category, internal_subcategory}
        """
        from openai import OpenAI
        import json
        
        client = OpenAI(api_key=api_key, base_url=api_base)
        
        # Build subcategory prompt section
        subcategory_prompt = "\n"
        for category, subcat_enum in self.category_subcategory_map.items():
            subcats = [sc.value for sc in subcat_enum]
            subcategory_prompt += f"If internal_category is '{category}', choose internal_subcategory from:\n"
            subcategory_prompt += "\n".join(f"  - {sc}" for sc in subcats)
            subcategory_prompt += "\n\n"
        
        context = f"""You are a product categorization expert. Your task is to correctly categorize a product.

            PRODUCT INFORMATION:
            - Product Name: {row_data.get('product_name', 'N/A')}
            - Description: {row_data.get('description', 'N/A')}
            - Brand: {row_data.get('brand', 'N/A')}
            - Category (original): {row_data.get('category', 'N/A')}
            - Current Type: {row_data.get('type', 'N/A')}
            - Current Internal Category: {row_data.get('internal_category', 'N/A')}
            - Current Internal Subcategory: {row_data.get('internal_subcategory', 'N/A')}

            VALID OPTIONS:

            1. TYPE (choose one):
            {chr(10).join(f"   - {t}" for t in self.valid_types)}

            2. INTERNAL_CATEGORY (choose based on type):

            If type is "Food", choose from:
            {chr(10).join(f"   - {c}" for c in self.valid_categories_food)}

            If type is "Non-Food", choose from:
            {chr(10).join(f"   - {c}" for c in self.valid_categories_non_food)}

            3. INTERNAL_SUBCATEGORY (choose based on internal_category):
            {subcategory_prompt}

            INSTRUCTIONS:
            1. Analyze the product information
            2. Choose the MOST APPROPRIATE values from the valid options above
            3. Return ONLY a valid JSON object with the three fields
            4. Do NOT use markdown formatting, explanations, or code blocks

            Return format:
            {{
                "type": "<type>",
                "internal_category": "<internal_category>",
                "internal_subcategory": "<internal_subcategory>"
            }}
            """
        
        prompt = "Categorize this product correctly:"
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.responses.create(
                    model=deployment_name,
                    input=[
                        {"role": "system", "content": context},
                        {"role": "user", "content": prompt}
                    ],
                    reasoning={
                        "effort": "low"
                    },
                    text={
                        "verbosity": "low"
                    }
                )
                
                content = response.output[1].content[0].text.strip()
                
                # Remove markdown if present
                if content.startswith("```"):
                    first_newline = content.find('\n')
                    if first_newline != -1:
                        content = content[first_newline + 1:]
                    if content.endswith("```"):
                        content = content[:-3].strip()
                
                result = json.loads(content)
                
                # Validate result
                type_val = result.get('type', '')
                cat_val = result.get('internal_category', '')
                subcat_val = result.get('internal_subcategory', '')
                
                type_valid, type_clean = self.validate_categorization_value(type_val, self.valid_types)
                cat_valid, cat_clean = self.validate_categorization_value(cat_val, self.valid_categories_all)
                subcat_valid = self.validate_subcategory_for_category(cat_clean, subcat_val)
                
                if type_valid and cat_valid and subcat_valid:
                    return {
                        'type': type_clean,
                        'internal_category': cat_clean,
                        'internal_subcategory': subcat_val
                    }
                else:
                    logger.warning(f"  ⚠️ Attempt {attempt + 1}: Invalid categorization returned by LLM")
                    logger.warning(f"     Type valid: {type_valid}, Category valid: {cat_valid}, Subcategory valid: {subcat_valid}")
                    if attempt < max_retries - 1:
                        logger.info(f"  🔄 Retrying...")
                        continue
                    else:
                        logger.error(f"  ❌ Max retries reached, using original values")
                        return {
                            'type': row_data.get('type', ''),
                            'internal_category': row_data.get('internal_category', ''),
                            'internal_subcategory': row_data.get('internal_subcategory', '')
                        }
                        
            except json.JSONDecodeError as e:
                logger.error(f"  ❌ JSON parsing error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    continue
                else:
                    return {
                        'type': row_data.get('type', ''),
                        'internal_category': row_data.get('internal_category', ''),
                        'internal_subcategory': row_data.get('internal_subcategory', '')
                    }
            except Exception as e:
                logger.error(f"  ❌ Error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    continue
                else:
                    return {
                        'type': row_data.get('type', ''),
                        'internal_category': row_data.get('internal_category', ''),
                        'internal_subcategory': row_data.get('internal_subcategory', '')
                    }
        
        return {
            'type': row_data.get('type', ''),
            'internal_category': row_data.get('internal_category', ''),
            'internal_subcategory': row_data.get('internal_subcategory', '')
        }
    
    def validate_and_fix_categorization(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate and fix categorization columns (internaltype, internalcategory, internalsubcategory).
        Uses LLM to correct invalid values.
        """
        logger.info(f"\n{'='*60}")
        logger.info("🔍 Validating Categorization Columns")
        logger.info(f"{'='*60}")
        
        # Check if required columns exist
        required_cols = ['internaltype', 'internalcategory', 'internalsubcategory']
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
            logger.warning(f"⚠️ Missing categorization columns: {missing_cols}")
            logger.warning(f"   Skipping categorization validation")
            return df
        
        df_result = df.copy()
        
        # Statistics
        total_rows = len(df_result)
        invalid_rows = []
        
        # Validate each row
        for idx, row in df_result.iterrows():
            type_val = row['internaltype']
            cat_val = row['internalcategory']
            subcat_val = row['internalsubcategory']
            
            # Validate type
            type_valid, type_clean = self.validate_categorization_value(type_val, self.valid_types)
            
            # Validate category
            cat_valid, cat_clean = self.validate_categorization_value(cat_val, self.valid_categories_all)
            
            # Validate type-category consistency
            type_cat_consistent = self.validate_type_category_consistency(type_val, cat_val) if type_valid and cat_valid else False
            
            # Validate subcategory
            subcat_valid = self.validate_subcategory_for_category(cat_clean, subcat_val) if cat_valid else False
            
            # If any validation fails, mark for fixing
            if not (type_valid and cat_valid and type_cat_consistent and subcat_valid):
                invalid_rows.append({
                    'index': idx,
                    'type': type_val,
                    'internal_category': cat_val,
                    'internal_subcategory': subcat_val,
                    'type_valid': type_valid,
                    'cat_valid': cat_valid,
                    'type_cat_consistent': type_cat_consistent,
                    'subcat_valid': subcat_valid
                })
            else:
                # Update with cleaned values (in case of case mismatch)
                df_result.at[idx, 'internaltype'] = type_clean
                df_result.at[idx, 'internalcategory'] = cat_clean
        
        invalid_count = len(invalid_rows)
        valid_count = total_rows - invalid_count
        
        logger.info(f"  ✅ Valid rows: {valid_count}/{total_rows} ({valid_count/total_rows*100:.1f}%)")
        logger.info(f"  ❌ Invalid rows: {invalid_count}/{total_rows} ({invalid_count/total_rows*100:.1f}%)")
        
        if invalid_count == 0:
            logger.info("  🎉 All categorization values are valid!")
            return df_result
        
        # Show examples of invalid rows
        logger.info(f"\n📋 Examples of invalid categorization:")
        for i, invalid in enumerate(invalid_rows[:5]):
            logger.info(f"  Row {invalid['index']}:")
            logger.info(f"    Type: '{invalid['type']}' (valid: {invalid['type_valid']})")
            logger.info(f"    Category: '{invalid['internal_category']}' (valid: {invalid['cat_valid']})")
            logger.info(f"    Type-Category consistent: {invalid['type_cat_consistent']}")
            logger.info(f"    Subcategory: '{invalid['internal_subcategory']}' (valid: {invalid['subcat_valid']})")
        
        if invalid_count > 5:
            logger.info(f"  ... and {invalid_count - 5} more")
        
        # Get API credentials
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        api_base = os.getenv("AZURE_OPENAI_API_BASE")
        deployment_name =os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        
        if not api_key or not api_base:
            logger.error("❌ Missing Azure OpenAI credentials in .env file")
            logger.error("   Cannot fix categorization automatically")
            return df_result
        
        # Fix invalid rows with LLM (multithreaded)
        logger.info(f"\n🔧 Fixing {invalid_count} rows with LLM (multithreaded)...")
        logger.info(f"   This will make {invalid_count} API calls")
        
        max_workers = min(50, invalid_count)  # Limit concurrent requests
        logger.info(f"   Using {max_workers} concurrent workers")
        
        fixed_count = 0
        failed_count = 0
        
        # Process rows in parallel
        results = self._fix_rows_multithreaded(
            invalid_rows=invalid_rows,
            df_result=df_result,
            api_key=api_key,
            deployment_name=deployment_name,
            api_base=api_base,
            max_workers=max_workers
        )
        
        # Apply results and count successes
        for result in results:
            if result['success']:
                df_result.at[result['index'], 'internaltype'] = result['type']
                df_result.at[result['index'], 'internalcategory'] = result['internal_category']
                df_result.at[result['index'], 'internalsubcategory'] = result['internal_subcategory']
                fixed_count += 1
            else:
                failed_count += 1
        
        # Summary
        logger.info(f"\n{'='*60}")
        logger.info("📊 Categorization Fix Summary")
        logger.info(f"{'='*60}")
        logger.info(f"  Total invalid rows: {invalid_count}")
        logger.info(f"  ✅ Successfully fixed: {fixed_count}")
        logger.info(f"  ❌ Failed to fix: {failed_count}")
        logger.info(f"  📈 Success rate: {fixed_count/invalid_count*100:.1f}%")
        
        return df_result
    
    def _process_single_categorization_fix(self, task_data: dict) -> dict:
        """
        Process a single categorization fix task (thread-safe).
        
        Args:
            task_data: Dictionary with fix task information
            
        Returns:
            dict: Result with success status and corrected values
        """
        idx = task_data['index']
        invalid_info = task_data['invalid_info']
        row_data = task_data['row_data']
        api_key = task_data['api_key']
        deployment_name = task_data['deployment_name']
        api_base = task_data['api_base']
        
        try:
            # Fix with LLM
            corrected = self.fix_single_categorization_with_llm(
                row_data=row_data,
                api_key=api_key,
                deployment_name=deployment_name,
                api_base=api_base
            )
            
            # Validate corrected values
            type_valid, _ = self.validate_categorization_value(corrected['type'], self.valid_types)
            cat_valid, _ = self.validate_categorization_value(corrected['internal_category'], self.valid_categories_all)
            subcat_valid = self.validate_subcategory_for_category(corrected['internal_category'], corrected['internal_subcategory'])
            
            if type_valid and cat_valid and subcat_valid:
                return {
                    'index': idx,
                    'success': True,
                    'type': corrected['type'],
                    'internal_category': corrected['internal_category'],
                    'internal_subcategory': corrected['internal_subcategory']
                }
            else:
                with self.lock:
                    logger.warning(f"     ❌ Row {idx}: Failed to fix (invalid values returned)")
                return {
                    'index': idx,
                    'success': False
                }
                
        except Exception as e:
            with self.lock:
                logger.error(f"     ❌ Row {idx}: Error during fix: {e}")
            return {
                'index': idx,
                'success': False
            }
    
    def _fix_rows_multithreaded(
        self,
        invalid_rows: list,
        df_result: pd.DataFrame,
        api_key: str,
        deployment_name: str,
        api_base: str,
        max_workers: int
    ) -> list:
        """
        Fix invalid rows using multithreading.
        
        Args:
            invalid_rows: List of invalid row information
            df_result: DataFrame to extract row data from
            api_key: Azure OpenAI API key
            deployment_name: Model deployment name
            api_base: Azure OpenAI base URL
            max_workers: Maximum concurrent threads
            
        Returns:
            list: List of results from all fix attempts
        """
        results = []
        
        # Prepare tasks
        tasks = []
        for invalid in invalid_rows:
            idx = invalid['index']
            
            # Get row data
            row_data = {
                'product_name': df_result.at[idx, 'product_name'] if 'product_name' in df_result.columns else None,
                'description': df_result.at[idx, 'description'] if 'description' in df_result.columns else None,
                'brand': df_result.at[idx, 'brand'] if 'brand' in df_result.columns else None,
                'category': df_result.at[idx, 'category'] if 'category' in df_result.columns else None,
                'type': df_result.at[idx, 'internaltype'],
                'internal_category': df_result.at[idx, 'internalcategory'],
                'internal_subcategory': df_result.at[idx, 'internalsubcategory']
            }
            
            tasks.append({
                'index': idx,
                'invalid_info': invalid,
                'row_data': row_data,
                'api_key': api_key,
                'deployment_name': deployment_name,
                'api_base': api_base
            })
        
        # Process tasks in parallel with progress bar
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_task = {
                executor.submit(self._process_single_categorization_fix, task): task
                for task in tasks
            }
            
            # Process completed tasks with progress bar
            with tqdm(total=len(invalid_rows), desc="Fixing categorization", unit="row") as pbar:
                for future in as_completed(future_to_task):
                    result = future.result()
                    results.append(result)
                    
                    # Update progress bar with status
                    status = "✓" if result['success'] else "✗"
                    pbar.set_postfix({
                        'status': status,
                        'row': result['index']
                    })
                    pbar.update(1)
        
        return results

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

    def restore_measure_fields_from_translated(self, df_enriched: pd.DataFrame, df_translated: pd.DataFrame) -> pd.DataFrame:
        """
        Restore measure_value and unit_measure from translated CSV when both fields are complete.
        This prevents LLM completion errors from corrupting these fields.
        
        Logic:
        - If both measure_value AND unit_measure are present in translated -> use translated values
        - If either is missing in translated -> keep enriched values
        - Decisions are made jointly for both fields as they depend on each other
        - Uses product_hash as key to match products between DataFrames
        
        Args:
            df_enriched: Enriched DataFrame (after LLM completion)
            df_translated: Translated DataFrame (before LLM completion)
            
        Returns:
            pd.DataFrame: Updated enriched DataFrame with corrected measure fields
        """
        logger.info(f"\n{'='*60}")
        logger.info("🔧 Restoring Measure Fields from Translated CSV")
        logger.info(f"{'='*60}")
        
        # Validate product_hash column exists in both DataFrames
        if 'product_hash' not in df_enriched.columns:
            logger.error("❌ 'product_hash' column not found in enriched DataFrame")
            return df_enriched
        
        if 'product_hash' not in df_translated.columns:
            logger.error("❌ 'product_hash' column not found in translated DataFrame")
            return df_enriched
        
        # Check if measure columns exist
        measure_cols = ['measure_value', 'unit_measure']
        missing_in_enriched = [col for col in measure_cols if col not in df_enriched.columns]
        missing_in_translated = [col for col in measure_cols if col not in df_translated.columns]
        
        if missing_in_enriched:
            logger.warning(f"⚠️ Missing columns in enriched: {missing_in_enriched}")
            return df_enriched
        
        if missing_in_translated:
            logger.warning(f"⚠️ Missing columns in translated: {missing_in_translated}")
            return df_enriched
        
        # Create a copy for modifications
        df_result = df_enriched.copy()
        
        # Set product_hash as index for faster lookups
        df_translated_indexed = df_translated.set_index('product_hash')
        
        # Track statistics
        restored_count = 0
        kept_enriched_count = 0
        missing_in_translated_count = 0
        
        # Process each row
        for idx, row in df_result.iterrows():
            product_hash = row['product_hash']
            
            # Check if product_hash exists in translated DataFrame
            if pd.isna(product_hash) or product_hash not in df_translated_indexed.index:
                missing_in_translated_count += 1
                continue
            
            # Get translated values
            translated_row = df_translated_indexed.loc[product_hash]
            
            # Handle case where product_hash has duplicates (returns DataFrame/Series)
            if isinstance(translated_row, pd.DataFrame):
                # Multiple rows with same product_hash - take the first one
                translated_row = translated_row.iloc[0]
            
            translated_measure_value = translated_row['measure_value']
            translated_unit_measure = translated_row['unit_measure']
            
            # Check if BOTH fields are present and non-empty in translated
            measure_value_complete = pd.notna(translated_measure_value) and str(translated_measure_value).strip() != ''
            unit_measure_complete = pd.notna(translated_unit_measure) and str(translated_unit_measure).strip() != ''
            
            if measure_value_complete and unit_measure_complete:
                # Both fields are complete in translated -> restore them
                df_result.at[idx, 'measure_value'] = translated_measure_value
                df_result.at[idx, 'unit_measure'] = translated_unit_measure
                restored_count += 1
            else:
                # Either field is missing in translated -> keep enriched values
                kept_enriched_count += 1
        
        # Print summary
        logger.info(f"\n📊 Measure Fields Restoration Summary:")
        logger.info(f"  Total rows: {len(df_result)}")
        logger.info(f"  ✅ Restored from translated: {restored_count}")
        logger.info(f"  📝 Kept enriched values: {kept_enriched_count}")
        logger.info(f"  ⚠️  Missing in translated: {missing_in_translated_count}")
        logger.info(f"{'='*60}")
        
        return df_result

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

    def normalize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize DataFrame - standardizes nutritional units
        Converts all nutritional values to standard units
        """
        # Standard units for each nutritional component
        unit_conversions = {
            'nutrition_information_calories': {
                'standard_unit': 'kcal',
                'conversions': {
                    'kcal': 1,
                    'kj': 0.239006,  # kJ to kcal
                    'cal': 0.001      # cal to kcal
                }
            },
            'nutrition_information_fat': {
                'standard_unit': 'g',
                'conversions': {
                    'g': 1,
                    'mg': 0.001,      # mg to g
                    'kg': 1000        # kg to g
                }
            },
            'nutrition_information_carbohydrates': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            },
            'nutrition_information_fiber': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            },
            'nutrition_information_protein': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            },
            'nutrition_information_salt': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            },
            'nutrition_information_sugars': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            },
            'nutrition_information_saturatedfattyacids': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            },
            'nutrition_information_monounsaturatedfattyacids': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            },
            'nutrition_information_polyunsaturatedgradeacids': {
                'standard_unit': 'g',
                'conversions': {'g': 1, 'mg': 0.001, 'kg': 1000}
            }
        }
        
        # Standardize each nutritional component
        for nutrient, config in unit_conversions.items():
            value_col = f"{nutrient}_value"
            unit_col = f"{nutrient}_unit"
            
            # Skip if columns don't exist
            if value_col not in df.columns or unit_col not in df.columns:
                continue
            
            # Create mask for rows that need conversion
            needs_conversion = df[unit_col].notna() & (df[unit_col] != '')
            
            if not needs_conversion.any():
                continue
            
            # Convert values based on their current unit
            for current_unit, conversion_factor in config['conversions'].items():
                mask = needs_conversion & (df[unit_col].str.lower() == current_unit.lower())
                if mask.any():
                    # Convert string values to float, handling errors
                    values = pd.to_numeric(df.loc[mask, value_col], errors='coerce')
                    df.loc[mask, value_col] = values * conversion_factor
                    df.loc[mask, unit_col] = config['standard_unit']
            
            logger.info(f"Normalized {nutrient} to {config['standard_unit']}")
        
        # Normalize product measure (unit_measure and measure_value columns)
        if 'unit_measure' in df.columns and 'measure_value' in df.columns:
            logger.info("Normalizing product measures (unit_measure, measure_value)...")
            
            # Define conversions for volume (to Liters) and weight (to grams)
            measure_conversions = {
                # Volume units → L (liters)
                'ml': {'target': 'L', 'factor': 0.001},
                'mililitros': {'target': 'L', 'factor': 0.001},
                'cl': {'target': 'L', 'factor': 0.01},
                'centilitros': {'target': 'L', 'factor': 0.01},
                'dl': {'target': 'L', 'factor': 0.1},
                'decilitros': {'target': 'L', 'factor': 0.1},
                'l': {'target': 'L', 'factor': 1},
                'litros': {'target': 'L', 'factor': 1},
                'litre': {'target': 'L', 'factor': 1},
                'litres': {'target': 'L', 'factor': 1},
                
                # Weight units → g (grams)
                'mg': {'target': 'g', 'factor': 0.001},
                'miligramos': {'target': 'g', 'factor': 0.001},
                'g': {'target': 'g', 'factor': 1},
                'gr': {'target': 'g', 'factor': 1},
                'gramos': {'target': 'g', 'factor': 1},
                'kg': {'target': 'g', 'factor': 1000},
                'kilogramos': {'target': 'g', 'factor': 1000},
                'kgs': {'target': 'g', 'factor': 1000},
            }
            
            # Create mask for rows that need conversion
            needs_conversion = df['unit_measure'].notna() & (df['unit_measure'] != '')
            
            if needs_conversion.any():
                conversions_made = 0
                
                for unit_str, conversion in measure_conversions.items():
                    # Match unit case-insensitively
                    mask = needs_conversion & (df['unit_measure'].str.lower().str.strip() == unit_str.lower())
                    
                    if mask.any():
                        # Convert values
                        values = pd.to_numeric(df.loc[mask, 'measure_value'], errors='coerce')
                        df.loc[mask, 'measure_value'] = values * conversion['factor']
                        df.loc[mask, 'unit_measure'] = conversion['target']
                        conversions_made += mask.sum()
                
                if conversions_made > 0:
                    logger.info(f"Normalized {conversions_made} product measures to standard units (L/g)")
                else:
                    logger.info("No product measure conversions needed")
            else:
                logger.info("No product measures to normalize")
        
        return df

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
            
            # Normalize all decimal separators (comma to dot) for ALL columns that could be numeric
            logger.info(f"\n🔢 Normalizing decimal separators (comma → dot) for all columns...")
            normalized_count = 0
            for col in df_enriched.columns:
                # Try to detect if column contains numeric values by checking a sample
                try:
                    sample = df_enriched[col].dropna().head(10)
                    if len(sample) > 0:
                        # Check if any value contains comma or can be converted to numeric
                        has_comma = sample.astype(str).str.contains(',', na=False).any()
                        # Try converting sample to numeric (ignore errors)
                        can_be_numeric = pd.to_numeric(sample.astype(str).str.replace(',', '.'), errors='coerce').notna().any()
                        
                        if has_comma or can_be_numeric:
                            # Replace comma with dot for decimal normalization
                            df_enriched[col] = df_enriched[col].apply(
                                lambda x: str(x).replace(',', '.') if isinstance(x, str) and ',' in str(x) else x
                            )
                            normalized_count += 1
                except Exception:
                    # Skip columns that can't be processed
                    pass
            logger.info(f"  ✅ Normalized {normalized_count} columns with numeric content")
            
            # Remove completely empty rows
            df_enriched = df_enriched.dropna(how='all')
            
            # Remove duplicate rows
            original_rows = len(df_enriched)
            df_enriched = df_enriched.drop_duplicates()
            duplicates_removed = original_rows - len(df_enriched)
            

            if duplicates_removed > 0:
                logger.info(f"  ✅ Removed {duplicates_removed} duplicate rows")

            # PRIORITY 1: Restore measure_value and unit_measure from translated (when both are complete)
            # This prevents LLM completion errors from corrupting these critical fields
            df_result = self.restore_measure_fields_from_translated(df_enriched, df_translated)

            # PRIORITY 2: Compare and fill missing values from translated for other columns
            df_result = self.compare_and_fill(df_result, df_translated)

            # --- VALIDACIÓN Y CORRECCIÓN DE CATEGORIZACIÓN ---
            df_result = self.validate_and_fix_categorization(df_result)

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
                    # Ensure postcode is treated as string and preserve leading zeros
                    postcode_str = str(postcode).zfill(5) if str(postcode).isdigit() else str(postcode)
                    df_result['postcode'] = postcode_str
                    logger.info(f"  ✅ Updated 'postcode' column with: {postcode_str}")
                else:
                    logger.warning(f"  ⚠️  'postcode' column not found in DataFrame")

            # --- CALCULAR price_without_vat SI FALTA ---
            # Normalize nutritional/product measure units first (LLM may have changed units)
            try:
                logger.info("\n🔁 Re-normalizing nutritional and measure units (post-LLM)...")
                df_result = self.normalize_dataframe(df_result)
            except Exception as e:
                logger.warning(f"Could not normalize units in CSVFixer: {e}")

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

            # Reemplazar ',' por '/' en la columna 'components'
            if 'components' in df_result.columns:
                df_result['components'] = df_result['components'].apply(
                    lambda x: x.replace(',', '/') if isinstance(x, str) else x
                )

            # --- LOWERCASE VALUES FOR STRING COLUMNS (EXCEPT IDENTIFIERS/CODES/NUMERIC FIELDS) ---
            # Define columns that should NOT be lowercased because they contain codes, numeric or canonical values
            exclude_lowercase = {
                'id', 'uuid', 'ean', 'gtin', 'asin', 'siid', 'url', 'price', 'price_without_vat',
                'price_with_offer', 'currency', 'vat', 'unit_price', 'postcode', 'image_list'
            }

            # Apply lowercase to object-type columns except the excluded ones
            for col in df_result.select_dtypes(include=['object']).columns:
                if col in exclude_lowercase:
                    logger.debug(f"Skipping lowercase for column: {col}")
                    continue
                try:
                    df_result[col] = df_result[col].apply(lambda x: x.lower() if isinstance(x, str) else x)
                except Exception:
                    logger.debug(f"Could not lowercase values for column: {col}")
            
            # Ensure categorization columns are also lowercase at the very end
            categorization_cols = ['internaltype', 'internalcategory', 'internalsubcategory', 'product_type']
            for col in categorization_cols:
                if col in df_result.columns:
                    try:
                        df_result[col] = df_result[col].apply(lambda x: x.lower() if isinstance(x, str) else x)
                        logger.debug(f"Applied lowercase to categorization column: {col}")
                    except Exception:
                        logger.debug(f"Could not lowercase values for categorization column: {col}")

            # Create output directory if it doesn't exist
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)

            # Save fixed CSV
            logger.info(f"\n💾 Saving fixed CSV...")
            # Ensure all column names are lowercase before saving final fixed CSV
            try:
                df_result.columns = [str(col).lower() for col in df_result.columns]
            except Exception:
                logger.warning("Could not lowercase column names; continuing with original column names")

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
