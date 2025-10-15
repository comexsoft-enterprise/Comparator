import json
import unicodedata

# Función para quitar los acentos de una cadena de texto
def quitar_acentos(texto):
    return ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )

# Cargar el archivo JSON
def procesar_json(nombre_archivo):
    with open(nombre_archivo, 'r', encoding='utf-8') as archivo:
        datos = json.load(archivo)

    # Recorrer el JSON y quitar acentos de las cadenas de texto
    def procesar_elemento(elemento):
        if isinstance(elemento, dict):
            return {clave: procesar_elemento(valor) for clave, valor in elemento.items()}
        elif isinstance(elemento, list):
            return [procesar_elemento(i) for i in elemento]
        elif isinstance(elemento, str):
            return quitar_acentos(elemento)
        else:
            return elemento

    datos_procesados = procesar_elemento(datos)

    # Guardar el JSON procesado
    with open('procesado_' + nombre_archivo, 'w', encoding='utf-8') as archivo:
        json.dump(datos_procesados, archivo, ensure_ascii=False, indent=4)

# Usar el script para procesar el archivo JSON
nombre_archivo = 'eroski.json'  # Reemplaza con el nombre de tu archivo
procesar_json(nombre_archivo)
