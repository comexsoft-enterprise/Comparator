
# Documentación de Bases de Datos

Este documento recoge la información necesaria para entender, mantener y operar las tres bases de datos usadas por el proyecto: MongoDB, Neo4j y PostgreSQL.

**Resumen:**
- **MongoDB**: almacén de documentos usado para los productos y operaciones de ingestión/consulta. (Comparación/insert/update en pipelines).
- **Neo4j**: grafo para relaciones entre productos, embeddings y consultas de similitud (Graph Data Science - GDS).
- **PostgreSQL**: base relacional empleada para consultas analíticas (euclidean distance, joins, etc.) y operaciones que requieren SQL.

**Dónde buscar el código relacionado**:
- **Conectores:** `src/connectors/mongodb_connector.py`, `src/connectors/neo4j_connector.py`, `src/connectors/postgresql_connector.py`.
- **Configuración central y valores por defecto:** `config/settings.py`.
- **Docker / despliegue local:** `stack/docker-compose.yml` y carpetas `stack/neo4j`, `stack/postgres`, `stack/mongodb` (si existen).
- **Esquemas & datos de ejemplo:** `data/schemas/` dentro del proyecto.

Información de despliegue local (archivo `stack/docker-compose.yml`):

- `mongo` (imagen `mongo:latest`): expone `27017`. Credenciales por defecto en compose: `MONGO_INITDB_ROOT_USERNAME=admin`, `MONGO_INITDB_ROOT_PASSWORD=secretpass`.
- `mongo-express` expone UI en `8081` con credenciales `meuser`/`mepass` (según compose).
- `neo4j` (imagen `neo4j:latest`): expone `7474` (HTTP) y `7687` (Bolt). En compose la variable `NEO4J_AUTH` está establecida en `neo4j/testpass`.
- `postgres` (imagen `postgres:latest`): expone `5432` y por defecto en compose `POSTGRES_USER=admin`, `POSTGRES_PASSWORD=secretpass`, `POSTGRES_DB=myappdb`.
- `pgadmin` disponible en `5050` según compose.

Estos valores son adecuados para desarrollo local pero deben ser reemplazados en entornos reales (usar `.env` o un gestor de secretos).

**1) MongoDB**

- **Propósito:** almacenamiento principal de documentos de producto; usado por los procesos de ingestion/validación y por tests/pipelines.
- **Variables de entorno / config:** consultadas desde `config/settings.py`:
	- `MONGO_URI` (por defecto: `localhost:27017` en `MONGO_CONFIG['uri']`)
	- `MONGO_USER1` (por defecto: `admin`)
	- `MONGO_USER1_PASSWORD` (por defecto: `secretpass`)
	- `MONGO_DATABASE` (por defecto: `test`)
- **Conector:** `src/connectors/mongodb_connector.py`.
	- Funciones útiles: `get_mongo_client(...)` y `diagnostic_get_mongo_client(...)` (esta última imprime/redacta URI y fuerza `ping`).

Comandos de diagnóstico / ejemplo:

```bash
# Ping rápido usando el helper diagnóstico
python - <<'PY'
from src.connectors.mongodb_connector import diagnostic_get_mongo_client
db = diagnostic_get_mongo_client()
print('Collections:', list(db.list_collection_names()))
PY
```

Backup / restore (ejemplos):

```bash
# Dump
mongodump --uri "mongodb://$MONGO_USER1:$MONGO_USER1_PASSWORD@$MONGO_URI/$MONGO_DATABASE" --out /tmp/mongodump_$(date +%F)

# Restore
mongorestore --uri "mongodb://$MONGO_USER1:$MONGO_USER1_PASSWORD@$MONGO_URI/" /tmp/mongodump_YYYY-MM-DD/$MONGO_DATABASE
```

Buenas prácticas: índices en campos de búsqueda (p. ej. product id, store id), usar perfiles de replicación/replica sets para alta disponibilidad y limitar tamaños de documentos.

**2) Neo4j**

