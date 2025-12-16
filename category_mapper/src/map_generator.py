# https://gemini.google.com/u/0/app/fbfcbc0b0bf8ac18
import json
import re
import unicodedata

from collections import defaultdict
from deep_translator import GoogleTranslator

from prefect import flow, task
from prefect.artifacts import create_markdown_artifact
from prefect_aws import AwsCredentials
from prefect_aws.s3 import S3Bucket

from sentence_transformers import SentenceTransformer
from sentence_transformers.util import cos_sim # A utility for cosine similarity
from sklearn.metrics import precision_score, recall_score, f1_score

from pathlib import Path
from rich import print
from typing import Union

# --- Configuration ---
SIMILARITY_THRESHOLD = 0.5
WEIGHT_INTERLINGUAL = 0.5
WEIGHT_MONOLINGUAL = 0.5
MODEL_NAME = "intfloat/multilingual-e5-large"
TRANSLATION_CACHE_FILE = Path("translation_cache.json")


@flow(name="generate_category_map", log_prints=True)
def generate_category_map(supermarket_a: str, supermarket_b: str) -> None:
    """
    Flow principal que orquesta la carga, mapeo, evaluación y guardado de categorías.
    """

    s3bucket = S3Bucket.load("s3-report")
    # if inspect.isawaitable(s3bucket):
    #     s3bucket = await s3bucket

    # --- 1. Carga de Datos ---
    # NOTE: debiesemos descargar los archivos o cargarlos en memoria?
    rawproducts_a = s3bucket.read_path(path=f"{supermarket_a}/{supermarket_a}.json")  # type: ignore
    supermarket_a_products = json.loads(rawproducts_a)  # type: ignore

    rawproducts_b = s3bucket.read_path(path=f"{supermarket_b}/{supermarket_b}.json")  # type: ignore
    supermarket_b_products = json.loads(rawproducts_b)  # type: ignore

    # SUPERMARKET_A_FILE = Path(f"input/{supermarket_a}/flattened.json")
    # SUPERMARKET_B_FILE = Path(f"input/{supermarket_b}/flattened.json")
    #
    # with open(SUPERMARKET_A_FILE, "r", encoding="utf-8") as file:
    #     supermarket_a_products = json.load(file)
    #
    # with open(SUPERMARKET_B_FILE, "r", encoding="utf-8") as file:
    #     supermarket_b_products = json.load(file)

    OUTPUT_FILE = Path(f"category_map_{supermarket_a}_{supermarket_b}.json")

    # --- 2. Procesamiento y Mapeo ---
    raw_category_a = extract_unique_categories(supermarket_a_products)
    raw_category_b = extract_unique_categories(supermarket_b_products)

    # print(raw_category_a)
    # print(raw_category_b)

    # Sort the categories to ensure a deterministic order for mapping
    categories_a = sorted(list(raw_category_a))
    categories_b = sorted(list(raw_category_b))
    translated_categories_a = translate_categories(categories_a)

    # Mapping results in original format.
    raw_mapping_results = map_categories(categories_a, categories_b, translated_categories_a)

    # --- 3. Evaluación y Monitoreo (Opcional) ---
    EVALUATION_FILE = Path("evaluation_set.json")
    if EVALUATION_FILE.exists():
        print("[bold blue]Evaluation file found. Running performance evaluation...[/bold blue]")

        with open(EVALUATION_FILE, "r", encoding="utf-8") as f:
            ground_truth = json.load(f)

        # Retrieve metrics by comparing source of truth with current matches
        metrics = evaluate_performance(ground_truth, raw_mapping_results)

        print(f"[bold]Métricas:[/bold] Precisión: {metrics['precision']:.2%}, Recall: {metrics['recall']:.2%}, F1-Score: {metrics['f1_score']:.2f}")

        # Convertir el diccionario de métricas a un string de JSON formateado
        report_json_string = json.dumps(metrics, indent=2, ensure_ascii=False)

        markdown_report = f"""```json
        {report_json_string}
        """

        create_markdown_artifact(
            key="category-mapping-report",
            markdown=markdown_report,
            description=f"Performance metrics for {supermarket_a} to {supermarket_b} mapping."
        )

        print("[bold green]Evaluation complete. Report generated in Prefect UI.[/bold green]")

    # --- 4. Formateo Final y Guardado ---
    formatted_results = []
    for result in raw_mapping_results:
        formatted_results.append({
            "source_category": format_category_as_slug(result["source_category"]),
            "matched_target_category": format_category_as_slug(result["matched_target_category"]),
            "similarity_score": result["similarity_score"]
        })

    # Create a self-documenting output object using the newly formatted results
    final_output = {
        "source_supermarket": supermarket_a,
        "target_supermarket": supermarket_b,
        "mapping_results": formatted_results # Use the formatted list
    }

    # Save OUTPUT
    # with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    #     json.dump(final_output, file, indent=2, ensure_ascii=False)

    # pprint(final_output)
    str_final_output = json.dumps(final_output, indent=2)

    markdown_report = f"""```python
       {str_final_output}
    """
    create_markdown_artifact(
        key="category-mapper",
        markdown=markdown_report,
        description="category map structure between the datasets of both supermarkets"

    )

    encoded_final_output = json.dumps(final_output).encode("utf-8")
    s3bucket.write_path(
        path=str(OUTPUT_FILE),
        content=encoded_final_output
    )

    print(f"\n[bold green]Successfully generated category map and saved to {OUTPUT_FILE}[/bold green]")


