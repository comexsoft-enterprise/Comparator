# This script is responsible for cleaning and preprocessing the product data from both Eroski and Makro.
# It normalizes text, removes stopwords, and separates products into two main categories:
# those with a recognized brand and those without (generic or white-label).
# The processed data is then saved into new JSON files.

import json
import nltk
import re
import unicodedata

from collections import defaultdict
# from nltk.corpus import stopwords

from pathlib import Path
from prefect import flow
from prefect.artifacts import create_progress_artifact, update_progress_artifact, create_markdown_artifact
from prefect_aws import AwsCredentials
from prefect_aws.s3 import S3Bucket
# from prefect.cache_policies import NO_CACHE
# from prefect.deployments import run_deployment

from sentence_transformers import SentenceTransformer

from rich import print as pprint
from typing import Any


MODEL_NAME = "jaimevera1107/all-MiniLM-L6-v2-similarity-es"
WHITE_LABELS_FILE = Path("white_label_brands.json")


@flow(name="tokenizer", log_prints=True, retries=3)
def tokenizer(execution_id: str, supermarket_a: str, supermarket_b: str) -> None:
    """
    execution_id: unique execution id
    supermarket_a: name of first supermarket
    supermarket_b: name of second supermarket
    """

    tokenizer_s3bucket = S3Bucket(
        bucket_name="comexsoft-workflows",
        bucket_folder=f"report/{execution_id}/tokenizer",
        credentials=AwsCredentials.load("credentials-block")  # type: ignore
    )
    s3block = tokenizer_s3bucket.save("s3-tokenizer", overwrite=True)

    # === Blocks configurations ===
    s3bucket = S3Bucket.load("s3-report")
    # if inspect.isawaitable(s3bucket):
    #     s3bucket = await s3bucket


    # ========== Main Settings and Data Loading ==========

    # Load a list of white-label or generic brands from a JSON file.
    # These brands will be trated as "NO BRAND" during the processing.
    with open(WHITE_LABELS_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)
    white_label_brands: list = data["general"]

    spanish_stopwords = download_spanish_stopwords(white_label_brands)

    # Define categories that trigger inclusion of raw ingredients
    # food_categories = ['frescos', 'fresh']

    # ========== Text Processing Functions ==========

    # Load the SentenceTransformer model for creating embeddings.
    model = SentenceTransformer(MODEL_NAME)
    # model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    # A global counter to track how many products are found in each category.
    # categories_counter = {}
    categories_counter = defaultdict(int)

    raw_supermarket_a = s3bucket.read_path(path=f"{supermarket_a}/{supermarket_a}.json")
    supermarket_a_products = json.loads(raw_supermarket_a)

    raw_supermarket_b = s3bucket.read_path(path=f"{supermarket_b}/{supermarket_b}.json")
    supermarket_b_products = json.loads(raw_supermarket_b)

    str_category_mappings = s3bucket.read_path(path=f"category_map_{supermarket_a}_{supermarket_b}.json")
    raw_category_mappings: list[dict[str, Any]] = json.loads(str_category_mappings)

    # SUPERMARKET_A_FILE = Path(f"input/{supermarket_a}/{supermarket_a}.json")
    # SUPERMARKET_B_FILE = Path(f"input/{supermarket_b}/{supermarket_b}.json")

    # with open(SUPERMARKET_A_FILE, "r", encoding="utf-8") as file:
    #     supermarket_a_products = json.load(file)

    # with open(SUPERMARKET_B_FILE, "r", encoding="utf-8") as file:
    #     supermarket_b_products = json.load(file)

    # Create output directories locally if they don't exist
    RESULTS_DIR = Path("results")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    SUPERMARKET_A_DIR = Path(f"results/{execution_id}/{supermarket_a}")
    SUPERMARKET_A_DIR.mkdir(parents=True, exist_ok=True)

    SUPERMARKET_B_DIR = Path(f"results/{execution_id}/{supermarket_b}")
    SUPERMARKET_B_DIR.mkdir(parents=True, exist_ok=True)

    # raw_category_mappings: dict[str, Any] = s3bucket.read_path(path=f"category_map_{supermarket_a}_{supermarket_b}.json")
    # with open(f"category_map_{supermarket_a}_{supermarket_b}.json", "r", encoding="utf-8") as file:
    #     raw_category_mappings: dict[str, Any] = json.load(file)

    # category_mappings = normalize_category_mappings(category_mappings, spanish_stopwords)
    category_mappings: list[dict[str, Any]] = raw_category_mappings.get("mapping_results", [])

    print("Normalizing category mappings...")
    normalized_mappings = []
    for mapping in category_mappings:
        source_cat = mapping.get("source_category")
        target_cat = mapping.get("matched_target_category")

        if source_cat and target_cat:
            normalized_mappings.append({
                "source": normalize_spanish_text(source_cat, spanish_stopwords),
                "target": normalize_spanish_text(target_cat, spanish_stopwords)
            })

    progress_artifact_id = create_progress_artifact(
        progress=0.0,
        description="Indicates the category brand/nobrand matching progress.",
    )
    total = len(normalized_mappings)

    # Initialize a list to hold progress data for the artifact

    for idx, mapping in enumerate(normalized_mappings, start=1):
        supermarket_a_cats = [mapping.get("source")]
        supermarket_b_cats = [mapping.get("target")]

        # if not supermarket_a_category or not supermarket_b_category:
        #     continue

        # supermarket_a_cats = [normalize_spanish_text(supermarket_a_category, spanish_stopwords)]
        # supermarket_b_cats = [normalize_spanish_text(supermarket_b_category, spanish_stopwords)]

        update_progress_artifact(artifact_id=progress_artifact_id, progress=(idx / total) * 100)

        # Process products
        processed_a_nobrand, processed_a_brand = process_products(supermarket_a_products, supermarket_a_cats, spanish_stopwords, white_label_brands, model, categories_counter)
        processed_b_nobrand, processed_b_brand = process_products(supermarket_b_products, supermarket_b_cats, spanish_stopwords, white_label_brands, model, categories_counter)

        # Use the same normalized category name for consistent file naming
        supermarket_a_cat_name = supermarket_a_cats[0] if supermarket_a_cats else "all"
        # _  = "_".join(supermarket_b_cats)  if supermarket_b_cats else "all"  # supermarket_b_cat_name

        # NOTE: Es necesario que coincidan en nombre makro y eroski para hacer luego las comparaciones sin gastar tantos recursos
        # TODO: aggregate category results and write to small number of files (I/O optimization & better testing)
        # meanwhile use local if possible

        # Save Eroski NO BRAND
        supermarket_a_filename_nobrand_s3 = f"{supermarket_a}/{supermarket_a}_{supermarket_a_cat_name}_no_brand_processed_products_modified.json"
        encoded_supermarket_a_nobrand_products = json.dumps(processed_a_nobrand).encode("utf-8")
        tokenizer_s3bucket.write_path(path=supermarket_a_filename_nobrand_s3, content=encoded_supermarket_a_nobrand_products)

        # supermarket_a_filename_nobrand = f"results/{execution_id}/{supermarket_a}/{supermarket_a}_{supermarket_a_cat_name}_no_brand_processed_products_modified.json"
        # with open(supermarket_a_filename_nobrand, "w", encoding="utf-8") as file:
        #     json.dump(processed_a_nobrand, file, ensure_ascii=False, indent=4)

        # Save Eroski BRAND
        supermarket_a_filename_brand_s3 = f"{supermarket_a}/{supermarket_a}_{supermarket_a_cat_name}_brand_processed_products_modified.json"
        encoded_eroski_brand_products = json.dumps(processed_a_brand).encode("utf-8")
        tokenizer_s3bucket.write_path(path=supermarket_a_filename_brand_s3, content=encoded_eroski_brand_products)

        # supermarket_a_filename_brand = f"results/{execution_id}/{supermarket_a}/{supermarket_a}_{supermarket_a_cat_name}_brand_processed_products_modified.json"
        # with open(supermarket_a_filename_brand, "w", encoding="utf-8") as file:
        #     json.dump(processed_a_brand, file, ensure_ascii=False, indent=4)

        # Save Makro NO BRAND
        supermarket_b_filename_nobrand_s3 = f"{supermarket_b}/{supermarket_b}_{supermarket_a_cat_name}_no_brand_processed_products_modified.json"
        encoded_supermarket_b_nobrand_products = json.dumps(processed_b_nobrand).encode("utf-8")
        tokenizer_s3bucket.write_path(path=supermarket_b_filename_nobrand_s3, content=encoded_supermarket_b_nobrand_products)

        # supermarket_b_filename_nobrand = f"results/{execution_id}/{supermarket_b}/{supermarket_b}_{supermarket_a_cat_name}_no_brand_processed_products_modified.json"
        # with open(supermarket_b_filename_nobrand, "w", encoding="utf-8") as file:
        #     json.dump(processed_b_nobrand, file, ensure_ascii=False, indent=4)

        # Save Makro BRAND
        supermarket_b_filename_brand_s3 = f"{supermarket_b}/{supermarket_b}_{supermarket_a_cat_name}_brand_processed_products_modified.json"
        encoded_supermarket_b_brand_products = json.dumps(processed_b_brand).encode("utf-8")
        tokenizer_s3bucket.write_path(path=supermarket_b_filename_brand_s3, content=encoded_supermarket_b_brand_products)

        # supermarket_b_filename_brand = f"results/{execution_id}/{supermarket_b}/{supermarket_b}_{supermarket_a_cat_name}_brand_processed_products_modified.json"
        # with open(supermarket_b_filename_brand, "w", encoding="utf-8") as file:
        #     json.dump(processed_b_brand, file, ensure_ascii=False, indent=4)

        print(f"Saved results for {supermarket_a} cats {supermarket_a_cats} and {supermarket_b} cats {supermarket_b_cats}")

    pprint(categories_counter)
    str_categories_counter = json.dumps(categories_counter, indent=2)

    markdown_report = f"""```python
       {str_categories_counter}
    """
    create_markdown_artifact(
        key="categories-report",
        markdown=markdown_report,
        description="Products per category when tokenized"

    )

    OUTPUT_DIR = Path(f"results/{execution_id}")
    OUTPUT_FILENAME = f"tokenizer_{supermarket_a}_{supermarket_b}_results_per_category.json"

    encoded_categories_counter = json.dumps(categories_counter).encode()
    tokenizer_s3bucket.write_path(
        path=OUTPUT_FILENAME,
        content=encoded_categories_counter
    )

    with open(OUTPUT_DIR / OUTPUT_FILENAME, "w", encoding="utf-8") as file:
        json.dump(categories_counter, file, ensure_ascii=False, indent=2)


