import json
import numpy as np
import spacy
import faiss

from collections import defaultdict

from prefect import flow
from prefect.artifacts import create_progress_artifact, update_progress_artifact, create_table_artifact
from prefect_aws import AwsCredentials
from prefect_aws.s3 import S3Bucket

from pathlib import Path
from rich import print
from kit.download_spacy_deps import download_es_core_news_sm_model


@flow(name="find_similarities", log_prints=True)
def find_similarities(execution_id: str, supermarket_a: str, supermarket_b: str) -> None:
    # === Blocks configurations ===

    root_s3bucket = S3Bucket.load("s3-report")
    s3bucket = S3Bucket.load("s3-tokenizer")

    # ========== Main Settings and Data Loading ==========

    # We will try to download the necessary language model from spaCy.
    download_es_core_news_sm_model()

    # Load the Spanish language model. We use this to turn words into vectors (numbers).
    nlp = spacy.load("es_core_news_sm")

    # This set will be passed down to all functions to track used IDs
    # across all category files, ensuring each product is matched only once.
    used_supermarket_b_ids = set()

    # Output
    OUTPUT_FILE = Path(f"results/{execution_id}/rawreport.json")

    OUTPUT_FOLDER = Path(f"results/{execution_id}/final_{supermarket_a}_{supermarket_b}")
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    # Input
    SUPERMARKET_A_DIR = Path(f"results/{execution_id}/{supermarket_a}")
    SUPERMARKET_A_DIR.mkdir(parents=True, exist_ok=True)

    SUPERMARKET_B_DIR = Path(f"results/{execution_id}/{supermarket_b}")
    SUPERMARKET_B_DIR.mkdir(parents=True, exist_ok=True)

    # NOTE: uncomment for production

    # List objects from s3 storage
    _ = s3bucket.download_folder_to_path(
        from_folder=supermarket_a,
        to_folder=SUPERMARKET_A_DIR
    )

    _ = s3bucket.download_folder_to_path(
        from_folder=supermarket_b,
        to_folder=SUPERMARKET_B_DIR
    )

    # List results of tokenizer.py
    supermarket_a_files = [str(file) for file in SUPERMARKET_A_DIR.iterdir() if str(file).endswith('_processed_products_modified.json')]
    supermarket_b_files = [str(file) for file in SUPERMARKET_B_DIR.iterdir() if str(file).endswith('_processed_products_modified.json')]

    progress_artifact_id = create_progress_artifact(progress=0.0, description="Indicates the iteration over tokenizer results.")

    # Iterate over each tokenized supermarket A result file
    for idx, supermarket_a_file in enumerate(supermarket_a_files):
        category_type = "no_brand" if "no_brand" in supermarket_a_file else "brand"

        # Extract category from filename
        parts = supermarket_a_file.split('_')
        category = parts[1] if len(parts) > 1 else "unknown_category"

        # Craft key substring to search corresponding file from supermarket B
        expected_substring = f"{category}_{category_type}"

        # Find tokenizer supermarket B file results
        supermarket_b_file = next((file for file in supermarket_b_files if expected_substring in file), None)
        if supermarket_b_file:

            # 3) Process and match
            progress = (idx / len(supermarket_a_files)) * 100
            update_progress_artifact(artifact_id=progress_artifact_id, progress=progress)

            supermarket_a_filepath = Path(supermarket_a_file)
            supermarket_b_filepath = Path(supermarket_b_file)

            matched_products = process_category_files(
                supermarket_a=supermarket_a,
                supermarket_b=supermarket_b,
                supermarket_a_filepath=supermarket_a_filepath,
                supermarket_b_filepath=supermarket_b_filepath,
                category_type=category_type,
                nlp=nlp,
                used_supermarket_b_ids=used_supermarket_b_ids
            )

            # 4) Build output filepath based on supermarket A as base & save
            category_name = supermarket_a_filepath.name.split("_")[1]
            output_file = Path(OUTPUT_FOLDER / f"computed_{supermarket_a}_{supermarket_b}_{category_name}_{category_type}_similarities.json")
            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(str(output_file), "w", encoding="utf-8") as file:
                json.dump(matched_products, file, ensure_ascii=False, indent=2)

            print(f"Processed {category_type} category '{category_name}' and saved results to '{output_file}'")

        else:
            print(f"[WARNING] No {supermarket_b} {category_type} file found for category '{category}'")

    input_files = list(OUTPUT_FOLDER.glob("*.json"))
    if not input_files:
        print("No JSON files found in the specified folder.")
        return

    # We save the data from each category and then save all together
    merged_data = merge_json_files(input_files, str(OUTPUT_FILE))

    create_table_artifact(
        key="similarities-report",
        table=merged_data,
        description="Monitors the data of similarities processing."
    )

    with open(str(OUTPUT_FILE), 'w', encoding='utf-8') as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=4)

    # FIX: Hardcoded
    root_s3bucket.write_path(
        path="rawreport.json",
        content=json.dumps(merged_data).encode("utf-8")
    )

    print(f"Merged JSON saved to {str(OUTPUT_FILE)}")


