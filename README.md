# AI Consumer Goods - Multi-Store Product Intelligence System

Sistema de inteligencia de productos para comparación cross-store entre cadenas de supermercados utilizando bases de datos multi-modal (Neo4j, MongoDB, PostgreSQL) y modelos de ML/AI.

## Descripción General

Este proyecto implementa un sistema completo de procesamiento, enriquecimiento y comparación de productos de supermercados que:
- Ingesta datos de múltiples cadenas de supermercados (CSV, Excel, JSON)
- Procesa y enriquece datos usando LLMs (Azure OpenAI GPT-5-nano)
- Almacena información en tres bases de datos especializadas
- Habilita búsqueda de similitud cross-store usando embeddings ML
- Proporciona una API REST para comparación de productos

**Stack Tecnológico**:
- **Lenguaje**: Python 3.10+
- **Framework API**: FastAPI
- **Bases de Datos**: MongoDB (datos enriquecidos), PostgreSQL (variables numéricas), Neo4j (grafo de productos)
- **ML/AI**: Azure OpenAI (GPT-5-nano para enriquecimiento, text-embedding-3-small para vectores)
- **Procesamiento**: Pandas, NumPy, Scikit-learn
- **Graph DB**: Neo4j con Cypher queries

---

## Estructura Completa del Repositorio

