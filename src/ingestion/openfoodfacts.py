from pathlib import Path
import sys
import pandas as pd
import json
import logging
import gc
import io
from fastapi import UploadFile

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import the API upload function
from src.api.api_utils.upload_file import process_uploaded_file

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


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
    illegal_chars_pattern = re.compile(
        r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F\uFFFD\uFFFE\uFFFF]'
    )
    
    for col in df.columns:
        if df[col].dtype == 'object':  # Solo columnas de texto
            df[col] = df[col].apply(
                lambda x: illegal_chars_pattern.sub('', str(x)) if not isinstance(x, (list, dict)) and pd.notna(x) else x
            )
    
    return df


def upload_excel_file_to_api(excel_path, base_path='data/raw', delete_after_upload=True):
    """
    Upload an Excel file to the API for processing.
    
    Args:
        excel_path: Path to the Excel file to upload
        base_path: Base directory for raw data files (default: 'data/raw')
        delete_after_upload: Whether to delete the local Excel file after successful upload
        
    Returns:
        dict: Upload result with status and details
    """
    try:
        logging.info(f"📤 Uploading file to API: {Path(excel_path).name}")
        
        # Read the Excel file into memory
        with open(excel_path, 'rb') as f:
            file_content = f.read()
        
        # Create an UploadFile object
        file_obj = UploadFile(
            filename=Path(excel_path).name,
            file=io.BytesIO(file_content)
        )
        
        # Process the file through the API upload pipeline
        result = process_uploaded_file(file_obj, base_path=base_path)
        
        if result and result.get('status') == 'success':
            logging.info(f"✅ Successfully uploaded and processed: {Path(excel_path).name}")
            
            # Delete local file if requested
            if delete_after_upload:
                try:
                    Path(excel_path).unlink()
                    logging.info(f"🗑️  Deleted local file: {Path(excel_path).name}")
                except Exception as e:
                    logging.warning(f"⚠️  Could not delete local file {excel_path}: {e}")
        else:
            logging.error(f"❌ Upload failed for {Path(excel_path).name}: {result}")
        
        return result
        
    except Exception as e:
        logging.error(f"❌ Error uploading file {excel_path}: {e}")
        return {"status": "error", "error": str(e)}


def process_large_json_file_streaming(file_path, output_dir='data/openfoodfacts', chunk_size=10000, 
                                       upload_to_api=True, delete_excel_after_upload=True, 
                                       base_path='data/raw'):
    """
    Procesa un archivo JSON grande de forma eficiente usando streaming y procesamiento por chunks.
    Genera CSV completo y opcionalmente sube cada chunk a la API en el momento de generación.
    
    Args:
        file_path: Ruta al archivo JSON
        output_dir: Directorio donde guardar los archivos de salida
        chunk_size: Número de registros por chunk (default: 10000)
        upload_to_api: Si True, sube cada Excel file a la API inmediatamente (default: True)
        delete_excel_after_upload: Si True, elimina el archivo Excel local después de subirlo (default: True)
        base_path: Base directory para la API de upload (default: 'data/raw')
        
    Returns:
        dict: Información sobre los archivos generados y subidos
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    logging.info(f"📂 Procesando archivo JSON en modo streaming: {file_path}")
    
    # Preparar archivos de salida
    csv_path = output_path / 'openfoodfacts_complete.csv'
    excel_files = []
    upload_results = []
    
    # Variables para tracking
    total_records = 0
    chunk_buffer = []
    chunk_number = 0
    csv_header_written = False
    
    # Leer JSON línea por línea (asumiendo que es un array JSON)
    logging.info("🔄 Leyendo y procesando datos en chunks...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        # Leer el archivo carácter por carácter para parsear el array JSON
        content = f.read()
        
        # Parsear el JSON completo (pero lo procesaremos en chunks)
        logging.info("📖 Parseando JSON...")
        json_data = json.loads(content)
        total_records = len(json_data)
        logging.info(f"📊 Total de registros: {total_records}")
        
        # Liberar memoria del string original
        del content
        gc.collect()
        
        # Procesar en chunks
        num_chunks = (total_records + chunk_size - 1) // chunk_size
        logging.info(f"📦 Procesando en {num_chunks} chunks de {chunk_size} registros")
        
        for i in range(0, total_records, chunk_size):
            chunk_number += 1
            chunk_data = json_data[i:i + chunk_size]
            
            logging.info(f"⚙️  Procesando chunk {chunk_number}/{num_chunks} ({len(chunk_data)} registros)...")
            
            # Procesar chunk
            df_chunk = load_json_to_dataframe(chunk_data)
            
            # Guardar en CSV (append mode)
            if not csv_header_written:
                df_chunk.to_csv(csv_path, index=False, sep=';', encoding='utf-8', mode='w')
                csv_header_written = True
            else:
                df_chunk.to_csv(csv_path, index=False, sep=';', encoding='utf-8', mode='a', header=False)
            
            # Guardar chunk en Excel
            excel_path = output_path / f'openfoodfacts.xlsx'
            try:
                df_chunk.to_excel(excel_path, index=False, engine='xlsxwriter')
                excel_files.append(str(excel_path))
                logging.info(f"✅ Chunk {chunk_number}/{num_chunks} guardado: {excel_path.name}")
                
                # Upload to API if requested
                if upload_to_api:
                    upload_result = upload_excel_file_to_api(
                        excel_path, 
                        base_path=base_path,
                        delete_after_upload=delete_excel_after_upload
                    )
                    upload_results.append({
                        'chunk': chunk_number,
                        'file': excel_path.name,
                        'result': upload_result
                    })
                
            except Exception as e:
                logging.error(f"❌ Error guardando/subiendo Excel chunk {chunk_number}: {e}")
            
            # Liberar memoria
            del df_chunk
            del chunk_data
            gc.collect()
        
        # Liberar memoria del JSON completo
        del json_data
        gc.collect()
    
    summary = {
        'total_records': total_records,
        'csv_file': str(csv_path),
        'excel_files': excel_files,
        'num_excel_files': len(excel_files),
        'chunk_size': chunk_size,
        'upload_to_api': upload_to_api,
        'upload_results': upload_results if upload_to_api else None
    }
    
    logging.info(f"\n{'='*60}")
    logging.info(f"✅ Procesamiento completado")
    logging.info(f"   - Registros totales: {total_records}")
    logging.info(f"   - CSV completo: {csv_path}")
    logging.info(f"   - Archivos Excel generados: {len(excel_files)}")
    if upload_to_api:
        successful_uploads = sum(1 for r in upload_results if r['result'].get('status') == 'success')
        logging.info(f"   - Archivos subidos exitosamente: {successful_uploads}/{len(upload_results)}")
    logging.info(f"   - Tamaño de chunk: {chunk_size}")
    logging.info(f"{'='*60}\n")
    
    return summary


# Ejemplo de uso
if __name__ == "__main__":
    # Usar versión streaming para archivos grandes
    # Con upload automático a la API
    summary = process_large_json_file_streaming(
        'productos.json',
        output_dir='data/openfoodfacts',  # Ruta relativa al proyecto
        chunk_size=10000,  # Más pequeño para evitar problemas de memoria
        upload_to_api=True,  # Subir cada archivo Excel a la API automáticamente
        delete_excel_after_upload=True,  # Eliminar Excel local después de subir
        base_path='data/raw'  # Base path para la API de upload
    )
    
    # Si no quieres subir a la API, solo generar archivos locales:
    # summary = process_large_json_file_streaming(
    #     'productos.json',
    #     output_dir='output',
    #     chunk_size=10000,
    #     upload_to_api=False
    # )