def process_category_files(supermarket_a: str, supermarket_b: str, supermarket_a_filepath: Path, supermarket_b_filepath: Path, category_type: str, nlp, used_supermarket_b_ids: set) -> list:
    """
    Load supermarket A and supermarket B products from JSON, match them via FAISS, and save results.

    supermarket_a_filepath: Path to supermarket A JSON file.
    supermarket_b_filepath: Path to supermarket B JSON file.
    output_folder: Folder where the result JSON should be saved.
    category_type: 'brand' or 'no_brand', used for naming outputs.
    """
    # 1) Load supermarket A products
    supermarket_a_rawproducts = load_json(str(supermarket_a_filepath))
    supermarket_a_map = {product["id"]: product for product in supermarket_a_rawproducts}
    supermarket_a_products = list(supermarket_a_map.values())

    # 2) Load supermarket B products
    supermarket_b_rawproducts = load_json(str(supermarket_b_filepath))
    supermarket_b_map = {product["id"]: product for product in supermarket_b_rawproducts}
    supermarket_b_products = list(supermarket_b_map.values())

    # 3) Find most similar products (FAISS) based on category type
    # https://gemini.google.com/u/1/app/3a7b2a90b3278238
    if category_type == "brand":
        matched_supermarket_products = add_similar_products_brand(supermarket_a, supermarket_b, supermarket_a_products, supermarket_b_products, nlp, used_supermarket_b_ids)

    else: # no_brand
        matched_supermarket_products = add_similar_products_no_brand(supermarket_a, supermarket_b, supermarket_a_products, supermarket_b_products, nlp, used_supermarket_b_ids)

    return matched_supermarket_products


