# AI Consumer Goods

AI-powered system for consumer goods data processing, analysis, and product comparison across supermarket chains. This project leverages graph databases, embeddings, and machine learning to enable intelligent product matching and data enrichment.

## 🎯 Overview

This system processes and analyzes consumer product data from multiple supermarket chains, providing:
- **Automated Data Processing**: Multi-stage pipeline for validation, translation, and enrichment
- **Product Comparison**: AI-powered similarity matching between products across different stores
- **Graph Database Integration**: Neo4j-based knowledge graph for complex product relationships
- **REST API**: FastAPI endpoints for data access and operations
- **Multi-Database Architecture**: MongoDB for raw data, Neo4j for relationships, PostgreSQL for analytics

## 🏗️ Architecture

```
┌─────────────────┐
│   FastAPI       │  ← REST API Layer
└────────┬────────┘
         │
    ┌────┴────┬──────────┬──────────┐
    │         │          │          │
┌───▼───┐ ┌──▼──┐  ┌────▼────┐ ┌───▼────┐
│MongoDB│ │Neo4j│  │PostgreSQL│ │OpenAI  │
└───────┘ └─────┘  └─────────┘ └────────┘
```

### Database Roles
- **MongoDB**: Stores raw and processed product data with full document flexibility
- **Neo4j**: Maintains product relationships, categories, and embeddings for similarity search
- **PostgreSQL**: Handles numeric variables and analytical queries

## 📁 Project Structure

```
ai_consumer_goods/
├── config/                      # Configuration management
│   ├── settings.py             # Central configuration (DB, API keys)
│   └── logs.py                 # Logging configuration
├── data/
│   ├── raw/                    # Original data files (CSV, JSON, XLSX)
│   ├── processed/              # Processed data pipeline stages
│   │   ├── validated/          # Column standardization
│   │   ├── translated/         # Multi-language translations
│   │   ├── enriched/           # LLM-enriched data
│   │   ├── fixed/              # Post-processing corrections
│   │   └── verified/           # Final validated data
│   └── schemas/                # Data schemas and ontologies
│       ├── taxonomy.py         # Product taxonomy definitions
│       ├── neo4j_ontology.py   # Graph database schema
│       └── column_registry.json
├── src/
│   ├── api/
│   │   ├── main.py            # FastAPI application
│   │   └── api_utils/         # API helper functions
│   ├── connectors/            # Database connectors
│   │   ├── neo4j_connector.py
│   │   ├── mongodb_connector.py
│   │   └── postgresql_connector.py
│   ├── preprocessors/         # Data preprocessing modules
│   │   ├── data_formatting/   # Format standardization
│   │   ├── text_translation/  # AWS/Azure translation
│   │   └── data_autocompletion/ # LLM-based completion
│   ├── ingestion/             # Data ingestion to databases
│   │   ├── neo4j_ingest.py
│   │   ├── neo4j_embeddings.py
│   │   └── nodes_relationships.py
│   ├── models/                # ML models and analysis
│   │   ├── category_analysis/
│   │   ├── embedding_analysis/
│   │   ├── model_variations/
│   │   └── postgresql_analysis/
│   ├── relate_similars_terms/ # Term aliasing and rules
│   └── utils/                 # Utility functions
└── requirements.txt           # Python dependencies
```

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Docker & Docker Compose
- 8GB+ RAM recommended

### 1. Start Infrastructure

```bash
# Navigate to stack directory
cd /path/to/stack

# Start all services
docker-compose up -d

# Verify services are running
docker-compose ps
```

Services will be available at:
- **Neo4j Browser**: http://localhost:3000 (neo4j/testpass)
- **MongoDB**: localhost:27017 (admin/secretpass)
- **Mongo Express**: http://localhost:8081 (meuser/mepass)
- **PostgreSQL**: localhost:5432 (admin/secretpass)
- **PgAdmin**: http://localhost:5050 (admin@local.test/pgadminpass)

### 2. Setup Python Environment

```bash
# Navigate to project directory
cd /path/to/victor/ai_consumer_goods

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

Create a `.env` file in the project root:

```env
# Neo4j Configuration
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=testpass
NEO4J_DATABASE=neo4j

# MongoDB Configuration
MONGO_URI=localhost:27017
MONGO_USER1=admin
MONGO_USER1_PASSWORD=secretpass
MONGO_DATABASE=consumer_goods

# PostgreSQL Configuration
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=myappdb
POSTGRES_USER=admin
POSTGRES_PASSWORD=secretpass

# Azure OpenAI Configuration
AZURE_OPENAI_API_KEY=your_api_key_here
AZURE_OPENAI_API_VERSION=2025-01-01-preview
AZURE_OPENAI_API_BASE=https://your_endpoint.openai.azure.com/openai/v1/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-5-nano
AZURE_OPENAI_ENDPOINT=https://your_endpoint.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=text-embedding-3-large

# AWS Translate Configuration (optional)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_DEFAULT_REGION=us-east-1