- **Propósito:** grafo de productos/tiendas/relaciones; almacenamiento de embeddings y consultas de similitud (se integra con OpenAI/embeddings). Puede usar GDS.
- **Variables de entorno / config:** definidas en `config/settings.py` (mapa `NEO4J_CONFIG`):
	- `NEO4J_URI` (por defecto: `54.237.55.135:7687`)
	- `NEO4J_USER` (por defecto: `neo4j`)
	- `NEO4J_PASSWORD`
	- `NEO4J_DATABASE` (por defecto: `neo4j`)
- **Conector:** `src/connectors/neo4j_connector.py`.
	- Clase `Neo4jConnector` con `get_neo4j_driver(...)`, `connect_to_neo4j()`, `test_connection()` y `execute_query(...)`.

Diagnóstico / ejemplo:

```bash
python - <<'PY'
from src.connectors.neo4j_connector import Neo4jConnector
nc = Neo4jConnector()
ok = nc.connect_to_neo4j()
print('conectado?', ok)
print('test_connection:', nc.test_connection())
nc.close_connection()
PY
```

Backup / restore (ejemplo usando el contenedor Docker — hay un script de ejemplo en el repo):

```bash
# Dentro del host que corre el contenedor neo4j
docker exec neo4j neo4j-admin database dump neo4j --to-path=/var/lib/neo4j/backups
docker cp neo4j:/var/lib/neo4j/backups/neo4j.dump /tmp/neo4j.dump

# Para restaurar (con contenedor detenido / nuevo contenedor)
docker cp /tmp/neo4j.dump neo4j:/var/lib/neo4j/backups/
docker exec neo4j neo4j-admin database load neo4j --from-path=/var/lib/neo4j/backups
```

Notas operativas:
- Si se usa GDS, asegúrate de instalar el plugin y permitir procedimientos en `neo4j.conf` (`dbms.security.procedures.unrestricted=gds.*`). El repo contiene un script `src/models/category_analysis/downgrade_neo4j.sh` con pasos útiles.
- Mantener índices y restricciones para las propiedades que se consultan frecuentemente (ej.: `product_id`, `store_id`).

**3) PostgreSQL**

- **Propósito:** consultas relacionales y cálculo de distancias euclídeas para análisis (módulos en `src/models/postgresql_analysis`).
- **Variables de entorno:** usadas por `src/connectors/postgresql_connector.py`:
	- `POSTGRES_HOST`
	- `POSTGRES_PORT`
	- `POSTGRES_DB`
	- `POSTGRES_USER`
	- `POSTGRES_PASSWORD`
- **Conector:** `src/connectors/postgresql_connector.py`.
	- Funciones: `get_connection_pool(minconn, maxconn)`, `get_pooled_connection(...)`, `get_postgresql_connection()`, `get_db_config()`.
	- Nota: en contextos multihilo el código opta por abrir conexiones directas; existe riesgo de `too many clients` si no se cierran conexiones — ver sección resolución de problemas.

Diagnóstico / ejemplo:

```bash
python - <<'PY'
from src.connectors.postgresql_connector import get_db_config, get_postgresql_connection
print('DB config:', get_db_config())
conn = get_postgresql_connection()
cur = conn.cursor()
cur.execute('SELECT version();')
print(cur.fetchone())
cur.close(); conn.close()
PY
```

Backup / restore:

```bash
# Dump
pg_dump -h $POSTGRES_HOST -p $POSTGRES_PORT -U $POSTGRES_USER -F c -b -v -f /tmp/db.dump $POSTGRES_DB

# Restore
pg_restore -h $POSTGRES_HOST -p $POSTGRES_PORT -U $POSTGRES_USER -d $POSTGRES_DB -v /tmp/db.dump

# Si se usa docker
docker exec -t postgres_container pg_dump -U $POSTGRES_USER $POSTGRES_DB > /tmp/db.sql
docker exec -i postgres_container psql -U $POSTGRES_USER -d $POSTGRES_DB < /tmp/db.sql
```

