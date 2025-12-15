"""
Stopwords and utilities for cleaning the allergens column in product data.
"""

import pandas as pd
from pathlib import Path


# Path to persistent allergens file
_ALLERGENS_FILE = Path(__file__).parent / 'known_allergens.txt'

ALLERGEN_STOPWORDS = {
    'contains', 'may', 'contain', 'traces', 'of', 'from', 'and', 'or',
    'including', 'such', 'as', 'with', 'in', 'the', 'a', 'an', 'is',
    'are', 'made', 'produced', 'manufactured', 'source', 'derived',
    'possible', 'potential', 'cross', 'contamination', 'contact',
    'processed', 'facility', 'that', 'also', 'processes', 'handles',
    'see', 'below', 'above', 'ingredients', 'list', 'for', 'more',
    'information', 'details', 'has', 'have', 'been', 'this', 'product',
    'following', 'present', 'used', 'during', 'preparation'
}


def _initialize_allergens_file():
    """Creates the allergens file with default values if it doesn't exist."""
    if not _ALLERGENS_FILE.exists():
        default_allergens = [
            'milk', 'egg', 'eggs', 'fish', 'shellfish', 'crustacean', 'crustaceans',
            'peanut', 'peanuts', 'tree nuts', 'almond', 'almonds', 'walnut', 'walnuts',
            'cashew', 'cashews', 'pecan', 'pecans', 'pistachio', 'pistachios',
            'hazelnut', 'hazelnuts', 'macadamia', 'brazil nut', 'brazil nuts',
            'soy', 'soya', 'wheat', 'gluten', 'sesame', 'celery', 'mustard',
            'lupin', 'molluscs', 'mollusc', 'sulfite', 'sulfites', 'sulphite', 'sulphites',
            'phenylalanine', 'lactose', 'casein', 'whey', 'dairy',
            'pork', 'beef', 'chicken', 'shrimp', 'crab', 'lobster', 'oyster',
            'clam', 'squid', 'octopus', 'barley', 'rye', 'oats'
        ]
        with open(_ALLERGENS_FILE, 'w', encoding='utf-8') as f:
            for allergen in sorted(default_allergens):
                f.write(f"{allergen}\n")


def _load_known_allergens():
    """Loads known allergens from file."""
    _initialize_allergens_file()
    
    try:
        with open(_ALLERGENS_FILE, 'r', encoding='utf-8') as f:
            allergens = {line.strip().lower() for line in f if line.strip()}
        return allergens
    except Exception:
        return set()


def _save_known_allergens():
    """Saves current KNOWN_ALLERGENS to file."""
    try:
        with open(_ALLERGENS_FILE, 'w', encoding='utf-8') as f:
            for allergen in sorted(KNOWN_ALLERGENS):
                f.write(f"{allergen}\n")
    except Exception:
        pass


# Load allergens from file on module import
KNOWN_ALLERGENS = _load_known_allergens()