# TEST
def add_similar_products_brand(supermarket_a:str, supermarket_b: str, supermarket_a_products: list, supermarket_b_products: list, nlp, used_supermarket_b_ids: set) -> list:
    """
    Finds the best one-to-one matches between supermarket A and supermarket B products.
    This new logic partitions products by brand name before comparison. It calculates
    potential matches only between products of the exact same brand, sorts them by
    similarity, and then greedily selects the best pairs, ensuring no product is
    matched more than once.

    :param supermarket_a_products: List of supermarket A products with embeddings.
    :param supermarket_b_products: List of supermarket B products with embeddings.

    :return: A list of matched pairs, where each item contains supermarket A product info and its single best supermarket B match of the same brand.
    """
    # Step 1: Group valid products by their brand name.
    # A product is valid if it has a brand and an embedding.
    supermarket_a_by_brand = defaultdict(list)
    for product in supermarket_a_products:
        brand = product["brand"].upper()
        if product.get("brand") and product.get("product_embedding"):
            supermarket_a_by_brand[brand].append(product)

    supermarket_b_by_brand = defaultdict(list)
    for product in supermarket_b_products:
        brand = product["brand"].upper()
        if product.get("brand") and product.get("product_embedding"):
            supermarket_b_by_brand[brand].append(product)

    # A list to hold all valid potential matches across all common brands
    all_potential_matches = []

    # Step 2: Find brands that exist in both supermarkets.
    common_brands = set(supermarket_a_by_brand.keys()) & set(supermarket_b_by_brand.keys())
    if not common_brands:
        print("[INFO] No common brands found between the two product lists. No matches possible.")
        return []

    # Step 3: Iterate over each common brand to find matches within that brand.
    for brand in common_brands:
        brand_supermarket_a_prods = supermarket_a_by_brand[brand]
        brand_supermarket_b_prods = supermarket_b_by_brand[brand]

        # Extract embeddings for the products of the current brand.
        supermarket_a_embeddings = np.array([p["product_embedding"] for p in brand_supermarket_a_prods], dtype=np.float32)
        supermarket_b_embeddings = np.array([p["product_embedding"] for p in brand_supermarket_b_prods], dtype=np.float32)

        if supermarket_a_embeddings.ndim != 2 or supermarket_b_embeddings.ndim != 2:
            continue

        # Use FAISS to find the single best Makro match for each Eroski product *within this brand*.
        supermarket_b_brand_index = create_faiss_index(supermarket_b_embeddings)
        distances, supermarket_b_indices = supermarket_b_brand_index.search(supermarket_a_embeddings, k=1)

        # Create a list of potential matches for this brand.
        for i in range(len(brand_supermarket_a_prods)):
            distance = distances[i][0]
            supermarket_b_idx_in_brand_list = supermarket_b_indices[i][0]

            # We store the actual product objects to easily retrieve all their data later.
            supermarket_a_brand_product = brand_supermarket_a_prods[i]
            supermarket_b_brand_product = brand_supermarket_b_prods[supermarket_b_idx_in_brand_list]

            all_potential_matches.append((distance, supermarket_a_brand_product, supermarket_b_brand_product))

    # Step 4: Sort ALL potential matches from all brands by distance (best to worst).
    # This ensures we prioritize the absolute best matches first, regardless of brand.
    all_potential_matches.sort(key=lambda x: x[0])

    # Step 5: Iterate through sorted matches and create unique one-to-one pairs.
    # We use product IDs to track which products have already been paired.
    used_supermarket_a_ids = set()
    # used_makro_ids = set()
    final_results = []

    for dist, supermarket_a_matched_product, supermarket_b_matched_product in all_potential_matches:
        supermarket_a_matched_id = supermarket_a_matched_product.get("id")
        supermarket_b_matched_id = supermarket_b_matched_product.get("id")

        # NOTE: This check is now redundant for makro_id because the list was pre-filtered,
        # but it's good practice to keep it for clarity and safety.

        # If both products in the potential pair are still available, create a match.
        if supermarket_a_matched_id not in used_supermarket_a_ids and supermarket_b_matched_id not in used_supermarket_b_ids:

            # FIX: this broke because of the eroski parser change of keys

            # Construct the Makro product info.
            similar_product_info = {
                "id": supermarket_b_matched_product["id"],
                "description": supermarket_b_matched_product.get("denomination"),
                "brand": supermarket_b_matched_product.get("brand"),
                "price": supermarket_b_matched_product.get("price"),
                "unit_price": supermarket_b_matched_product.get("unit_price"),
                f"category_{supermarket_b}": supermarket_b_matched_product.get("category"),
                # "product_concatenation": supermarket_b_matched_product.get("product_concatenation"),
                # "main_words": extract_main_word(supermarket_b_matched_product.get("product_concatenation"), nlp),
                "distance": float(dist),
            }

            # Construct the final result object for the Eroski product.
            final_results.append({
                "id": supermarket_a_matched_product.get("id"),
                "description": supermarket_a_matched_product.get("description"),
                "brand": supermarket_a_matched_product.get("brand"),
                "price": supermarket_a_matched_product.get("price"),
                "unit_price": supermarket_a_matched_product.get("unit_price"),
                f"category_{supermarket_a}": supermarket_a_matched_product.get("category"),
                # "product_concatenation": supermarket_a_matched_product.get("product_concatenation"),
                # "main_words": extract_main_word(supermarket_a_matched_product.get("product_concatenation"), nlp),
                "similar_products": [similar_product_info],
            })

            # Add the IDs of the matched products to the 'used' sets to prevent re-matching.
            used_supermarket_a_ids.add(supermarket_a_matched_id)
            used_supermarket_b_ids.add(supermarket_b_matched_id)

    return final_results