```
Comparator/
├── config/                              # ⚙️ Configuración central
│   ├── __init__.py
│   ├── settings.py                      # Configuración de BD, API keys, rutas
│   └── logs.py                          # Configuración de logging
│
├── data/                                # 💾 Almacenamiento de datos
│   ├── raw/                             # Archivos originales cargados
│   │   ├── csv/                         # Archivos CSV
│   │   │   └── openfoodfacts_complete.csv
│   │   └── xlsx/                        # Archivos Excel
│   ├── processed/                       # Datos procesados por pipeline
│   │   ├── validated/                   # Etapa 1: Validación de columnas
│   │   ├── verified/                    # Etapa 2: Sincronización MongoDB
│   │   ├── translated/                  # Etapa 3: Traducción a inglés
│   │   ├── enriched/                    # Etapa 4: Completado por LLM
│   │   ├── fixed/                       # Etapa 5: Limpieza final
│   │   ├── embeddings_cache.pkl         # Cache de embeddings
│   │   └── embeddings_cache.bak         # Backup de cache
│   ├── results/                         # Resultados de comparaciones
│   │   └── cross_store_similarity_*.xlsx
│   ├── openfoodfacts/                   # Datos de OpenFoodFacts
│   └── schemas/                         # 📋 Definiciones de estructura
│       ├── column_registry.json         # Taxonomía maestra de columnas
│       ├── taxonomy.py                  # Clases de datos de taxonomía
│       ├── categories.py                # Enumeraciones de categorización
│       ├── neo4j_ontology.py            # Definición de estructura grafo Neo4j
│       └── product_type_patterns.py     # Patrones de clasificación
│
├── src/                                 # 🔧 Código fuente
│   ├── api/                             # 🌐 API REST (FastAPI)
│   │   ├── main.py                      # Aplicación principal y endpoints
│   │   └── api_utils/
│   │       ├── upload_file.py           # Orquestación del pipeline
│   │       ├── neo4j_queries.py         # Constructor dinámico de queries
│   │       └── product_files.py         # Generación de archivos de productos
│   │
│   ├── connectors/                      # 🔌 Conectores de bases de datos
│   │   ├── neo4j_connector.py           # Gestión de conexión Neo4j
│   │   ├── mongodb_connector.py         # Gestión de conexión MongoDB
│   │   └── postgresql_connector.py      # Gestión de conexión PostgreSQL
│   │
│   ├── preprocessors/                   # 📝 Módulos de preprocesamiento
│   │   ├── data_formatting/             # Etapa 1 y 2: Validación y verificación
│   │   │   ├── column_validation.py     # Estandarización de columnas
│   │   │   ├── product_verification.py  # Sincronización con MongoDB
│   │   │   └── path_handling.py         # Utilidades de rutas
│   │   ├── text_translation/            # Etapa 3: Traducción
│   │   │   └── translator.py            # Traducción multi-idioma a inglés
│   │   └── data_autocompletion/         # Etapas 4-5: Enriquecimiento
│   │       ├── llm_completion.py        # Completado de campos con LLM
│   │       ├── csv_fixing.py            # Limpieza y validación de datos
│   │       ├── run_completion_only.py   # Script solo completado
│   │       ├── run_fixing_only.py       # Script solo limpieza
│   │       ├── known_allergens.txt      # Lista de alérgenos conocidos
│   │       └── stopwords.py             # Stopwords de alérgenos/ingredientes
│   │
│   ├── ingestion/                       # 💾 Ingesta a bases de datos
│   │   ├── neo4j_ingest.py              # Etapa 7: Ingesta CSV a Neo4j
│   │   ├── nodes_relationships.py       # Construcción de grafo Neo4j
│   │   ├── neo4j_embeddings.py          # Etapa 8: Generación de embeddings
│   │   ├── neo4j_embeddings_hf.py       # Embeddings con HuggingFace
│   │   └── openfoodfacts.py             # Procesamiento OpenFoodFacts
│   │
│   ├── numeric_variables_postgres/      # Etapa 6: PostgreSQL
│   │   └── postgresql_data_extraction.py # Extracción de datos numéricos
│   │
│   ├── models/                          # 🤖 Modelos de ML
│   │   ├── model_variations/            # Modelos de similitud de productos
│   │   │   ├── get_similar_neo4j_refactored.py  # Modelo principal de similitud
│   │   │   ├── model.py                 # Lógica de similitud combinada
│   │   │   ├── insert_match_posgres.py  # Inserción de matches en PostgreSQL
│   │   │   ├── clean_matches_csv.py     # Limpieza de resultados
│   │   │   ├── check_and_create_indices.py      # Gestión de índices
│   │   │   └── models_analysis/         # Análisis de rendimiento de modelos
│   │   │       ├── comparativa_modelos.xlsx
│   │   │       ├── allmini_eroski-01013_makro-01013_sin_repetidos.csv
│   │   │       ├── fullmodel_eroski-01013_to_makro-01013_sin_repetidos.xlsx
│   │   │       └── openai_eroski-01013_to_makro-01013_sin_repetidos.xlsx
│   │   ├── category_analysis/           # Similitud basada en grafo
│   │   │   └── category_analysis.py
│   │   ├── embedding_analysis/          # Similitud basada en embeddings
│   │   │   ├── embedding_analysis.py
│   │   │   └── embedding_generation.py
│   │   └── postgresql_analysis/         # Análisis con PostgreSQL
│   │       ├── euclidean_distance_calculator.py
│   │       ├── postgresql_eclidean_distance_calculation.py
│   │       └── postgresql_eclidean_distance_calculation_siid_only.py
│   │
│   ├── model_exports/                   # Exportación de resultados
│   │   └── export_utils.py              # Utilidades de exportación
│   │
│   ├── relate_similars_terms/           # 🔗 Sistema de reglas de similitud
│   │   ├── main.py                      # Aplicación principal
│   │   ├── web.py                       # Interfaz web
│   │   ├── apply_rules_direct.py        # Aplicación directa de reglas
│   │   ├── verify_and_apply_aliases.py  # Verificación de alias
│   │   ├── brand_rules_seed.json        # Reglas semilla de marcas
│   │   ├── component_rules_seed.json    # Reglas semilla de componentes
│   │   ├── format_rules_seed.json       # Reglas semilla de formatos
│   │   ├── ingredient_rules_seed.json   # Reglas semilla de ingredientes
│   │   ├── quantity_rules_seed.json     # Reglas semilla de cantidades
│   │   ├── sugerencias_brand.json       # Sugerencias de marcas
│   │   ├── sugerencias_component.json   # Sugerencias de componentes
│   │   ├── sugerencias_format.json      # Sugerencias de formatos
│   │   ├── sugerencias_ingredient.json  # Sugerencias de ingredientes
│   │   └── sugerencias_quantity.json    # Sugerencias de cantidades
│   │
│   └── validate_match/                  # ✅ Validación de matches
│       ├── validate_web.py              # Interfaz web de validación
│       └── templates/
│           └── validate_matches.html    # Template HTML
│
├── docs/                                # 📚 Documentación
│   ├── API_DOCUMENTATION.md             # Documentación de la API REST
│   ├── BBDD_documentation.md            # Documentación de bases de datos
│   ├── DATA_INGESTION_PIPELINE.md       # Pipeline de ingesta de datos
│   ├── MODEL_DOCUMENTATION.md           # Documentación de modelos ML
│   ├── PRODUCT_FILE_GENERATION.md       # Generación de archivos de productos
│   ├── REPOSITORY_ARCHITECTURE.md       # Arquitectura del repositorio
│   └── __init__.py
│
├── .env                                 # Variables de entorno (no versionado)
├── .gitignore                           # Archivos ignorados por Git
├── requirements.txt                     # Dependencias de Python
├── productos.json                       # Datos de productos de ejemplo
└── README.md                            # Este archivo
```

---

## Componentes Principales

### 1. API REST (FastAPI)
**Ubicación**: `src/api/`

Proporciona endpoints HTTP para:
- **Comparación de productos**: Similitud cross-store usando modelos ML
- **Gestión de datos**: Consultas a Neo4j, MongoDB y PostgreSQL
- **Carga de archivos**: Pipeline automático de procesamiento
- **Eliminación de productos**: Borrado en todas las bases de datos