def clean_allergen_text(text, keep_unknown=True, min_word_length=2):
    """
    Cleans allergen text by removing stopwords and keeping only ingredient names.
    
    Args:
        text: String with allergen information
        keep_unknown: If True, keeps words not in KNOWN_ALLERGENS
        min_word_length: Minimum word length to keep
        
    Returns:
        String with allergen names separated by commas
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ''
    
    text = str(text)
    if not text or text.strip() == '':
        return ''
    
    text_lower = text.lower()
    separators = [',', ';', '/', '|', ' and ', ' or ']
    parts = [text_lower]
    
    for sep in separators:
        new_parts = []
        for part in parts:
            new_parts.extend(part.split(sep))
        parts = new_parts
    
    cleaned_parts = []
    for part in parts:
        part = part.strip().strip('.,;:()[]{}')
        if not part:
            continue
        
        words = part.split()
        filtered_words = []
        for word in words:
            word_clean = word.strip('.,;:()[]{}').lower()  # Convertir a minúsculas antes de comparar
            if word_clean and word_clean not in ALLERGEN_STOPWORDS:
                filtered_words.append(word_clean)
        
        if filtered_words:
            cleaned_part = ' '.join(filtered_words)
            if (cleaned_part in KNOWN_ALLERGENS or 
                (keep_unknown and 
                 len(cleaned_part) >= min_word_length and 
                 not cleaned_part.isdigit() and
                 any(c.isalpha() for c in cleaned_part))):
                cleaned_parts.append(cleaned_part)
    
    seen = set()
    unique_parts = []
    for part in cleaned_parts:
        if part not in seen:
            seen.add(part)
            unique_parts.append(part)
    
    return ', '.join(unique_parts) if unique_parts else ''


def process_allergen_column(df, column_name='allergens'):
    """
    Processes the allergens column in a DataFrame.
    
    Args:
        df: pandas DataFrame
        column_name: Name of the column to process
        
    Returns:
        DataFrame with processed column
    """
    if column_name in df.columns:
        df = df.copy()  # Create a copy to avoid SettingWithCopyWarning
        df[column_name] = df[column_name].apply(clean_allergen_text)
    return df


def find_unknown_allergens(df, column_name='allergens'):
    """
    Finds allergens not in the known list.
    
    Args:
        df: pandas DataFrame
        column_name: Name of the allergens column
        
    Returns:
        Set of unrecognized allergens
    """
    unknown = set()
    if column_name not in df.columns:
        return unknown
    
    for value in df[column_name].dropna():
        if value and value.strip():
            allergens = [a.strip().lower() for a in value.split(',')]
            for allergen in allergens:
                if allergen and allergen not in KNOWN_ALLERGENS:
                    unknown.add(allergen)
    return unknown


def add_known_allergen(allergen):
    """Adds a new allergen to the known list and persists it to file."""
    allergen_lower = allergen.lower().strip()
    if allergen_lower in KNOWN_ALLERGENS:
        return False
    KNOWN_ALLERGENS.add(allergen_lower)
    _save_known_allergens()
    return True


def add_multiple_allergens(allergen_list):
    """Adds multiple allergens to the known list and persists them to file."""
    added = []
    skipped = []
    for allergen in allergen_list:
        allergen_lower = allergen.lower().strip()
        if allergen_lower in KNOWN_ALLERGENS:
            skipped.append(allergen_lower)
        else:
            KNOWN_ALLERGENS.add(allergen_lower)
            added.append(allergen_lower)
    
    if added:
        _save_known_allergens()
    
    return {'added': added, 'skipped': skipped}


def save_known_allergens_to_file(filepath='known_allergens.txt'):
    """Saves the current KNOWN_ALLERGENS list to a file."""
    filepath = Path(filepath)
    with open(filepath, 'w', encoding='utf-8') as f:
        for allergen in sorted(KNOWN_ALLERGENS):
            f.write(f"{allergen}\n")


def load_known_allergens_from_file(filepath='known_allergens.txt'):
    """Loads allergens from a file and adds them to KNOWN_ALLERGENS."""
    filepath = Path(filepath)
    if not filepath.exists():
        return 0
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            allergens = [line.strip().lower() for line in f if line.strip()]
        result = add_multiple_allergens(allergens)
        return len(result['added'])
    except Exception:
        return 0


def reset_to_defaults():
    """Resets KNOWN_ALLERGENS to default values and saves to file."""
    global KNOWN_ALLERGENS
    
    default_allergens = {
        'milk', 'egg', 'eggs', 'fish', 'shellfish', 'crustacean', 'crustaceans',
        'peanut', 'peanuts', 'tree nuts', 'almond', 'almonds', 'walnut', 'walnuts',
        'cashew', 'cashews', 'pecan', 'pecans', 'pistachio', 'pistachios',
        'hazelnut', 'hazelnuts', 'macadamia', 'brazil nut', 'brazil nuts',
        'soy', 'soya', 'wheat', 'gluten', 'sesame', 'celery', 'mustard',
        'lupin', 'molluscs', 'mollusc', 'sulfite', 'sulfites', 'sulphite', 'sulphites',
        'phenylalanine', 'lactose', 'casein', 'whey', 'dairy',
        'pork', 'beef', 'chicken', 'shrimp', 'crab', 'lobster', 'oyster',
        'clam', 'squid', 'octopus', 'barley', 'rye', 'oats'
    }
    
    KNOWN_ALLERGENS = default_allergens
    _save_known_allergens()
    return len(KNOWN_ALLERGENS)