# TEST
def add_similar_products_no_brand(supermarket_a: str, supermarket_b: str, supermarket_a_products: list, supermarket_b_products: list, nlp, used_supermarket_b_ids) -> list:
    """
    Finds the best one-to-one matches between supermarket A and supermarket B products.
    This new logic calculates all potential matches, sorts them by similarity, and then 
    greedily selects the best pairs, ensuring no product is matched more than once.

    :param eroski_products: List of Eroski products with embeddings.
    :param makro_products: List of Makro products with embeddings.
    :return: A list of matched pairs, where each item contains Eroski product
             info and its single best Makro match.
    """

    # Step 1: Filter products to ensure they have valid embeddings for matching.
    # This avoids errors and ensures we only work with processable data.
    valid_supermarket_a_products = [p for p in supermarket_a_products if p.get("product_embedding")]

    # --- NOTE: The logic needs to check the product ID, not the embedding ---
    # The original line caused a TypeError because an embedding (a list) cannot be
    # checked for membership in a set. The correct logic is to check if the
    # product's ID is already in the set of used IDs.
    valid_supermarket_b_products = [
        p for p in supermarket_b_products 
        if p.get("product_embedding") and p.get("id") not in used_supermarket_b_ids
    ]
    # valid_makro_products = [p for p in makro_products if p.get('product_embedding') not in used_supermarket_b_ids]

    if not valid_supermarket_a_products or not valid_supermarket_b_products:
        print("[WARNING] One of the product lists is empty after filtering for embeddings. Skipping.")
        return []

    # Step 2: Extract embeddings into NumPy arrays for efficient processing with FAISS.
    supermarket_a_embeddings = np.array([p["product_embedding"] for p in valid_supermarket_a_products], dtype=np.float32)
    supermarket_b_embeddings = np.array([p["product_embedding"] for p in valid_supermarket_b_products], dtype=np.float32)

    # Ensure embeddings are 2D arrays [num_products, embedding_dim]
    if supermarket_a_embeddings.ndim != 2 or supermarket_b_embeddings.ndim != 2:
        print("[WARNING] Embeddings are not in the correct 2D format. Skipping.")
        return []

    # Step 3: Use FAISS to find the single best Makro match for EACH Eroski product.
    # We build an index for Makro products and then search it with all Eroski products at once for efficiency.
    supermarket_b_index = create_faiss_index(supermarket_b_embeddings)
    # We search for the top 1 (k=1) match for every eroski embedding.
    distances, supermarket_b_indices = supermarket_b_index.search(supermarket_a_embeddings, k=1)

    # Step 4: Create a list of all potential matches, including the distance.
    # This list will contain tuples of (distance, eroski_product_index, makro_product_index).
    potential_matches = []
    for i in range(len(valid_supermarket_a_products)):
        # The search returns a list of lists, so we get the first element for the single best match.
        distance = distances[i][0]
        supermarket_b_idx = supermarket_b_indices[i][0]
        potential_matches.append((distance, i, supermarket_b_idx))

    # Step 5: Sort the potential matches by distance (from best to worst match).
    # This crucial step allows us to greedily pick the absolute best pairs first.
    potential_matches.sort(key=lambda x: x[0])

    # Step 6: Iterate through sorted matches and create unique one-to-one pairs.
    # We use sets to keep track of which products have already been paired off.
    used_supermarket_a_indices = set()
    # used_makro_indices = set()
    final_results = []

    for dist, supermarket_a_idx, supermarket_b_idx in potential_matches:

        # If both the Eroski and Makro products in the potential pair are still available, we create a match.
        if supermarket_a_idx not in used_supermarket_a_indices and supermarket_b_idx not in used_supermarket_b_ids:

            supermarket_a_matched_product = valid_supermarket_a_products[supermarket_a_idx]
            supermarket_b_matched_product = valid_supermarket_b_products[supermarket_b_idx]

            # Construct the Makro product info, maintaining the original format.
            similar_product_info = {
                "id": supermarket_b_matched_product["id"],
                "brand": supermarket_b_matched_product.get("brand"),
                "description": supermarket_b_matched_product.get("denomination"),  # FIX this should be name 'description' for consistency
                f"category_{supermarket_b}": supermarket_b_matched_product.get("category"),
                "price": supermarket_b_matched_product.get("price"),
                "unit_price": supermarket_b_matched_product.get("unit_price"),
                "product_concatenation": supermarket_b_matched_product.get("product_concatenation"),
                "main_words": extract_main_word(supermarket_b_matched_product.get("product_concatenation"), nlp),
                "distance": float(dist),
            }

            # Construct the final result object for the Eroski product.
            final_results.append({
                "id": supermarket_a_matched_product.get("id"),
                "brand": supermarket_a_matched_product.get("brand"),
                "description": supermarket_a_matched_product.get("description"),
                "price": supermarket_a_matched_product.get("price"),
                "unit_price": supermarket_a_matched_product.get("unit_price"),
                f"category_{supermarket_a}": supermarket_a_matched_product.get("category"),
                # The matched Makro product is wrapped in a list for format consistency with the original script.
                "product_concatenation": supermarket_a_matched_product.get("product_concatenation"),
                "main_words": extract_main_word(supermarket_a_matched_product.get("product_concatenation"), nlp),
                "similar_products": [similar_product_info],
            })

            # Add the indices of the matched products to the 'used' sets to prevent them from being matched again.
            used_supermarket_a_indices.add(supermarket_a_idx)
            used_supermarket_b_ids.add(supermarket_b_idx)

    return final_results