def download_spanish_stopwords(white_label_brands: list) -> set:
    nltk.download("stopwords")
    stopwords = nltk.corpus.stopwords

    # Load the standard Spanish stopwords from the NLTK library.
    spanish_stopwords = set(stopwords.words("spanish"))

    # Add the white-label brands to the stopwords list to ensure they are removed from product names.
    spanish_stopwords.update(word for x in white_label_brands for word in x.split(' '))

    # Add custom words to the stopwords list that are common in product descriptions but don't add value for matching.
    # NOTE should I add measuring units?
    # spanish_stopwords.update(["bandeja", "extra", "unidades", "kg", "gr", "g", "l", "ml"])
    spanish_stopwords.update(["bandeja", "extra", "unidades"])

    # Add all single letters to stopwords to remove them.
    spanish_stopwords.update(list("qwertyuiopasdfghjklzxcvbnmñ"))
    return spanish_stopwords


# DEPRECATED
def normalize_category_mappings(category_mappings, stopwords: set):
    """
    Normalize all elements in category_mappings using normalize_spanish_text().
    """
    normalized_mappings = []
    for source_list, target_list in category_mappings:
        normalized_source = [normalize_spanish_text(category, stopwords) for category in source_list]
        normalized_target = [normalize_spanish_text(category, stopwords) for category in target_list]
        normalized_mappings.append((normalized_source, normalized_target))
    return normalized_mappings