Consideraciones y mantenimiento
- **Secretos:** No almacenar credenciales en el repo. Usar `.env` para desarrollo y un gestor de secretos en producción.
- **Conexiones y pool:** ajustar `minconn/maxconn` en `get_connection_pool()` para evitar `FATAL: sorry, too many clients already`. Cerrar conexiones tras su uso.
- **Monitoreo:** habilitar métricas/healthchecks: `pg_stat_activity` para Postgres, `db.stats()` y logs para MongoDB, logs + GDS checks para Neo4j.
- **Migraciones:** el repo no contiene una herramienta de migraciones SQL estándar; recomendamos usar `alembic` o aplicar scripts SQL versionados en `migrations/` si se agrega.

Resolución rápida de problemas comunes
- **Postgres "too many clients":** revisar `SELECT count(*) FROM pg_stat_activity;`, reducir concurrencia o aumentar `max_connections` en `postgresql.conf`, y/o usar mejor pooling. Asegurar que cada `connect()` termina en `close()`.
- **MongoDB conexión fallida:** usar `diagnostic_get_mongo_client()` para ver URI redactado y probar `mongosh` con las mismas credenciales.
- **Neo4j conectividad:** ejecutar `neo4j-admin` dentro del contenedor o revisar `docker logs neo4j`. Si se usa GDS, comprobar que el plugin está presente en `plugins/`.

Referencias en el código
- **MongoDB connector:** `src/connectors/mongodb_connector.py`
- **Neo4j connector:** `src/connectors/neo4j_connector.py` (método `test_connection()` devuelve conteos/labels disponibles)
- **Postgres connector:** `src/connectors/postgresql_connector.py` (pool y helpers)

**Checklist operativo (playbook) — Resumen rápido**

- Daily backups:
	- MongoDB: ejecutar `mongodump` diario de la base completa y rotar los backups 7-30 días.
		- Ejemplo: `mongodump --uri "mongodb://$MONGO_USER1:$MONGO_USER1_PASSWORD@$MONGO_URI/$MONGO_DATABASE" --out /backups/mongo/$(date +%F)`
	- PostgreSQL: `pg_dump` en formato comprimido (custom) cada noche.
		- Ejemplo: `pg_dump -h $POSTGRES_HOST -p $POSTGRES_PORT -U $POSTGRES_USER -F c -b -v -f /backups/postgres/db_$(date +%F).dump $POSTGRES_DB`
	- Neo4j: `neo4j-admin database dump neo4j` o snapshot del volumen del contenedor.
		- Ejemplo: `docker exec neo4j neo4j-admin database dump neo4j --to-path=/var/lib/neo4j/backups` y `docker cp neo4j:/var/lib/neo4j/backups/neo4j.dump /backups/neo4j/`.

- Restore / Recovery (rápido):
	- MongoDB: `mongorestore --uri "mongodb://user:pass@host/" /path/to/dump`.
	- PostgreSQL: `pg_restore -h host -p port -U user -d db /path/to/db.dump`.
	- Neo4j: copiar `.dump` al contenedor y `neo4j-admin database load` (ver sección anterior).

- Migrations:
	- Para Postgres: recomendamos introducir `alembic` y mantener un directorio `migrations/` con scripts versionados.
	- Para Neo4j/MongoDB: versionar scripts de ingestión y transformation en `src/ingestion/` y guardar ejemplos de CSV en `data/processed/versioned/`.

- Monitoring & Healthchecks:
	- Postgres: usar `pg_stat_activity`, `pg_stat_database`, y un exporter para Prometheus (`postgres_exporter`).
	- MongoDB: habilitar monitoring (MongoDB Cloud / MMS) o usar `mongostat`/`mongotop`; exponer métricas y logs.
	- Neo4j: revisar `debug.log` y usar `neo4j-admin` y métricas expuestas por el contenedor.

- Runbook para problemas comunes:
	- Postgres `too many clients`: listar `pg_stat_activity`, identificar conexiones largas, reiniciar pool o aumentar `max_connections`, y revisar código para cerrar conexiones.
	- MongoDB conexión: validar `MONGO_URI`, ejecutar `mongosh` con credenciales; si falla, revisar logs del contenedor.
	- Neo4j errores con GDS: comprobar versión de Neo4j y compatibilidad del plugin; reinstalar plugin en `plugins/` y permitir procedimientos.