**Endpoints principales**:
- `POST /compare-stores`: Comparación de productos entre tiendas
- `POST /upload`: Carga y procesamiento de archivos
- `POST /upload/products-array`: Procesamiento de array de productos
- `GET /health`: Estado de conexiones a bases de datos
- `GET /neo4j/nodes/{label}`: Obtener nodos por etiqueta
- `DELETE /products/{siid}`: Eliminar producto por ID

### 2. Conectores de Bases de Datos
**Ubicación**: `src/connectors/`

- **Neo4j** (`neo4j_connector.py`): Gestión de grafo de productos y relaciones
- **MongoDB** (`mongodb_connector.py`): Almacenamiento de datos enriquecidos
- **PostgreSQL** (`postgresql_connector.py`): Variables numéricas y matches

### 3. Pipeline de Preprocesamiento
**Ubicación**: `src/preprocessors/`

#### Etapa 1-2: Validación y Verificación (`data_formatting/`)
- `column_validation.py`: Estandarización de columnas según taxonomía
- `product_verification.py`: Sincronización con MongoDB
- `path_handling.py`: Gestión de rutas de archivos

#### Etapa 3: Traducción (`text_translation/`)
- `translator.py`: Traducción automática a inglés (Azure Translator)

#### Etapas 4-5: Enriquecimiento (`data_autocompletion/`)
- `llm_completion.py`: Completado de campos usando GPT-5-nano
- `csv_fixing.py`: Limpieza y validación final
- Scripts independientes: `run_completion_only.py`, `run_fixing_only.py`

### 4. Ingesta a Bases de Datos
**Ubicación**: `src/ingestion/`

- **Neo4j**:
  - `neo4j_ingest.py`: Ingesta de CSV a Neo4j (Etapa 7)
  - `nodes_relationships.py`: Construcción de nodos y relaciones
  - `neo4j_embeddings.py`: Generación de embeddings (Etapa 8)
  - `neo4j_embeddings_hf.py`: Embeddings con HuggingFace

- **PostgreSQL**:
  - `numeric_variables_postgres/postgresql_data_extraction.py`: Extracción de variables numéricas (Etapa 6)

### 5. Modelos de ML/AI
**Ubicación**: `src/models/`

#### Modelos de Similitud (`model_variations/`)
- `get_similar_neo4j_refactored.py`: Modelo principal de similitud cross-store
- `model.py`: Lógica combinada (grafo + embeddings)
- `faiss_embedding_similarity.py`: Búsqueda vectorial con FAISS
- `insert_match_posgres.py`: Persistencia de matches

#### Análisis Especializados
- `category_analysis/`: Similitud basada en grafo Neo4j
- `embedding_analysis/`: Similitud semántica con embeddings
- `postgresql_analysis/`: Distancia euclidiana en variables numéricas

### 6. Sistema de Reglas de Similitud
**Ubicación**: `src/relate_similars_terms/`

Sistema para gestionar aliases y reglas de similitud entre términos:
- `main.py` / `web.py`: Interfaz de gestión de reglas
- `apply_rules_direct.py`: Aplicación de reglas predefinidas
- Archivos de semillas (`*_rules_seed.json`): Reglas base por categoría
- Archivos de sugerencias (`sugerencias_*.json`): Sugerencias de similitudes

### 7. Validación de Matches
**Ubicación**: `src/validate_match/`

- `validate_web.py`: Interfaz web para validación manual de matches
- Templates HTML para visualización de resultados

### 8. Esquemas y Taxonomías
**Ubicación**: `data/schemas/`

- `column_registry.json`: Taxonomía maestra de columnas del sistema
- `taxonomy.py`: Clases Pydantic para manejo de taxonomía
- `categories.py`: Enumeraciones de categorías de productos
- `neo4j_ontology.py`: Definición de ontología del grafo
- `product_type_patterns.py`: Patrones de clasificación

---

## Pipeline de Procesamiento

El sistema implementa un pipeline de 8 etapas:

1. **Validación**: Estandarización de columnas según `column_registry.json`
2. **Verificación**: Sincronización con MongoDB (evitar duplicados)
3. **Traducción**: Conversión a inglés de campos multiidioma
4. **Enriquecimiento**: Completado de campos vacíos con LLM
5. **Limpieza**: Validación final y corrección de datos
6. **PostgreSQL**: Extracción de variables numéricas
7. **Neo4j**: Ingesta de datos y construcción de grafo
8. **Embeddings**: Generación de vectores semánticos

**Flujo**:
```
Raw File → [1-5] → data/processed/fixed/ → [6] → PostgreSQL
                                          → [7] → Neo4j nodes/relationships
                                          → [8] → Neo4j embeddings
                                          → MongoDB (datos enriquecidos)
```

---

## Instalación y Configuración