def normalize_spanish_text(text: str, spanish_stopwords: set) -> str:
    """
    Normalize Spanish text by:
      1. Lowercasing.
      2. Removing accents/diacritics.
      3. Removing punctuation (including ¿ and ¡).
      4. Collapsing multiple spaces.
      5. Remove numbers

    :param text: The Spanish text to be normalized.
    :return: The normalized text.
    """
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')  # remove accents
    text = re.sub(r'[^\w\s]', '', text)  # remove punctuation
    text = re.sub(r'\S*\d+\S*', '', text)
    text = re.sub(r'\s+', ' ', text).strip()  # collapse extra spinach-chard-and-others

    # NOTE: Check if the results of similarities are best with removing stopwords
    # Filters out any word that is in the spanish_stopwords set.
    # remove_stopwords() in original tokenizer
    # words = text.split()
    # filtered_words = [word for word in words if word not in spanish_stopwords]
    # text = ' '.join(filtered_words)

    return text


# TODO: any way to improve readability of this function?
def process_products(products: list, categories: list,  stopwords: set, white_label_brands:list, model: SentenceTransformer, counter: dict) -> tuple[list, list]:
    """
    Process a list of products by filtering, classifying brands, generate embeddings, and
    separating them, marking brands as NO BRAND if they appear only once or match white-label patterns.

    :param products: List of product dictionaries.
    :param categories: List of category keywords to match. If empty, all products are processed.
    :param stopwords: Set of stopwords for text normalization.
    :param white_label_brands: List of white-label brand patterns
    :param model: SentenceTransformer model for embeddings.
    :param counter: A dictionary to count products per category.
    :return: A tuple of two lists: (marca_blanca_products, marca_products)
        - marca_blanca = White label or brand with only 1 occurrence.
        - marca = All other brands with > 1 occurrence.
    """

    # Filter products by the specified categories
    filtered_products = _filter_products_by_category(products, categories, stopwords, counter)
    # print(filtered_products)

    # Count and classify all brands in the filtered list
    brand_classification_map = _classify_brands(filtered_products, white_label_brands)
    print(brand_classification_map)

    # Generate embeddings and assign the final brand name to each product
    processed_products = _process_and_embed_products(filtered_products, brand_classification_map, model, stopwords)

    # Separate products into brand and no_brand lists
    marca_blanca = []
    marca = []
    for product in processed_products:
        if product.get("brand", "NO BRAND") == "NO BRAND":
            marca_blanca.append(product)
        else:
            marca.append(product)

    return marca_blanca, marca


