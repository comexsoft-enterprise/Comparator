import json

from prefect import flow
from prefect.artifacts import create_table_artifact, create_markdown_artifact
from prefect_aws import AwsCredentials
from prefect_aws.s3 import S3Bucket


@flow(name="evaluate_norepeat")
def evaluate_norepeat(execution_id: str, input_filename: str) -> None:
    """
    Check for ids matching more than once within the similarities report as a validation.
    If False it means something went wrong with the report generation process (tokenizer-similarities)

    Args:
        file_path (str): The path to the JSON file to be checked.
    """
    # Read from s3
    s3bucket = S3Bucket.load("s3-report")
    rawdata = s3bucket.read_path(path=input_filename)
    data = json.loads(rawdata)

    # try:
    #     with open(input_filename, 'r', encoding='utf-8') as f:
    #         data = json.load(f)
    # except FileNotFoundError:
    #     print(f"Error: The file '{input_filename}' was not found.")
    #     return
    # except json.JSONDecodeError:
    #     print(f"Error: The file '{input_filename}' is not a valid JSON file.")
    #     return

    # Dictionary to track which similar IDs we've seen and in which row
    seen_similar_ids = {}
    found_duplicates = False
    print("--- Starting Repeated Similar Product ID Check ---")

    for i, product in enumerate(data):
        # Check the similar_products array for content
        similar_products = product.get('similar_products')
        if not (similar_products and isinstance(similar_products, list) and len(similar_products) > 0):
            continue

        similar_product = similar_products[0]
        similar_product_id = similar_product.get('productIdInSupermarket')

        if not similar_product_id:
            continue

        # Check if we have seen this similar product ID before
        if similar_product_id in seen_similar_ids:
            found_duplicates = True
            first_occurrence_row = seen_similar_ids[similar_product_id]
            print(f"\n[!] Repeated Similar ID Found: '{similar_product_id}'")
            print(f"    - First seen in Row {first_occurrence_row}")
            print(f"    - Repeated in Row {i + 1} (Original Product: '{product.get('denomination', 'N/A')}')")
        else:
            # If not seen, add it to our dictionary with the current row number
            seen_similar_ids[similar_product_id] = i + 1

    print("\n--- Check Complete ---")
    success = None
    message = None
    if not found_duplicates:
        success = True
        message = "Success: No repeated similar product IDs were found across rows."
        print(message)

    else:
        success = False
        message = "Action Required: One or more similar product IDs are repeated in the report."
        print(message)

    response = {
        "success": success,
        "message": message,
    }
    str_response = json.dumps(response, indent=2)
    markdown_report = f"""```python
       {str_response}
    """
    create_markdown_artifact(
        key="norepeat-report",
        markdown=markdown_report,
        description="Evaluate norepeat matches for validation"

    )


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
        print("Deploying evaluate norepeat matches flow to Prefect...")
        evaluate_norepeat.deploy(
            name=args.version,
            version=args.version,
            tags=["evaluate", "matches", "norepeat"],
            work_pool_name=args.version,
            image=f"990187902980.dkr.ecr.us-east-1.amazonaws.com/report:{args.version}_evaluate_norepeat",
            build=True,
            push=True
        )
        print("Deployment command executed. Check your Prefect UI for status.")
    else:
        # --- Local execution logic ---
        print("Running evaluate norepeat flow locally...")

        # NOTE: this process depends on the dynamic input file stored within s3
        # rawreport.json, this means we will probably set execution id manually for testing.

        # TEST: Manual execution id for testing
        execution_id = "202506010000000000"

        # --- Create and save S3 bucket block ---
        s3bucket = S3Bucket(
            bucket_name="comexsoft-workflows",
            bucket_folder=f"report/{execution_id}",
            credentials=AwsCredentials.load("credentials")  # type: ignore
        )

        # NOTE: Need to overwrite for using the local execution id
        s3block = s3bucket.save("s3-report", overwrite=True)

        evaluate_norepeat(
            execution_id=execution_id,
            input_filename="rawreport.json"
        )