### 1. Crear entorno virtual
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# o
venv\Scripts\activate     # Windows
```

### 2. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

Crear archivo `.env` en la raíz del proyecto:

```bash
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j

# MongoDB
MONGO_URI=mongodb://localhost:27017
MONGO_USER1=admin
MONGO_USER1_PASSWORD=your_password
MONGO_DATABASE=supermarket_db

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=myappdb
POSTGRES_USER=admin
POSTGRES_PASSWORD=your_password

# Azure OpenAI
AZURE_OPENAI_API_TYPE=azure
AZURE_OPENAI_API_KEY=your_api_key
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_API_BASE=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-5-nano
AZURE_OPENAI_API_ENDPOINT=https://your-resource.openai.azure.com/

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/migration.log
```

### 4. Iniciar bases de datos

Asegurarse de que Neo4j, MongoDB y PostgreSQL estén ejecutándose.

---

## Uso

### Iniciar la API

```bash
cd src/api
python main.py

# O con uvicorn directamente
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

La API estará disponible en `http://localhost:8000`

### Documentación interactiva de la API

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Procesar archivos

**Opción 1: API (recomendado)**
```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@data/raw/csv/products.csv" \
  -F "store_name=eroski-01013" \
  -F "postcode=01013"
```

**Opción 2: Scripts directos**
```bash
# Solo completado LLM
python src/preprocessors/data_autocompletion/run_completion_only.py

# Solo limpieza
python src/preprocessors/data_autocompletion/run_fixing_only.py

# Pipeline completo de ingesta
python src/ingestion/neo4j_ingest.py
```

### Comparar productos entre tiendas

```bash
curl -X POST "http://localhost:8000/compare-stores" \
  -H "Content-Type: application/json" \
  -d '{
    "store_a": "eroski-01013",
    "store_b": "makro-01013",
    "list_ids": ["SIID123", "SIID456"],
    "top_n_results": 5
  }'
```

### Interfaz web de validación

```bash
python src/validate_match/validate_web.py
```

Acceder a `http://localhost:5000` para validar matches visualmente.

---

## Tecnologías y Dependencias Principales

- **FastAPI** (0.128.0): Framework web asíncrono
- **Neo4j** (6.0.3): Base de datos de grafos
- **PyMongo** (4.15.5): Driver de MongoDB
- **psycopg2-binary** (2.9.11): Driver de PostgreSQL
- **LangChain** (1.0.1+): Framework para LLMs
- **OpenAI** (2.14.0): Cliente de Azure OpenAI
- **Pandas** (2.3.3): Manipulación de datos
- **NumPy** (2.4.0): Computación numérica
- **scikit-learn** (1.8.0): ML tradicional
- **Pydantic** (2.12.5): Validación de datos
- **SQLAlchemy** (2.0.45): ORM para PostgreSQL
- **uvicorn** (0.40.0): Servidor ASGI

Ver `requirements.txt` para la lista completa.

---

## Documentación Adicional

La carpeta `docs/` contiene documentación detallada:

- **[API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md)**: Referencia completa de endpoints
- **[BBDD_documentation.md](docs/BBDD_documentation.md)**: Esquemas de bases de datos
- **[DATA_INGESTION_PIPELINE.md](docs/DATA_INGESTION_PIPELINE.md)**: Pipeline detallado
- **[MODEL_DOCUMENTATION.md](docs/MODEL_DOCUMENTATION.md)**: Modelos ML y parámetros
- **[PRODUCT_FILE_GENERATION.md](docs/PRODUCT_FILE_GENERATION.md)**: Generación de archivos
- **[REPOSITORY_ARCHITECTURE.md](docs/REPOSITORY_ARCHITECTURE.md)**: Arquitectura completa

---

## Características Principales

### 🎯 Comparación Cross-Store
Encuentra productos similares entre diferentes cadenas de supermercados combinando:
- Similitud de grafo (atributos compartidos en Neo4j)
- Similitud semántica (embeddings de OpenAI)
- Distancia euclidiana (variables numéricas)

### 🤖 Enriquecimiento Automático
Completa automáticamente campos vacíos usando:
- GPT-5-nano para inferencia inteligente
- Traducción multiidioma (Azure Translator)
- Validación y corrección de datos

### 📊 Multi-Base de Datos
Aprovecha fortalezas de cada tecnología:
- **Neo4j**: Relaciones y grafo de productos
- **MongoDB**: Datos flexibles y enriquecidos
- **PostgreSQL**: Variables numéricas y matches

### 🔍 Búsqueda Avanzada
- Filtros complejos en grafo Neo4j
- Búsqueda vectorial con FAISS
- Queries dinámicas basadas en ontología

### 🎨 Interfaces Web
- API REST con documentación Swagger
- Validación visual de matches
- Gestión de reglas de similitud

