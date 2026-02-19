import os
import pandas as pd
import boto3
import time
import re
import sys
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from langdetect import detect, detect_langs, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm
from dataclasses import dataclass
from openai import AzureOpenAI

from data.schemas.taxonomy import ColumnRegistry, create_default_registry
from config.settings import PROJECT_ROOT

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from config.settings import AWS_TRANSLATE_CONFIG
except ImportError:
    AWS_TRANSLATE_CONFIG = None

# Set seed for consistent language detection
DetectorFactory.seed = 0

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class TranslationResult:
    """Data class to store translation results."""
    original_text: str
    translated_text: str
    source_lang: str
    success: bool
    error: Optional[str] = None
    duration: float = 0.0


class Translator:
    """Base class for translation functionality."""
    
    def __init__(self):
        """Initialize base translator attributes."""
        # Thread lock for thread-safe operations
        self.lock = Lock()
        
        # Get non-translatable columns from the registry
        registry_path = PROJECT_ROOT / "data" / "schemas" / "column_registry.json"
        self.column_registry = ColumnRegistry.load_from_json(registry_path)
        
        # Patterns to identify non-translatable content
        self.non_translatable_patterns = [
            r'^[A-Z0-9\-_]{1,3}$',  # Short codes only (1-3 chars): IDs, currency codes, country codes
            r'^[0-9A-Z\-_]*[0-9][A-Z0-9\-_]*$',  # Codes with at least one number
            r'^\d+(\.\d+)?$',   # Pure numbers
            r'^https?://',       # URLs
            r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',  # Emails
            r'^\d{10,}$',       # Long numeric codes (EAN, etc.)
        ]
        
        # AWS language code mapping (some differ from standard ISO codes)
        self.aws_language_map = {
            'zh': 'zh',     # Chinese (AWS supports both zh and zh-TW)
            'zh-cn': 'zh',  # Simplified Chinese
            'zh-tw': 'zh-TW',  # Traditional Chinese
            'pt': 'pt',     # Portuguese
            'pt-br': 'pt',  # Brazilian Portuguese (AWS uses pt)
            'ca': 'ca',     # Catalan
            'da': 'da',     # Danish
            'nl': 'nl',     # Dutch
            'fi': 'fi',     # Finnish
            'fr': 'fr',     # French
            'de': 'de',     # German
            'he': 'he',     # Hebrew
            'hi': 'hi',     # Hindi
            'id': 'id',     # Indonesian
            'it': 'it',     # Italian
            'ja': 'ja',     # Japanese
            'ko': 'ko',     # Korean
            'ms': 'ms',     # Malay
            'no': 'no',     # Norwegian
            'fa': 'fa',     # Persian
            'pl': 'pl',     # Polish
            'ru': 'ru',     # Russian
            'es': 'es',     # Spanish
            'sv': 'sv',     # Swedish
            'th': 'th',     # Thai
            'tr': 'tr',     # Turkish
            'uk': 'uk',     # Ukrainian
        }
        
        # Will be set by child classes
        self.use_llm = False
        self.predetermined_languages = None
    
    def is_translatable_text(self, text: str) -> bool:
        """
        Determine if a text string should be translated based on content analysis.
        
        Args:
            text: The text to analyze
            
        Returns:
            bool: True if the text should be translated, False otherwise
        """
        # 1. Check for null or non-string
        if pd.isna(text) or not isinstance(text, str):
            return False
        
        text = str(text).strip()
        
        # 2. Skip empty or very short strings
        if len(text) < 4:
            return False
        
        # 3. Check against non-translatable patterns
        for pattern in self.non_translatable_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return False
        
        return True
    
    def detect_language(self, text: str) -> str:
        """
        Detect the language of a text string.
        If predetermined languages are set, limit detection to those languages.
        
        Args:
            text: The text to analyze
            
        Returns:
            str: Language code (e.g., 'es', 'en', 'fr') or 'unknown'
        """
        try:
            # Clean text for language detection
            clean_text = re.sub(r'[^\w\s]', ' ', str(text))
            clean_text = ' '.join(clean_text.split())
            
            # For very short texts, try detection anyway but be less strict
            if len(clean_text) < 4:
                return 'unknown'
            
            if self.predetermined_languages:
                try:
                    # Get probability scores for all languages
                    lang_probs = detect_langs(clean_text)
                    # Filter to only predetermined languages and find the best match
                    valid_detections = [lp for lp in lang_probs if lp.lang in self.predetermined_languages]
                    if valid_detections:
                        # Return the most probable predetermined language
                        best_match = max(valid_detections, key=lambda x: x.prob)
                        logger.debug(f"Language detection: '{text}' -> {best_match.lang} (from predetermined: {self.predetermined_languages})")
                        return best_match.lang
                    else:
                        # None of the predetermined languages match well enough
                        logger.debug(f"Language detection: '{text}' -> (not in predetermined list: {self.predetermined_languages})")
                        return 'unknown'
                except Exception:
                    # Fallback: if we can't get probabilities, return unknown
                    return 'unknown'

            else:
                detected_lang = detect(clean_text)
                logger.debug(f"Language detection: '{text}' -> {detected_lang}")
                return detected_lang
                
        except (LangDetectException, Exception):
            return 'unknown'
    
    def should_translate_column(self, series: pd.Series) -> Tuple[bool, str]:
        """
        Determine if a column should be translated based on its content.
        
        Args:
            series: Pandas series (column) to analyze
            
        Returns:
            Tuple[bool, str]: (should_translate, reason)
        """
        # 1. Check if column contains strings
        if series.dtype != 'object':
            return False, "Column is not of string type"
        
        # 2. Sample non-null values for analysis
        non_null_values = series.dropna()
        if len(non_null_values) == 0:
            return False, "Column contains no non-null values"
        
        # 3. Take a sample for analysis (max 50 values)
        sample_size = min(50, len(non_null_values))
        sample = non_null_values.sample(n=sample_size, random_state=42)
        
        # 4. Check if values are translatable text
        translatable_count = sum(1 for val in sample if self.is_translatable_text(val))
        translatable_ratio = translatable_count / len(sample)
        
        if translatable_ratio < 0.1:  # Less than 10% translatable content
            return False, f"Only {translatable_ratio:.2%} of values appear to be translatable text"
        
        # 5. Detect languages in the sample
        languages = []
        for val in sample:
            if self.is_translatable_text(val):
                lang = self.detect_language(val)
                languages.append(lang)  # Include 'unknown' languages now
        
        if not languages:
            return False, "No translatable text found"
        
        # 6. Check if most content is already in English
        english_ratio = sum(1 for lang in languages if lang == 'en') / len(languages)
        
        if english_ratio > 0.9:  # More than 90% already in English
            return False, f"Column appears to be mostly in English ({english_ratio:.2%})"
        
        # 7. If there's any non-English content (including unknown), attempt translation
        non_english_langs = [lang for lang in languages if lang != 'en']
        if non_english_langs:
            # Count known vs unknown languages
            known_langs = [lang for lang in non_english_langs if lang != 'unknown']
            unknown_count = sum(1 for lang in non_english_langs if lang == 'unknown')
            
            if known_langs:
                most_common_lang = max(set(known_langs), key=known_langs.count)
                if unknown_count > 0:
                    return True, f"Column contains translatable text, primarily in '{most_common_lang}' with {unknown_count} unknown language texts"
                else:
                    return True, f"Column contains translatable text, primarily in '{most_common_lang}'"
            elif unknown_count > 0:
                return True, f"Column contains {unknown_count} texts with unknown language - will attempt translation"
        
        return False, "Column analysis inconclusive"
    
    def map_language_code(self, lang_code: str) -> str:
        """
        Map detected language code to AWS Translate supported language code.
        
        Args:
            lang_code: Language code from language detection
            
        Returns:
            str: AWS Translate compatible language code, or original code if not in mapping
        """
        if not lang_code or lang_code == 'unknown':
            return lang_code
        
        mapped_code = self.aws_language_map.get(lang_code.lower(), lang_code)
        return mapped_code
    
    def _translate_single_text_with_metadata(self, text_data: Tuple[int, str, Optional[str]]) -> TranslationResult:
        """
        Translate a single text and return metadata.
        Thread-safe method for use in multithreading.
        Supports both AWS Translate and LLM translation.
        
        Args:
            text_data: Tuple of (index, text, source_lang)
            
        Returns:
            TranslationResult: Result object with translation metadata
        """
        index, text, source_lang = text_data
        start_time = time.time()
        
        try:
            # Auto-detect source language if not provided
            if not source_lang:
                detected_lang = self.detect_language(text)
                if detected_lang == 'en':
                    # If it's already English, skip translation
                    duration = time.time() - start_time
                    return TranslationResult(
                        original_text=text,
                        translated_text=text,
                        source_lang='en',
                        success=True,
                        duration=duration
                    )
                source_lang = detected_lang
            
            # Use LLM or AWS Translate based on configuration
            if self.use_llm:
                # LLM Translation
                translated_text = self.translate_text_with_llm(text, source_lang, target_lang='en')
                detected_source_lang = source_lang
            else:
                # AWS Translate
                translate_params = {
                    'Text': text,
                    'TargetLanguageCode': 'en'
                }
                
                if source_lang and source_lang != 'unknown':
                    aws_source_lang = self.map_language_code(source_lang)
                    if aws_source_lang in self.aws_language_map.values() or aws_source_lang == source_lang:
                        translate_params['SourceLanguageCode'] = aws_source_lang
                    else:
                        translate_params['SourceLanguageCode'] = 'auto'
                else:
                    translate_params['SourceLanguageCode'] = 'auto'
                
                response = self.translate_client.translate_text(**translate_params)
                translated_text = response['TranslatedText']
                detected_source_lang = response.get('SourceLanguageCode', 'unknown')
            
            duration = time.time() - start_time
            
            with self.lock:
                logger.debug(f"Translation {index}: '{text}' ({detected_source_lang}) -> '{translated_text}'")
            
            return TranslationResult(
                original_text=text,
                translated_text=translated_text,
                source_lang=detected_source_lang,
                success=True,
                duration=duration
            )
            
        except Exception as e:
            duration = time.time() - start_time
            error_message = str(e)
            
            with self.lock:
                if 'UnsupportedLanguagePairException' in error_message:
                    logger.warning(f"Unsupported language pair for: '{text}', keeping original")
                elif 'DetectedLanguageInvalidException' in error_message:
                    logger.warning(f"Invalid detected language for: '{text}', keeping original")
                else:
                    logger.error(f"AWS Translate error for '{text}': {e}")
            
            return TranslationResult(
                original_text=text,
                translated_text=text,  # Keep original on error
                source_lang='unknown',
                success=False,
                error=error_message,
                duration=duration
            )

    def translate_text_batch_multithreaded(
        self, 
        texts: List[str], 
        source_lang: str = None,
        max_workers: int = 100,
        show_progress: bool = True
    ) -> List[str]:
        """
        Translate a batch of texts using multithreading.
        
        Args:
            texts: List of texts to translate
            source_lang: Source language code (if None, auto-detect)
            max_workers: Maximum number of concurrent threads
            show_progress: Whether to show progress bar
            
        Returns:
            List[str]: Translated texts in original order
        """
        if not texts:
            return []
        
        # Prepare data for multithreading: (index, text, source_lang)
        text_data = [(i, text, source_lang) for i, text in enumerate(texts)]
        results = [None] * len(texts)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_index = {
                executor.submit(self._translate_single_text_with_metadata, data): data[0]
                for data in text_data
            }
            
            # Process completed tasks with optional progress bar
            if show_progress:
                with tqdm(total=len(texts), desc="Translating", unit="text") as pbar:
                    for future in as_completed(future_to_index):
                        result = future.result()
                        index = future_to_index[future]
                        results[index] = result.translated_text
                        
                        status = "✓" if result.success else "✗"
                        pbar.set_postfix({
                            'status': status,
                            'time': f"{result.duration:.2f}s"
                        })
                        pbar.update(1)
            else:
                for future in as_completed(future_to_index):
                    result = future.result()
                    index = future_to_index[future]
                    results[index] = result.translated_text
        
        return results

    def translate_column(self, series: pd.Series, source_lang: str = None, batch_size: int = 25, use_multithreading: bool = True, max_workers: int = 5) -> pd.Series:
        """
        Translate a pandas series (column) with optional multithreading.
        
        Args:
            series: The series to translate
            source_lang: Source language code (if None, auto-detect)
            batch_size: Number of texts to process in each batch (for progress tracking)
            use_multithreading: Whether to use multithreaded translation
            max_workers: Maximum number of concurrent threads
            
        Returns:
            pd.Series: Translated series
        """
        translated_series = series.copy()
        
        # Get unique non-null values that should be translated
        unique_values = series.dropna().unique()
        translatable_values = [val for val in unique_values if self.is_translatable_text(val)]
        
        if not translatable_values:
            logger.info("No translatable values found in column")
            return translated_series
        
        logger.info(f"Translating {len(translatable_values)} unique values...")
        logger.info(f"Translation mode: {'MULTITHREADED' if use_multithreading else 'SEQUENTIAL'}")
        if use_multithreading:
            logger.info(f"Max workers: {max_workers}")
        
        # Create translation mapping
        translation_map = {}
        
        if use_multithreading:
            # MULTITHREADED TRANSLATION
            # Process all values at once with multithreading
            logger.info(f"Starting multithreaded translation of {len(translatable_values)} values...")
            translated_batch = self.translate_text_batch_multithreaded(
                translatable_values, 
                source_lang=source_lang,
                max_workers=max_workers,
                show_progress=True
            )
            
            # Update translation map
            for original, translated in zip(translatable_values, translated_batch):
                translation_map[original] = translated
                if str(original) != str(translated):
                    logger.debug(f"Translation mapping: '{original}' -> '{translated}'")
        else:
            # SEQUENTIAL TRANSLATION (Original behavior)
            # Process in batches for progress tracking
            for i in range(0, len(translatable_values), batch_size):
                batch = translatable_values[i:i + batch_size]
                logger.info(f"Translating batch {i//batch_size + 1}/{(len(translatable_values) + batch_size - 1)//batch_size}")
                
                # Translate batch
                translated_batch = self.translate_text_batch(batch, source_lang=source_lang)
                
                # Update translation map
                for original, translated in zip(batch, translated_batch):
                    translation_map[original] = translated
                    if str(original) != str(translated):
                        logger.debug(f"Translation mapping: '{original}' -> '{translated}'")
        
        # Apply translations to the series
        translated_series = series.map(lambda x: translation_map.get(x, x))
        
        return translated_series
    
    def translate_csv(
        self, 
        input_path: str, 
        output_path: str, 
        columns_with_language: Dict[str, str] = None, 
        predetermined_languages: List[str] = None,
        use_multithreading: bool = True,
        max_workers: int = 1000
    ) -> Dict[str, Any]:
        """
        Translate specified columns in a CSV file with optional multithreading.
        
        Args:
            input_path: Path to input CSV file
            output_path: Path to output CSV file
            columns_with_language: Dictionary mapping column names to source language codes
            columns_to_avoid: List of column names to avoid translating
            predetermined_languages: List of language codes to limit detection to (overrides instance setting)
            use_multithreading: Whether to use multithreaded translation (default: True)
            max_workers: Maximum number of concurrent threads (default: 5)
            
        Returns:
            Dict with translation results
        """
        logger.info(f"Translation mode: {'MULTITHREADED' if use_multithreading else 'SEQUENTIAL'}")
        if use_multithreading:
            logger.info(f"Max workers: {max_workers}")
        # Temporarily override predetermined languages if provided
        original_predetermined_languages = self.predetermined_languages
        if predetermined_languages is not None:
            self.predetermined_languages = predetermined_languages
            logger.info(f"Using predetermined languages for this translation: {predetermined_languages}")
        
        # Get non-translatable columns from the registry
        columns_to_avoid = [col.name for col in self.column_registry.get_non_translatable_columns()]

        try:
            # Read the CSV
            df = pd.read_csv(input_path, sep=";", encoding='utf-8')

            analysis_results = {
                'columns_to_translate': [],
                'columns_not_to_translate': []
            }

            # Analyze CSV first
            for col_name in df.columns:
                if columns_to_avoid and col_name in columns_to_avoid:
                    logger.info(f"⚠️  Column '{col_name}' is in the avoid list, skipping translation")
                    continue
                else:
                    should_translate, reason = self.should_translate_column(df[col_name])
                    
                    column_info = {
                        'name': col_name,
                        'reason': reason,
                        'dtype': str(df[col_name].dtype),
                        'non_null_count': df[col_name].count(),
                        'sample_values': df[col_name].dropna().head(3).tolist()
                    }
                    
                    if should_translate:
                        analysis_results['columns_to_translate'].append(column_info)
                        logger.info(f"✓ Column '{col_name}' will be translated: {reason}")
                    else:
                        analysis_results['columns_not_to_translate'].append(column_info)
                        logger.info(f"✗ Column '{col_name}' will NOT be translated: {reason}")
            
            # Determine which columns to translate
            columns_to_translate = [col['name'] for col in analysis_results['columns_to_translate']]
            
            if not columns_to_translate:
                logger.warning("No columns identified for translation")
                return {'translated_columns': [], 'total_translations': 0}
            
            logger.info(f"Starting translation of columns: {columns_to_translate}")
            
            translation_results = {
                'translated_columns': [],
                'total_translations': 0
            }
            
            # Translate each column
            for col_name in columns_to_translate:
                if col_name in df.columns:
                    logger.info(f"Translating column: {col_name}")
                    original_series = df[col_name].copy()
                    if columns_with_language and col_name in columns_with_language.keys():
                        translated_series = self.translate_column(
                            original_series, 
                            source_lang=columns_with_language[col_name],
                            use_multithreading=use_multithreading,
                            max_workers=max_workers
                        )
                    else:
                        translated_series = self.translate_column(
                            original_series,
                            use_multithreading=use_multithreading,
                            max_workers=max_workers
                        )
                    
                    # Count actual translations (where text changed)
                    translations_count = sum(1 for orig, trans in zip(original_series, translated_series) 
                                           if pd.notna(orig) and pd.notna(trans) and str(orig) != str(trans))
                    
                    df[col_name] = translated_series
                    
                    translation_results['translated_columns'].append({
                        'column': col_name,
                        'translations_made': translations_count
                    })
                    translation_results['total_translations'] += translations_count
                    
                    logger.info(f"Completed translation of '{col_name}': {translations_count} values translated")
                else:
                    logger.warning(f"Column '{col_name}' not found in CSV")
            
            # Save translated CSV
            df.to_csv(output_path, sep=";", index=False, encoding='utf-8')
            logger.info(f"Translated CSV saved to: {output_path}")
            
            return translation_results
            
        finally:
            # Restore original predetermined languages setting
            self.predetermined_languages = original_predetermined_languages