def extract_main_word(text, nlp):
    doc = nlp(text)
    main_words = [token.text for token in doc if token.pos_ == "NOUN" ]
    return main_words


def create_faiss_index(embeddings: np.ndarray) -> faiss.IndexFlatL2:
    """
    Create a FAISS index using L2 (Euclidean) distance.

    :param embeddings: A NumPy 2D array of shape (num_products, embedding_dim).
    :return: A faiss.IndexFlatL2 index.
    """
    if embeddings.ndim != 2:
        raise ValueError("Embeddings must be a 2D array (num_samples, embedding_dim).")
    embedding_dim = embeddings.shape[1]
    index = faiss.IndexFlatL2(embedding_dim)
    index.add(embeddings.astype('float32'))
    return index


def load_json(file_path: str) -> list:
    """
    Load JSON data from a file and return it as a Python list (or dict).
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        return json.load(file)


def merge_json_files(input_files: list, output_file: str) -> list:
    """
    Merges multiple JSON files into one.

    :param input_files: List of input JSON file paths.
    :param output_file: Path for the merged JSON output file.
    """
    merged_data = []

    for file_path in input_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

                if isinstance(data, list):
                    merged_data.extend(data)
                elif isinstance(data, dict):
                    merged_data.append(data)
                else:
                    print(f"Unsupported JSON structure in file: {file_path}")

        except Exception as e:
            print(f"Error reading file {file_path}: {e}")

    # Add deduplication logic before saving the final report.
    # We use a set to keep track of product pairs we have already added to the results.
    deduplicated_data = []
    seen_pairs = set()

    for item in merged_data:
        # A unique key for a match is the combination of the Eroski and Makro product IDs.
        if item.get('similar_products'):
            eroski_id = item.get('id')

            # Get the ID of the matched makro product.
            makro_id = item['similar_products'][0].get('id')

            # We only deduplicate if both IDs are present.
            if eroski_id and makro_id:
                pair = (eroski_id, makro_id)
                # If we haven't seen this pair before, add it to our results and record the pair.
                if pair not in seen_pairs:
                    deduplicated_data.append(item)
                    seen_pairs.add(pair)
            else:
                # If IDs are missing, we can't create a unique pair key, so we add the item directly.
                deduplicated_data.append(item)
        else:
            # If an item has no similar products, there's no pair to check. Add it.
            deduplicated_data.append(item)

    return deduplicated_data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run or deploy the similarities workflow.")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy the workflow to Prefect."
    )
    parser.add_argument(
        "--version",
        type=str,
        default="default",
        help="Specify the version for deployment or local execution (e.g., 'v1', 'v2', 'main')."
    )
    args = parser.parse_args()

    if args.deploy:
        # --- Prefect deployment logic ---
        print("Deploying similarities flow to Prefect...")
        find_similarities.deploy(
            name=args.version,
            version=args.version,
            work_pool_name=args.version,
            image=f"990187902980.dkr.ecr.us-east-1.amazonaws.com/report:{args.version}_find_similarities",
            build=True,
            push=True
        )
        print("Deployment command executed. Check your Prefect UI for status.")

    else:
        # --- Local execution logic ---
        print("Running similarities flow locally...")

        # NOTE: this scraper depends directly of the files saved by tokenizer.py

        # TEST: Manual execution id for testing
        execution_id = "202507151752603285"

        # --- Create and save S3 bucket block ---

        s3bucket = S3Bucket(
            bucket_name="comexsoft-workflows",
            bucket_folder=f"report/{execution_id}/tokenizer",
            credentials=AwsCredentials.load("credentials")  # type: ignore
        )

        # NOTE: Need to overwrite for using the local execution id
        # Need to overwrite for using the local execution id
        root_s3block = s3bucket.save("s3-report", overwrite=True)
        s3block = s3bucket.save("s3-tokenizer", overwrite=True)

        find_similarities(
            execution_id=execution_id,
            supermarket_a="eroski",
            supermarket_b="makro",
        )