He completado la recopilación y el borrador de la documentación. Queda revisar y perfilar el documento final, añadir playbooks/scripts concretos si lo deseas, y crear scripts reproducibles en `scripts/`.

Esquemas, tablas y flujo de ingestión (resumen)

- MongoDB:
	- Esquema: documentos flexibles derivados de CSV procesados; no hay un esquema fijo, pero los procesos esperan campos clave como `uuid`, `product_hash`, `siid`, `price`, campos nutricionales, `measure_value`, `unit_measure`, etc.
	- Colecciones: cada CSV generalmente se inserta en una colección con nombre igual al fichero (`Path(csv).stem`).
	- ID único por defecto: campo `uuid` (o `uuid`/`product_hash` según el preprocesador). El ingestor hace `find_one({id_field: doc_id})` y `insert_one` o `replace_one`.
	- Índices recomendados: `uuid`, `product_hash` (siempre que se haga upsert), `siid`, y cualquier campo usado para búsquedas frecuentes.

- Neo4j:
	- Esquema: nodos `Product`, `Store`, `Internal_Category`, `Internal_Type`, con relaciones `SELLS`, `SIMILAR_TO` u otras según `src/ingestion/nodes_relationships.py`.
	- Ingesta: CSV limpiados y convertidos a dicts; procesados en batches por `Neo4jNodesRelationshipsManager.process_products_batch(session, batch_data)` para crear nodos/relaciones.
	- Índices/constraints: el ingestor ejecuta `create_constraints_and_indexes()` antes de la ingestión para optimizar performance; asegurar índices en `product_hash`, `siid`, y claves de búsqueda.