class TranslatorOpenAI(Translator):
    """Translator that uses OpenAI LLM for translation."""

    def __init__(self, llm_api_key: str = None, llm_base_url: str = None, llm_model: str = "gpt-5-nano", predetermined_languages: List[str] = None):
        """
        Initialize OpenAI-based translator.
        
        Args:
            llm_api_key: API key for LLM (required)
            llm_base_url: Base URL for LLM API (required)
            llm_model: Model name for LLM (default: gpt-5-nano)
            predetermined_languages: List of language codes to limit detection to (e.g., ['es', 'en'])
        """
        # Initialize base class
        super().__init__()
        
        # Set LLM mode flag
        self.use_llm = True
        
        # Store predetermined languages
        self.predetermined_languages = predetermined_languages
        if self.predetermined_languages:
            logger.info(f"Language detection limited to: {self.predetermined_languages}")
        
        # Store LLM configuration
        self.llm_model = llm_model
        llm_api_key = llm_api_key or os.getenv("AZURE_OPENAI_API_KEY")
        llm_base_url = llm_base_url or os.getenv("AZURE_OPENAI_API_BASE")
        
        if not llm_api_key:
            raise ValueError("LLM API key is required. Set AZURE_OPENAI_API_KEY environment variable or pass llm_api_key parameter.")
        if not llm_base_url:
            raise ValueError("LLM base URL is required. Set AZURE_OPENAI_API_BASE environment variable or pass llm_base_url parameter.")
        
        self.llm_client = AzureOpenAI(
            base_url=llm_base_url,
            api_key=llm_api_key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION")
        )
        logger.info(f"✅ LLM client initialized (model: {self.llm_model})")
        logger.info(f"   Base URL: {llm_base_url}")
        
        # Set translate_client to None (not used in LLM mode)
        self.translate_client = None

    def translate_text_with_llm(self, text: str, source_lang: str = None, target_lang: str = "en") -> str:
        """
        Translate text using LLM API (similar to csv_completion approach).
        
        Args:
            text: Text to translate
            source_lang: Source language code (optional, for context)
            target_lang: Target language code (default: 'en')
            
        Returns:
            str: Translated text
        """
        if not self.llm_client:
            raise ValueError("LLM client not initialized. Set use_llm=True in constructor.")
        
        # Create translation prompt
        lang_context = f" from {source_lang.upper()}" if source_lang and source_lang != 'unknown' else ""
        
        system_prompt = f"""You are a professional translator. Translate the given text{lang_context} to english.
        
        Rules:
        1. Preserve product names, brands, and technical terms when appropriate
        2. Maintain the original formatting and structure
        3. If the text is already in {target_lang.upper()}, return it unchanged
        4. Return ONLY the translated text, no explanations or additional text
        5. If the text contains codes, IDs, or non-translatable content, keep them as-is
        """
        
        user_prompt = f"Text to translate: {text}"
        
        try:
            response = self.llm_client.responses.create(
                model=self.llm_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                reasoning={"effort": "low"},
                text={"verbosity": "low"}
            )
            
            # Extract translated text from response
            translated_text = response.output[1].content[0].text.strip()
            
            # Remove quotes if the LLM wrapped the translation in them
            if translated_text.startswith('"') and translated_text.endswith('"'):
                translated_text = translated_text[1:-1]
            if translated_text.startswith("'") and translated_text.endswith("'"):
                translated_text = translated_text[1:-1]
            
            return translated_text
            
        except Exception as e:
            logger.error(f"LLM translation error for '{text}': {e}")
            return text  # Return original text on error