def _filter_products_by_category(products: list, categories: list, stopwords: set, counter: defaultdict):
    """
    Filters products against a list of category patterns and counts them.

    This function iterates through a list of products, normalizes each product's
    category, and checks if it matches any of the provided category patterns.
    Matching products are added to a list and counted.

    Args:
        products: A list of product dictionaries.
        categories: A list of category strings to match against.
        stopwords: A set of words to remove during text normalization.
        counter: A dictionary to store the counts of products in each category.

    Returns:
        A new list containing only the products that match the categories.
    """

    filtered_products = []

    # If category is empty return all products
    if not categories:
        return products

    for product in products:
        # FIX: deeply coupled to spider because of key reference

        # Normalize product's category for a consistent comparison format
        raw_category = product.get("category", "")
        product_cat = normalize_spanish_text(raw_category, stopwords).replace(" ", "")

        # Check if the normalized category starts with any of the filter patterns.
        # This is efficient as 'any()' stops as soon as a match is found
        if any(re.match(pattern, product_cat, re.IGNORECASE) for pattern in categories):
            # The product matches a category; add it to our results
            filtered_products.append(product)
            # counter[product_cat] = counter.get(product_cat, 0) + 1  # counter[product_cat] += 1 (need to use defaultdict)
            counter[product_cat] += 1

    return filtered_products


def _classify_brands(products: list, white_label_brands: list) -> dict[str, str]:
    """
    Counts brand occurrences and classifies them as 'NO BRAND' if they
    are a white-label or appear only once.

    Args:
        products: The filtered list of product dictionaries.
        white_label_brands: A list of white-label brand patterns to check against.

    Returns:
        A classification dictionary that maps each original brand name
        to its final, classified name (e.g., {"BrandX": "NO BRAND", "BrandY": "BrandY"}).
    """
    brand_counts = defaultdict(int)

    # First, iterate through products to count occurences of each brand.
    for product in products:
        brand = str(product.get("brand", "NO BRAND"))

        # if the brand is empty or matches a white-label pattern, treat it as "NO BRAND"
        is_white_label = not brand or any(
            re.search(re.escape(brand), pattern, re.IGNORECASE)
            for pattern in white_label_brands
        )

        final_brand = "NO BRAND" if is_white_label else brand
        # brand_counts[final_brand] = brand_counts.get(final_brand, 0) + 1  # brand_counts[final_brand] += 1 (need to use defaultdict)
        brand_counts[final_brand] += 1

    # Now, build the final classification map.
    # A brand is "NO BRAND" if its count is 1 or it was already classified as such
    # brand_classification = {
    #     brand: "NO BRAND" if count <= 1 or brand == "NO BRAND" else brand
    #     for brand, count in brand_counts.items()
    # }

    # TEST this should effectively remove the lonely brand from matching? apparently it works better!! (~5%precision)
    brand_classification = {
        brand: "NO BRAND" if brand == "NO BRAND" else brand
        for brand, _ in brand_counts.items()
    }
    return brand_classification


