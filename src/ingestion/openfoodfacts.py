from pathlib import Path
import sys
import pandas as pd
import json

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_json_to_dataframe(json_data):
    """
    Carga un JSON en un DataFrame de pandas, expandiendo el campo 'Nutritional Value'
    en múltiples columnas con el formato 'nutritional_value.nombre_campo'.
    
    Args:
        json_data: Lista de diccionarios o string JSON
        
    Returns:
        pd.DataFrame: DataFrame con los datos procesados
    """
    # Si json_data es un string, convertirlo a lista de diccionarios
    if isinstance(json_data, str):
        json_data = json.loads(json_data)
    
    # Crear DataFrame desde el JSON
    df = pd.DataFrame(json_data)
    
    # Verificar si existe la columna 'Nutritional Value'
    if 'Nutritional Value' in df.columns:
        # Normalizar el campo 'Nutritional Value' y añadir prefijo
        nutritional_df = pd.json_normalize(df['Nutritional Value'])
        nutritional_df.columns = ['nutritional_value.' + col for col in nutritional_df.columns]
        
        # Eliminar la columna original 'Nutritional Value'
        df = df.drop('Nutritional Value', axis=1)
        
        # Concatenar el DataFrame original con las columnas nutricionales
        df = pd.concat([df, nutritional_df], axis=1)
    
    # Limpiar caracteres de nueva línea en todas las columnas de texto
    for col in df.columns:
        if df[col].dtype == 'object':  # Solo columnas de texto
            df[col] = df[col].apply(lambda x: str(x).replace('\n', ' ').replace('\r', ' ') if not isinstance(x, (list, dict)) and pd.notna(x) else x)
    
    # Eliminar prefijos de idioma (en:, es:, fr:, etc.)
    df = remove_language_prefixes(df)
    
    # Formatear la columna Allergens si existe
    df = format_allergens(df)
    
    # Limpiar caracteres ilegales para Excel
    df = clean_for_excel(df)
    
    return df


def remove_language_prefixes(df):
    """
    Elimina prefijos de idioma como 'en:', 'es:', 'fr:', etc. de todos los valores de texto.
    Maneja prefijos al inicio, después de espacios, comas, comillas simples o ['
    Por ejemplo: 'en:soy, en:milk' -> 'soy, milk', ['en:word -> ['word
    
    Args:
        df: DataFrame de pandas
        
    Returns:
        pd.DataFrame: DataFrame con los prefijos de idioma eliminados
    """
    import re
    
    # Patrón para detectar prefijos de idioma:
    # - Al inicio de la cadena (^)
    # - Después de un espacio, coma, comilla simple o ['
    # Capturamos el carácter previo para mantenerlo
    pattern = r'(^|[\s,\']|\[\'?)[a-z]{2}:'
    
    for col in df.columns:
        if df[col].dtype == 'object':  # Solo columnas de texto
            df[col] = df[col].apply(
                lambda x: re.sub(pattern, r'\1', str(x)) if not isinstance(x, (list, dict)) and pd.notna(x) else x
            )
    
    return df


def format_allergens(df):
    """
    Formatea la columna 'Allergens' separando palabras con comas.
    Por ejemplo: 'soy hazelnuts milk' -> 'soy, hazelnuts, milk'
    
    Args:
        df: DataFrame de pandas
        
    Returns:
        pd.DataFrame: DataFrame con la columna Allergens formateada
    """
    if 'Allergens' in df.columns:
        df['Allergens'] = df['Allergens'].apply(
            lambda x: ', '.join(str(x).split()) if pd.notna(x) and str(x).strip() != '' else x
        )
    
    return df


def clean_for_excel(df):
    """
    Limpia caracteres ilegales para Excel de todas las columnas de texto.
    Excel no permite ciertos caracteres de control y algunos caracteres Unicode.
    
    Args:
        df: DataFrame de pandas
        
    Returns:
        pd.DataFrame: DataFrame con caracteres ilegales eliminados
    """
    import re
    
    # Patrón para caracteres ilegales en Excel:
    # - Caracteres de control (0x00-0x1F excepto tab, newline, carriage return)
    # - Otros caracteres problemáticos
    illegal_chars_pattern = re.compile(
        r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F\uFFFD\uFFFE\uFFFF]'
    )
    
    for col in df.columns:
        if df[col].dtype == 'object':  # Solo columnas de texto
            df[col] = df[col].apply(
                lambda x: illegal_chars_pattern.sub('', str(x)) if not isinstance(x, (list, dict)) and pd.notna(x) else x
            )
    
    return df


def load_json_from_file(file_path):
    """
    Carga un archivo JSON y lo convierte en DataFrame.
    
    Args:
        file_path: Ruta al archivo JSON
        
    Returns:
        pd.DataFrame: DataFrame con los datos procesados
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    return load_json_to_dataframe(json_data)


# Ejemplo de uso
if __name__ == "__main__":
    # Cargar datos desde el archivo JSON
    df = load_json_from_file('productos.json')
    
    # Mostrar información del DataFrame
    print("Shape del DataFrame:", df.shape)
    print("\nColumnas del DataFrame:")
    print(df.columns.tolist())
    print("\nPrimeras filas:")
    print(df.head())
    
    # Mostrar las columnas de valores nutricionales
    nutritional_columns = [col for col in df.columns if col.startswith('nutritional_value.')]
    print(f"\nColumnas nutricionales ({len(nutritional_columns)}):")
    for col in nutritional_columns:
        print(f"  - {col}")

    df.to_excel("openfoodfacts.xlsx", index=False)
