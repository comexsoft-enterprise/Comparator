# AI Consumer Goods - Neo4j Migration Project

Este proyecto se encarga de migrar datos de archivos Excel, CSV y JSON a una base de datos Neo4j.

## Estructura del Proyecto

```
ai_consumer_goods/
├── src/                    # Código fuente principal
│   ├── connectors/         # Conectores a bases de datos y APIs
│   ├── preprocessors/      # Procesamiento de datos
│   ├── validators/         # Validación de formatos y datos
│   └── utils/              # Utilidades y funciones auxiliares
├── data/                   # Datos del proyecto
│   ├── processed/          # Archivos procesados y limpios
    │   ├── validated/                # Datos tras la validación y estandarización de columnas
    │   ├── translated/               # Datos traducidos (Azure Translator)
    │   └── enriched/                 # Datos completados por LLM
    ├── raw/                # Archivos originales (Excel, CSV, JSON)
│   └── schemas/            # Esquemas de validación y estructura
├── config/                 # Archivos de configuración
├── tests/                  # Pruebas unitarias e integración
├── docs/                   # Documentación
├── logs/                   # Archivos de log
├── .env                    # Variables de entorno
├── requirements.txt        # Dependencias de Python
└── README.md              # Este archivo
```

## Componentes Principales

### Connectors
- **Neo4j**: Conexión y operaciones con la base de datos Neo4j
- **APIs**: Conectores para futuras integraciones con APIs externas

### Preprocessors
- **Excel**: Procesamiento de archivos Excel
- **CSV**: Procesamiento de archivos CSV
- **JSON**: Procesamiento de archivos JSON

### Validators
- **Format**: Validación de formatos de archivo
- **Schema**: Validación de esquemas de datos
- **Data**: Validación de integridad de datos

### Data Structure
- **raw/**: Archivos originales sin procesar
- **processed/**: Archivos listos para importar a Neo4j
- **schemas/**: Definiciones de esquemas y estructuras de datos

## Instalación

1. Crear entorno virtual:
```bash
python -m venv .venv
.venv\Scripts\activate
```

2. Instalar dependencias:
```bash
pip install -r requirements.txt
```

3. Configurar variables de entorno en `.env`

## Uso

1. Colocar archivos de datos en `data/raw/`
2. Ejecutar validaciones y preprocesamiento
3. Importar datos a Neo4j

## Documentación

Ver la carpeta `docs/` para documentación detallada de cada componente.