def _process_and_embed_products(products: list, brand_classification: dict, model: SentenceTransformer, stopwords: set) -> list:
    """
    Generates concatenation string and embedding for each product.

    This function updates each product with its final, classified brand name,
    creates a concatenated text string for semantic meaning, and generates
    a vector embedding using the provided model.

    Args:
       products: The filtered list of product dictionaries.
       brand_classification: A map from original brand names to their final names.
       model: The initialized SentenceTransformer model.
       stopwords: A set of words to remove during text normalization.

    Returns:
       A list of fully processed products, now including their final brand,
       concatenation string, and embedding vector.
    """
    processed_products = []
    for product in products:
        # Get original brand to look up its final classification
        # This handles products that might not have a brand key initially
        original_brand = str(product.get("brand", "NO BRAND")) 
        final_brand = brand_classification.get(original_brand, "NO BRAND")
        product["brand"] = final_brand

        # Generate text string for the embedding model
        product_concatenation = generate_product_concatenation(product, stopwords)
        if not product_concatenation:
            continue
        product["product_concatenation"] = product_concatenation

        # Generate and assign the embedding. This is the heavy lifting.
        product["product_embedding"] = model.encode(product_concatenation).tolist()

        processed_products.append(product)

    return processed_products


def generate_product_concatenation(product: dict, stopwords: set) -> str:
    """
    Create a text string from product details for embedding.

    :param product: The product dictionary.
    :param supermarket: 'eroski' or 'makro'.
    :return: A normalized text string containing relevant fields.
    """
    # category_path = product.get("categoryInSupermarket", "")
    # category_root = category_path.split('/')[0] if category_path else ""

    concatenation = None

    print(product)

    # FIX: Need to standarize the output keys, this is a temporary fix
    if "EROSKI" in product.get("supermarket", ""):
        concatenation = product.get("description", "")

    if "Makro" in product.get("supermarket", ""):
        concatenation = product.get("denomination", "")

    if not concatenation:
        raise ValueError("FIX concatenation bug")

    brand = product.get('brand', 'NO BRAND')
    raw_ingredients = product.get('raw_ingredients', '')

    nutrition = product.get('nutrition_information', {})
    nutrition_values = ""
    if nutrition:
        nutrition_values = " ".join([
            f"calories {nutrition.get('calories', {}).get('value', '')}",
            f"fat {nutrition.get('fat', {}).get('value', '')}",
            f"protein {nutrition.get('protein', {}).get('value', '')}",
            f"carbs {nutrition.get('carbohydrates', {}).get('value', '')}"
        ])

    concatenation += f" {brand}" if brand != "NO BRAND" else ""
    concatenation += f" {raw_ingredients}" if raw_ingredients else ""
    concatenation += f" {nutrition_values}" if nutrition else ""

    return normalize_spanish_text(concatenation, stopwords)



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
        print("Deploying tokenizer flow to Prefect...")
        tokenizer.deploy(
            name=args.version,
            version=args.version,
            tags=["tokenizer"],
            work_pool_name=args.version,
            image=f"990187902980.dkr.ecr.us-east-1.amazonaws.com/report:{args.version}_tokenizer",
            build=True,
            push=True
        )
        print("Deployment command executed. Check your Prefect UI for status.")
    else:
        # --- Local execution logic ---
        print("Running tokenizer flow locally...")

        # NOTE: tokenizer depends on the datatsets of both supermarkets (flattened)
        # and the category_map_{supermarket_a}_{supermarket_b}.json as inputs

        # TEST: Manual execution id for testing
        execution_id = "202508141755214580"

        # --- Create and save S3 bucket block ---
        s3bucket = S3Bucket(
            bucket_name="comexsoft-workflows",
            bucket_folder=f"report/{execution_id}",
            credentials=AwsCredentials.load("credentials")  # type: ignore
        )

        # NOTE: Need to overwrite for using the local execution id
        # Need to overwrite for using the local execution id
        s3block = s3bucket.save("s3-report", overwrite=True)

        tokenizer(
            execution_id=execution_id,
            supermarket_a="eroski",
            supermarket_b="makro",
        )

# --- References ---

# ========== Option 1: SentenceTransformer model ==========
# model = SentenceTransformer('hiiamsid/sentence_similarity_spanish_es')

# ========== Option 2: HF MiniLM model ==========

# model_path = '../models/all-MiniLM-L6-v2'
# tokenizer = AutoTokenizer.from_pretrained(model_path)
# hf_model = AutoModel.from_pretrained(model_path)

# def get_embedding(text):
#     inputs = tokenizer(text, return_tensors='pt', truncation=True, padding=True, max_length=128)
#     with torch.no_grad():
#         outputs = hf_model(**inputs)
#     return outputs.last_hidden_state.mean(dim=1).squeeze().numpy()