- PostgreSQL:
	- Tabla principal: `product_vector_data` (creada automáticamente por `VectorDataExtractor.create_table_if_not_exists`).
	- Columnas: `id SERIAL PRIMARY KEY`, `uuid TEXT`, `product_hash TEXT`, `siid TEXT`, + dinámicas para medidas, nutrición y price (numéricos/texto). También `price_history JSONB`, `created_at`, `updated_at`.
	- Índices: índice único en `product_hash` (`uq_product_hash`) y no-únicos en `uuid` y `siid` para búsquedas.
	- Ingesta: el proceso extrae columnas relevantes de CSV (`uuid`, `siid`, `product_hash`, columnas con `nutri` y `price`, `measure_value`, `unit_measure`), crea la tabla si hace falta, añade columnas faltantes y realiza inserts/updates (upserts) usando `psycopg2.extras.execute_batch`.

	**Detalle Estructuras (extraído de `data/schemas/column_registry.json`)**

	**Nodos (columnas con `representation: node`)**
	- **Brand**: `brand`
	- **Internal_Type**: `internaltype`
	- **Internal_Category**: `internalcategory`
	- **Internal_Subcategory**: `internalsubcategory`
	- **Format**: `format`
	- **Quantity**: `quantity`
	- **Company**: `company`
	- **Store**: `store`
	- **Country**: `country`
	- **Offer**: `offer`

	**Propiedades por Nodo (columnas con `representation: node_property`)**
	- **Product**: `ean`, `gtin`, `product_name`, `description`, `description_synthesized`, `unit_measure`, `measure_value`, `uuid`, `siid`, `product_hash`, `colour`, `model`, `specifications`, `documents_safety_instructions`
	- **Store**: `postcode`
	- **Product (otras propiedades desde registry)**: `unit_measure`, `measure_value`, `model`, `colour`, `specifications`

	**Relaciones (columnas que representan relaciones/targets, `representation: relationship`)**
	- **OTHER_INGREDIENT / Ingredients**: `ingredients`
	- **OTHER_COMPONENT / Components**: `components`
	- **FIRST_LEVEL_COMPONENT**: `first_level_components`
	- **SECOND_LEVEL_COMPONENT**: `second_level_components`
	- **ALLERGENS**: `allergens`

	**Propiedades por Relación (columnas con `representation: relationship_property`)**
	- **SELLS (Store -> Product)**: `price`, `unit_price`, `price_with_offer`, `shipping_cost`, `seller`, `days_of_shipping`, `url`, `image_list`, `reviews`, `general_characteristics`, `conservation_characteristics`, `additional_information_more_information`, many `offer_price_*` and `percent_promotion_*` fields, y multitud de `nutrition_information_*` campos (estos últimos suelen agruparse bajo la relación `SELLS`).
	- **IS_SOLD_IN_COUNTRY / IS_IN_COUNTRY**: `currency`, `vat`
	- **FROM_BRAND**: `manufacturer`, `manufacturer_name`, `manufacturer_address`, `instalation_cost`

	Nota: la registry contiene muchas columnas opcionales (campos `offer_price_*`, `percent_promotion_*`, `nutrition_information_*`, etc.) que se asignan mayoritariamente a propiedades de la relación `SELLS` y otras relaciones comerciales. Para una extracción completa y actualizada, inspeccionar `data/schemas/column_registry.json` directamente.

	Ejemplo rápido (ejemplo esquemático de cómo el ingestor crea nodos/relaciones):

	1) Payload (fila CSV procesada -> dict) simplificado:

	```
	{
		"product_hash": "hash-123",
		"uuid": "uuid-abc",
		"siid": "siid-xyz",
		"product_name": "Leche entera 1L",
		"brand": "MarcaX",
		"price": 1.25,
		"unit_price": 1.25,
		"currency": "EUR",
		"country": "ES",
		"ingredients": "Leche",
		"allergens": "Milk",
		"store": "Mercado-01001-Centro",
		"company": "Mercado",
	}
	```

	2) Cypher UNWIND (simplified) that the manager effectively runs in batch (conceptual):

	```
	UNWIND $batch AS row
	MERGE (p:Product {product_hash: row.product_hash})
	SET p.product_name = coalesce(row.product_name, p.product_name), p.brand = coalesce(row.brand, p.brand)
	MERGE (s:Store {name: row.store})
	MERGE (c:Company {name: row.company})
	MERGE (s)-[r:SELLS]->(p)
	SET r.price = coalesce(row.price, r.price), r.currency = coalesce(row.currency, r.currency)
	```

	3) Relación con ingredientes y alérgenos (ejemplo conceptual):

	```
	MERGE (i:Ingredient {name: 'Leche'})
	MERGE (p)-[r:ALLERGENS]->(i)
	```

	Regenerar las listas automáticamente
	- Si se quiere regenerar estas listas en tu máquina, ejecuta un pequeño script Python que lea `data/schemas/column_registry.json` y agrupe por `representation` y `belongs_to`. Ejemplo rápido (ejecutar en el root del repo):

	```bash
	python - <<'PY'
	import json
	from pathlib import Path
	obj = json.loads(Path('data/schemas/column_registry.json').read_text(encoding='utf-8'))
	from collections import defaultdict
	group = defaultdict(list)
	for col, defs in obj.items():
			group[(defs.get('representation'), defs.get('belongs_to'))].append(col)
	for k, v in group.items():
			print(k, sorted(v))
	PY
	```

	--

	Fin de la sección de estructuras extraídas.

Scripts / módulos relevantes:
- `src/preprocessors/data_formatting/product_verification.py`: lógica que decide si un producto es nuevo/modificado/igual y actualiza MongoDB; prepara CSVs para la ingestión a Neo4j/Postgres.
- `src/ingestion/mongodb_ingest.py`: `MongoDBCSVIngestor` con `process_csv_file`, `ingest_all_csv_files`, `create_indexes`.
- `src/ingestion/neo4j_ingest.py`: `Neo4jCSVIngestor` con `ingest_csv_file`, `ingest_all_csv_files` y uso de `nodes_relationships` para insertar en batch.
- `src/nutritional_price_info_extraction/postgresql_data_extraction.py`: `VectorDataExtractor` y `ingest_verified_csv()` para crear/actualizar la tabla `product_vector_data`.