class TranslatorAWS(Translator):
    """
    A class to intelligently translate CSV columns from various languages to English using Amazon Translate.
    Only translates columns that contain natural language text that is not already in English.
    """

    def __init__(self, aws_access_key_id: str = None, aws_secret_access_key: str = None, region_name: str = None, predetermined_languages: List[str] = None):
        """
        Initialize the translator with AWS credentials.
        
        Args:
            aws_access_key_id: AWS Access Key ID
            aws_secret_access_key: AWS Secret Access Key
            region_name: AWS region (e.g., 'us-east-1', 'eu-west-1')
            predetermined_languages: List of language codes to limit detection to (e.g., ['es', 'en'])
        """
        # Initialize base class
        super().__init__()
        
        # Set AWS mode flag (not using LLM)
        self.use_llm = False
        
        # Get credentials from environment or parameters
        self.aws_access_key_id = aws_access_key_id or os.getenv("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = aws_secret_access_key or os.getenv("AWS_SECRET_ACCESS_KEY")
        self.region_name = region_name or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        
        # Store predetermined languages for later validation
        self.predetermined_languages = predetermined_languages
        
        # Validate and set predetermined languages after AWS language map is defined
        if self.predetermined_languages:
            # Validate that predetermined languages are supported
            supported_languages = set(self.aws_language_map.keys()) | set(self.aws_language_map.values()) | {'en'}
            invalid_languages = [lang for lang in self.predetermined_languages if lang not in supported_languages]
            if invalid_languages:
                logger.warning(f"Some predetermined languages may not be supported by AWS Translate: {invalid_languages}")
            logger.info(f"Language detection limited to: {self.predetermined_languages}")
        
        # Validate required credentials
        if not self.aws_access_key_id:
            raise ValueError("AWS Access Key ID is required. Set AWS_ACCESS_KEY_ID environment variable or pass aws_access_key_id parameter.")
        if not self.aws_secret_access_key:
            raise ValueError("AWS Secret Access Key is required. Set AWS_SECRET_ACCESS_KEY environment variable or pass aws_secret_access_key parameter.")
        
        # Initialize AWS Translate client
        try:
            self.translate_client = boto3.client(
                'translate',
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.region_name
            )
            
            # Test the connection
            self.translate_client.describe_text_translation_job(JobId='test-connection-check')
        except Exception as e:
            if 'ResourceNotFoundException' in str(e):
                # This is expected when testing connection with a fake job ID
                logger.info("AWS Translate client initialized successfully")
            else:
                raise ValueError(f"Failed to initialize AWS Translate client: {e}")
        except Exception as e:
            raise ValueError(f"Failed to initialize AWS Translate client: {e}")

    def translate_text_batch(self, texts: List[str], source_lang: str = None) -> List[str]:
        """
        Determine if a text string should be translated based on content analysis.
        
        Args:
            text: The text to analyze
            
        Returns:
            bool: True if the text should be translated, False otherwise
        """
        # 1. Check for null or non-string
        if pd.isna(text) or not isinstance(text, str):
            return False
        
        text = str(text).strip()
        
        # 2. Skip empty or very short strings
        if len(text) < 4:
            return False
        
        # 3. Check against non-translatable patterns
        for pattern in self.non_translatable_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return False
        
        return True
    
    def translate_text_batch(self, texts: List[str], source_lang: str = None) -> List[str]:
        """
        Translate a batch of texts using AWS Translate.
        Note: AWS Translate doesn't support batch translation, so we translate one by one.
        
        Args:
            texts: List of texts to translate
            source_lang: Source language code (if None, auto-detect)
            
        Returns:
            List[str]: Translated texts
        """
        if not texts:
            return []
        
        translated_texts = []
        
        for i, text in enumerate(texts):
            try:
                # Prepare translation parameters
                translate_params = {
                    'Text': text,
                    'TargetLanguageCode': 'en'
                }
                
                # If source language is specified, use it
                if source_lang:
                    aws_source_lang = self.map_language_code(source_lang)
                    translate_params['SourceLanguageCode'] = aws_source_lang
                else:
                    # Auto-detect source language
                    detected_lang = self.detect_language(text)
                    if detected_lang == 'en':
                        # If it's already English, skip translation
                        translated_texts.append(text)
                        logger.info(f"DEBUG - Skipping translation for: '{text}' (language: {detected_lang})")
                        continue
                    elif detected_lang != 'unknown':
                        # Use detected language if it's known and supported
                        aws_source_lang = self.map_language_code(detected_lang)
                        if aws_source_lang in self.aws_language_map.values() or aws_source_lang == detected_lang:
                            translate_params['SourceLanguageCode'] = aws_source_lang
                        else:
                            # If not in map, use AWS auto-detect
                            translate_params['SourceLanguageCode'] = 'auto'
                    else:
                        # For 'unknown' language, use AWS Translate auto-detect
                        translate_params['SourceLanguageCode'] = 'auto'
                
                logger.info(f"DEBUG - AWS Translate request: {translate_params}")
                
                # Call AWS Translate
                response = self.translate_client.translate_text(**translate_params)
                
                translated_text = response['TranslatedText']
                detected_source_lang = response.get('SourceLanguageCode', 'unknown')
                
                logger.info(f"DEBUG - Translation {i+1}: '{text}' (detected: {detected_source_lang}) -> '{translated_text}'")
                translated_texts.append(translated_text)
                
                # Add small delay to respect rate limits (AWS Translate allows 100 TPS by default)
                time.sleep(0.01)  # 10ms delay
                
            except Exception as e:
                error_message = str(e)
                if 'UnsupportedLanguagePairException' in error_message:
                    logger.warning(f"DEBUG - Unsupported language pair for: '{text}', keeping original")
                    translated_texts.append(text)
                elif 'DetectedLanguageInvalidException' in error_message:
                    logger.warning(f"DEBUG - Invalid detected language for: '{text}', keeping original")
                    translated_texts.append(text)
                else:
                    logger.error(f"AWS Translate error for '{text}': {e}")
                    translated_texts.append(text)
        
        return translated_texts

    