# HuggingFace (optional)
USERNAME=your_email
PASSWORD=your_password
```

### 4. Start the API Server

```bash
# From project root
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

API will be available at: http://localhost:8000

Interactive documentation: http://localhost:8000/docs

## 📊 Data Processing Pipeline

### Stage 1: Validation
```bash
# Validates column names and data types
python src/preprocessors/data_formatting/validate.py
```
- Standardizes column names
- Validates data types
- Checks required fields

### Stage 2: Translation
```bash
# Translates text fields to target language
python src/preprocessors/text_translation/translate.py
```
- Uses Azure Translator API
- Multi-threaded processing
- Supports multiple languages

### Stage 3: Enrichment
```bash
# Enriches data using LLMs
python src/preprocessors/data_autocompletion/complete.py
```
- Fills missing data
- Standardizes formats
- Extracts structured information

### Stage 4: Ingestion
```bash
# Ingest to Neo4j with embeddings
python src/ingestion/neo4j_ingest.py
```
- Creates graph nodes and relationships
- Generates embeddings for similarity search
- Maintains data lineage

## 🔌 API Endpoints

### Core Endpoints

#### `GET /`
Health check and API information

#### `POST /upload`
Upload and process new product data files
```json
{
  "file": "product_data.csv"
}
```

#### `POST /compare`
Compare products between two stores
```json
{
  "store_a": "eroski-01013-01",
  "store_b": "makro-01013-01",
  "list_ids": ["23421704", "22405302"],
  "top_n_results": 5,
  "food_weights": {
    "name_weight": 0.3,
    "brand_weight": 0.2,
    "format_weight": 0.15,
    "quantity_weight": 0.15,
    "ingredient_weight": 0.1,
    "component_weight": 0.1
  }
}
```

#### `GET /nodes_by_label`
Query Neo4j nodes by label
```
/nodes_by_label?label=Product&limit=100
```

#### `POST /nodes/filter`
Filter nodes with complex queries
```json
{
  "nodes": ["Product"],
  "relationships": [{"type": "BELONGS_TO", "direction": "out"}],
  "filters": {"category": "Food"}
}
```

#### `GET /mongodb/product/{siid}`
Retrieve product by store item ID

#### `DELETE /product/{product_hash}`
Delete product and associated data

#### `GET /health`
System health check

## 🧪 Testing

Run tests:
```bash
# All tests
pytest tests/

# Specific test
pytest tests/test_data_extraction.py

# With coverage
pytest --cov=src tests/
```

## 🤖 Machine Learning Models

### Product Similarity Model
Located in `src/models/model_variations/get_similar_products.py`

Features:
- **Multi-category support**: Food, Non-Food Supermarket, Non-Food Electronics
- **Weighted features**: Configurable weights for name, brand, format, quantity, ingredients, components
- **Quality thresholds**: Filters low-quality matches
- **Embedding-based**: Uses semantic embeddings for deep similarity

### Embedding Models
- **Text Embeddings**: Azure OpenAI text-embedding-3-large
- **Sentence Transformers**: For local embeddings
- **Custom Training**: Fine-tuned on supermarket product data

## 🔧 Development

### Code Style
```bash
# Format code
black src/ tests/

# Lint
flake8 src/ tests/

# Type checking
mypy src/
```

### Adding New Preprocessors
1. Create module in `src/preprocessors/`
2. Implement standard interface
3. Add to pipeline configuration
4. Update tests

### Adding New Models
1. Create module in `src/models/`
2. Implement training and inference
3. Export to `src/model_exports/`
4. Integrate with API

## 🐛 Troubleshooting

### Connection Issues
```bash
# Check if services are running
docker-compose ps

# View logs
docker-compose logs neo4j
docker-compose logs mongo

# Restart services
docker-compose restart
```

### Import Errors
```bash
# Ensure virtual environment is activated
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt --upgrade
```

### Memory Issues
- Increase Docker memory allocation (Settings → Resources)
- Process data in smaller batches
- Use pagination for large queries

## 📈 Performance Optimization

- **Batch Processing**: Process data in configurable batch sizes
- **Parallel Execution**: Multi-threaded translation and completion
- **Connection Pooling**: Reuse database connections
- **Caching**: Cache frequently accessed embeddings
- **Indexing**: Ensure proper database indexes

## 🔒 Security

- Store sensitive credentials in `.env` (never commit)
- Use environment-specific configurations
- Implement rate limiting on API endpoints
- Validate all user inputs
- Regular security updates

## 📝 License

[Add your license information here]

## 👥 Contributors

[Add contributor information here]

## 🙏 Acknowledgments

- OpenFoodFacts for product data
- Azure OpenAI for embeddings and completion
- Neo4j for graph database capabilities
- FastAPI for the web framework

## 📞 Support

For issues and questions:
- Create an issue in the repository
- Contact the development team
- Check documentation in `docs/` folder

---

**Last Updated**: December 15, 2025