@task(name="extract_categories")
def extract_unique_categories(products: list[dict]) -> set[str]:
    """Extracts the set of unique category strings from a list of products."""
    print(f"Extracting categories from {len(products)} products...")

    # FIX: decouple! already had bugs because of this multiple times, this one specifically carries from
    # a change on the category structure when scraping, the parser failed (no validation) and inherit the error here
    # NOTE: This is tightly coupled to eroski/makro scrapers because it looks directly on the item = {} category key 'categoryInSupermarket'
    unique_categories = {product["category"] for product in products if "category" in product}
    print(f"Found {len(unique_categories)} unique categories.")
    return unique_categories


@task(name="translate_categories")
def translate_categories(categories: list[str], source_lang: str = 'en', target_lang: str = 'es') -> list[str]:
    """Traduce una lista de categorías, utilizando un caché en disco para evitar llamadas repetidas."""
    try:
        with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}

    translated_categories = []
    to_translate = []
    for cat in categories:
        if cat in cache:
            translated_categories.append(cache[cat])
        else:
            to_translate.append(cat)
    
    if to_translate:
        print(f"Traduciendo {len(to_translate)} nuevas categorías...")
        try:
            # GoogleTranslator puede manejar listas directamente, lo que es más eficiente.
            new_translations = GoogleTranslator(source=source_lang, target=target_lang).translate_batch(to_translate)
            for original, translated in zip(to_translate, new_translations):
                cache[original] = translated
        except Exception as e:
            print(f"[bold yellow]Warning:[/bold yellow] Fallo en la traducción por lotes: {e}. Volviendo al modo individual.")
            for cat_to_translate in to_translate:
                try:
                    translated_text = GoogleTranslator(source=source_lang, target=target_lang).translate(cat_to_translate)
                    cache[cat_to_translate] = translated_text
                except Exception as inner_e:
                    print(f"[yellow]Warning:[/yellow] No se pudo traducir '{cat_to_translate}': {inner_e}. Usando texto original.")
                    cache[cat_to_translate] = cat_to_translate

    with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    final_list = [cache[cat] for cat in categories]
    print("Traducción completada.")
    return final_list


@task(name="map_categories")
def map_categories(categories_a_en: list[str], categories_b_es: list[str], translated_a_es) -> list[dict]:
    """
    Compares two lists of categories and builds a detailed mapping from A to B
    using a multilingual SentenceTransformer model.
    """

    print("Loading SentenceTransformer model...")

    model = SentenceTransformer(MODEL_NAME)

    print(f"Building map between {len(translated_a_es)} and {len(categories_b_es)} categories...")

    # --- Score 1: Comparación Interlingüística (Inglés vs Español) ---

    # Añadir los prefijos 'query' & 'passages' recomendados por el modelo E5
    queries_en = [f"query: {cat}" for cat in categories_a_en]
    passages_es = [f"passage: {cat}" for cat in categories_b_es]

    # Generate embeddings.
    # No normalization or stopword removal is needed here, as the model understands the full context.
    embeddings_en = model.encode(queries_en, convert_to_tensor=True, show_progress_bar=True)
    embeddings_es = model.encode(passages_es, convert_to_tensor=True, show_progress_bar=True)

    # Calculate Cosine Similarity
    # This creates a matrix where matrix[i, j] is the similarity
    # between category_a[i] and category_b[j].
    score_interlinguistico = cos_sim(embeddings_en, embeddings_es)

    # --- Score 2: Comparación Monolingüística (Español-Traducido vs Español) ---
    queries_es_translated = [f"query: {cat}" for cat in translated_a_es]
    # Reutilizamos los embeddings de `passages_es` que ya calculamos

    embeddings_es_translated = model.encode(queries_es_translated, convert_to_tensor=True, show_progress_bar=True)

    # Calculate Cosine Similarity
    score_monolinguistico = cos_sim(embeddings_es_translated, embeddings_es)

    # --- Puntuación Final Ponderada ---
    # Damos el mismo peso a ambas señales. Puedes ajustar esto.
    similarity_matrix = (WEIGHT_INTERLINGUAL * score_interlinguistico) + (WEIGHT_MONOLINGUAL * score_monolinguistico)

    mapping_results = []
    for i, source_category in enumerate(categories_a_en):
        best_match_index = similarity_matrix[i].argmax()
        best_score = similarity_matrix[i][best_match_index]

        target_category = categories_b_es[best_match_index]

        match_result = {
            "source_category": source_category,
            "matched_target_category": None,
            "similarity_score": round(best_score.item(), 4)
        }

        if best_score >= SIMILARITY_THRESHOLD:
            match_result["matched_target_category"] = target_category
            print(f"MATCH: '{source_category}' -> '{target_category}' (Score: {best_score:.2f})")
        else:
            print(f"NO MATCH: '{source_category}' (Best score was {best_score:.2f} with '{target_category}')")

        mapping_results.append(match_result)

    return mapping_results


