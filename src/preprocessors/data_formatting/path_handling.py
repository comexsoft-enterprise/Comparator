#### PREPROCESSING PIPELINE ####

import logging
import os
import glob
from pathlib import Path
from typing import Dict, List

# 1. Ensure filepaths are handled correctly

def get_file_extension(file_path: str) -> str:
    """
    Get the file extension from a file path.
    
    Args:
        file_path (str): Path to the file

    Returns:
        str: File extension (lowercase, including the dot), or an empty string if no extension is found.
    
    Examples:
        >>> get_file_extension("data.csv")
        '.csv'
        >>> get_file_extension("report.xlsx")  
        '.xlsx'
        >>> get_file_extension("config.json")
        '.json'
    """
    return Path(file_path).suffix.lower()


def identify_file_type(file_path: str) -> str:
    """
    Identify the type of file based on its extension.
    
    Args:
        file_path (str): Path to the file
        
    Returns:
        str: File type ('xlsx', 'csv', 'json', 'unknown')
    
    Examples:
        >>> identify_file_type("data.xlsx")
        'xlsx'
        >>> identify_file_type("data.csv")
        'csv'
        >>> identify_file_type("config.json")
        'json'
    """
    extension = get_file_extension(file_path)
    
    if extension in ['.xlsx', '.xls']:
        return 'xlsx'
    elif extension == '.csv':
        return 'csv'
    elif extension == '.json':
        return 'json'
    else:
        return 'unknown'


def validate_file_exists(file_path: str) -> bool:
    """
    Check if a file exists at the given path.
    
    Args:
        file_path (str): Path to the file
        
    Returns:
        bool: True if file exists, False otherwise
    """
    return os.path.isfile(file_path)
    

def get_data_directory_path(base_path: str = "/data/raw") -> str:
    """
    Get the base data directory path, creating it if it doesn't exist.
    
    Args:
        base_path (str): Base path for data directory (default: "data/raw")
        
    Returns:
        str: Absolute path to the data directory
    """
    # If base_path is relative, make it relative to the project root
    if not os.path.isabs(base_path):
        # Assuming we're in src/preprocessors/data_formatting, go up 4 levels to reach project root
        current_dir = Path(__file__).parent.parent.parent.parent
        data_path = current_dir / base_path
    else:
        data_path = Path(base_path)
    
    # Create directory if it doesn't exist
    data_path.mkdir(parents=True, exist_ok=True)
    logging.info(f"Data directory ensured at: {data_path}")
    return str(data_path)


def get_file_type_directory(file_extension: str, base_path: str = "data/raw") -> str:
    """
    Get the directory path for a specific file type based on its extension.
    
    Args:
        file_extension (str): File extension (with or without dot)
        base_path (str): Base path for data directory (default: "data/raw")
        
    Returns:
        str: Path to the file type directory
    """
    # Remove dot from extension if present
    extension = file_extension.lstrip('.').lower()
    
    # Map extensions to directory names
    extension_mapping = {
        'csv': 'csv',
        'xlsx': 'xlsx', 
        'xls': 'xlsx',  # Both .xls and .xlsx go to xlsx folder
        'json': 'json'
    }
    
    dir_name = extension_mapping.get(extension, extension)
    base_dir = get_data_directory_path(base_path)
    type_dir = os.path.join(base_dir, dir_name)
    
    # Create directory if it doesn't exist
    Path(type_dir).mkdir(parents=True, exist_ok=True)
    
    return type_dir


def discover_files_by_type(file_extension: str, base_path: str = "data/raw") -> List[str]:
    """
    Discover all files of a specific type in the organized directory structure.
    
    Args:
        file_extension (str): File extension to search for (with or without dot)
        base_path (str): Base path for data directory (default: "data/raw")
        
    Returns:
        List[str]: List of absolute file paths found
    """
    extension = file_extension.lstrip('.').lower()
    type_dir = get_file_type_directory(extension, base_path)
    
    # Search patterns for different extensions
    patterns = [f'*.{extension}']
    
    files = []
    for pattern in patterns:
        search_pattern = os.path.join(type_dir, pattern)
        files.extend(glob.glob(search_pattern))

    logging.info(f"Discovered {len(files)} files of type '{extension}' in {type_dir}")
    
    return [os.path.abspath(f) for f in files]


def get_organized_file_path(filename: str, base_path: str = "data/raw") -> str:
    """
    Get the expected file path for a file in the organized directory structure.
    
    Args:
        filename (str): Name of the file (with extension)
        base_path (str): Base path for data directory (default: "data/raw")
        
    Returns:
        str: Expected absolute file path in the organized structure
    """
    # Ensure filename is only the file name, not a path
    filename = os.path.basename(filename)
    extension = get_file_extension(filename)
    type_dir = get_file_type_directory(extension, base_path)
    return os.path.join(type_dir, filename)