@task(name="evaluate_performance")
def evaluate_performance(ground_truth_raw: list[dict], predictions_raw: list[dict]) -> dict:
    """
    Compara las predicciones con un "golden dataset" para calcular métricas de rendimiento.

    Este enfoque híbrido:
    1. Utiliza scikit-learn para el cálculo de métricas agregadas (Precision, Recall, F1).
    2. Utiliza la teoría de conjuntos para generar listas de errores detalladas y precisas (FP, FN).
    """
    print(f"Evaluating performance against {len(ground_truth_raw)} ground truth entries.")

    # --- 1. Preparación de Datos ---
    # Se usan bucles 'for' explícitos para adherirse al estilo de código solicitado.

    # Mapa de búsqueda para la verdad absoluta (ground truth) usando slugs.
    ground_truth_lookup = defaultdict(set)
    for item in ground_truth_raw:
        source_slug = format_category_as_slug(item["source_category"])
        target_slug = format_category_as_slug(item["target_category"])
        ground_truth_lookup[source_slug].add(target_slug)

    # Mapa de las predicciones del modelo, también usando slugs.
    sources_to_evaluate = set(ground_truth_lookup.keys())
    predictions_map = {}
    for item in predictions_raw:
        source_slug = format_category_as_slug(item["source_category"])

        # Solo procesamos la predicción si su fuente está en nuestro set de evaluación.
        if source_slug in sources_to_evaluate:
            target_slug = format_category_as_slug(item["matched_target_category"])

            predictions_map[source_slug] = target_slug

    # --- 2. Clasificación de Predicciones y Generación de Listas ---
    y_true = []
    y_pred = []
    false_positives_list_slug = []

    # Iteramos sobre el ground truth para construir las listas para sklearn y encontrar errores.
    for source_slug, expected_targets_slugs in ground_truth_lookup.items():
        predicted_slug = predictions_map.get(source_slug, None)

        # Añadimos los valores a las listas para el cálculo con sklearn.
        # Se elige un valor "verdadero" representativo si hay múltiples opciones.
        true_label_for_sklearn = list(expected_targets_slugs)[0]
        y_true.append(true_label_for_sklearn)
        y_pred.append(predicted_slug)

        # Identificar Falsos Positivos (lógica consolidada y sin duplicados).
        if predicted_slug is not None and predicted_slug not in expected_targets_slugs:
            false_positives_list_slug.append({
                "source": source_slug,
                "predicted": predicted_slug,
                "expected": list(expected_targets_slugs)
            })

    # --- 3. Cálculo de Falsos Negativos (usando la robusta teoría de conjuntos) ---
    ground_truth_pairs_slug = set()
    for s, targets in ground_truth_lookup.items():
        for t in targets:
            ground_truth_pairs_slug.add((s, t))

    predicted_pairs_slug = set(predictions_map.items())
    fn_set_slug = ground_truth_pairs_slug.difference(predicted_pairs_slug)

    # --- 4. Cálculo de Métricas con Scikit-learn ---
    y_true_str = [str(y) for y in y_true]
    y_pred_str = [str(y) for y in y_pred]
    labels = sorted(list(set(y_true_str + y_pred_str)))

    precision = precision_score(y_true_str, y_pred_str, labels=labels, average='macro', zero_division=0)
    recall = recall_score(y_true_str, y_pred_str, labels=labels, average='macro', zero_division=0)
    f1 = f1_score(y_true_str, y_pred_str, labels=labels, average='macro', zero_division=0)

    # --- 5. Formateo de Reportes con Nombres Originales ---
    all_raw_cats_set = set()
    all_sources_and_targets = ground_truth_raw + predictions_raw
    for item in all_sources_and_targets:
        if item.get("source_category"):
            all_raw_cats_set.add(item["source_category"])

        if item.get("target_category"):
            all_raw_cats_set.add(item["target_category"])

        if item.get("matched_target_category"):
            all_raw_cats_set.add(item["matched_target_category"])

    slug_to_original = {}
    for cat in all_raw_cats_set:
        if cat is not None:
            slug_to_original[format_category_as_slug(cat)] = cat

    fn_list_human = []
    for s, t in fn_set_slug:
        fn_list_human.append({"source": slug_to_original.get(s, s), "expected": slug_to_original.get(t, t)})

    fp_list_human = []
    for d in false_positives_list_slug:
        fp_list_human.append({
            "source": slug_to_original.get(d["source"], d["source"]),
            "predicted": slug_to_original.get(d["predicted"], d["predicted"]),
            "expected": [slug_to_original.get(e, e) for e in d["expected"]]
        })

    # --- 6. Ensamblado del Diccionario Final de Métricas ---
    return {
        "precision": precision, "recall": recall, "f1_score": f1,
        "true_positives": len(predicted_pairs_slug.intersection(ground_truth_pairs_slug)),
        "false_positives": len(fp_list_human),
        "false_negatives": len(fn_list_human),
        "false_positives_list": fp_list_human,
        "false_negatives_list": fn_list_human,
    }


def format_category_as_slug(category: Union[str, None]) -> Union[str, None]:
    """
    Converts a category string into a URL-friendly "slug", preserving slashes
    and transliterating special characters.

    e.g., "Alimentación General > Bebés" -> "alimentacion-general/bebes"
    """
    if category is None:
        return None

    # 1. Convert to lowercase
    text = category.lower()

    # 2. Transliterate special characters to their ASCII equivalent
    # e.g., 'alimentación' -> 'alimentacion'
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')

    # 3. Replace any group of characters that are not a letter, number, or slash with a single hyphen.
    # We also replace spaces and > with / to have a consistent hierarchy
    text = text.replace(' > ', '/') # Specifically handle breadcrumb separator
    text = re.sub(r'[\s_]+', '-', text) # Replace spaces and underscores with hyphens
    text = re.sub(r'[^a-z0-9/-]+', '', text) # Remove any remaining unwanted characters

    # 4. Consolidate multiple hyphens into one, and remove leading/trailing ones
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')

    # 5. Remove leading and trailing '/'
    text = text.strip("/")

    return text


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run or deploy the Eroski scrape workflow.")
    parser.add_argument(
        "--version",
        type=str,
        default="default",
        help="Specify the version for deployment or local execution (e.g., 'v1', 'v2', 'main')."
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy the workflow to Prefect."
    )
    args = parser.parse_args()
    if args.deploy:
        # --- Prefect deployment logic ---
        print("Deploying category mapper flow to Prefect...")
        generate_category_map.deploy(
            name=args.version,
            version=args.version,
            tags=["mapper"],
            work_pool_name=args.version,
            image=f"990187902980.dkr.ecr.us-east-1.amazonaws.com/report:{args.version}_generate_category_map",
            build=True,
            push=True
        )
        print("Deployment command executed. Check your Prefect UI for status.")

    else:
        # --- Local execution logic ---
        print("Running map generator flow locally...")

        # NOTE: for category map generation we need two datasets (flattened) of two different supermarkets
        # this should get the name of {scraper}.json within s3

        # TEST: Manual execution id for testing
        execution_id = "202510161760659152"

        # --- Create and save S3 bucket block ---
        s3bucket = S3Bucket(
            bucket_name="comexsoft-workflows",
            bucket_folder=f"report/{execution_id}",
            credentials=AwsCredentials.load("credentials")  # type: ignore
        )

        # NOTE: Need to overwrite for using the local execution id
        # Need to overwrite for using the local execution id
        s3block = s3bucket.save("s3-report", overwrite=True)

        generate_category_map(
            supermarket_a="eroski",
            supermarket_b="makro"
        )
