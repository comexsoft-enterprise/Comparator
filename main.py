# DEPRECATED

import asyncio
import time
import os

# TODO: import pytz  # Use utc timezone? spain timezone? local timezone?

from datetime import datetime

from prefect import flow
from prefect_aws import AwsCredentials
from prefect_aws.s3 import S3Bucket
from prefect.deployments import run_deployment

from pydantic import SecretStr


@flow(name="report_workflow")
async def report():
    """
    Main workflow that orchestrates the entire report process.
    """

    STORE = "01013"
    VERSION = "default"  # 'main' | 'dev'

    SUPERMARKET_A = "eroski"
    SUPERMARKET_B = "makro"

    # --- Execution ID ---
    date = int(datetime.now().strftime("%Y%m%d"))
    timestamp = int(time.time())
    execution_id = f"{date}{timestamp}"

    # TEST Manual execution id for testing
    # execution_id = "202512011764621828"

    # --- AWS ---
    print("Setting up AWS resources...")

    # AWS_PROFILE = os.getenv("AWS_PROFILE")
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", None)
    AWS_BUCKET = os.getenv("BUCKET", "comexsoft-workflows")
    if not AWS_SECRET_ACCESS_KEY:
        raise ValueError("AWS_SECRET_ACCESS_KEY environment variable is not set.")

    aws_credentials = AwsCredentials(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=SecretStr(AWS_SECRET_ACCESS_KEY),
    )
    await aws_credentials.save("credentials-block", overwrite=True)  # type: ignore

    print("AWS credentials block saved.")

    s3bucket = S3Bucket(
        bucket_name=AWS_BUCKET,
        bucket_folder=f"report/{execution_id}",
        credentials=aws_credentials
    )
    await s3bucket.save("s3-report", overwrite=True)  # type: ignore
    print(f"S3 report bucket block for '{AWS_BUCKET}' saved.")

    s3bucket = S3Bucket(
        bucket_name=AWS_BUCKET,
        bucket_folder="report-static",
        credentials=aws_credentials
    )
    await s3bucket.save("s3-report-static", overwrite=True)  # type: ignore
    print(f"S3 report static bucket block for '{AWS_BUCKET}' saved.")

    tasks = []

    # --- Eroski ---

    async def run_eroski_workflow():
        s3bucket = S3Bucket(
            bucket_name=AWS_BUCKET,
            bucket_folder=f"report/{execution_id}/eroski",
            credentials=aws_credentials
        )
        await s3bucket.save("s3-eroski", overwrite=True)  # type: ignore
        print(f"S3 eroski bucket block for '{AWS_BUCKET}' saved.")

        await run_deployment(  # type: ignore
            name=f"eroski_workflow/{VERSION}",  # flow/deployment_name
            parameters={
                "execution_id": execution_id,
                "version": VERSION,
                "store": STORE
            }
        )
        print("Eroski workflow deployment initiated.")

    if SUPERMARKET_A == "eroski" or SUPERMARKET_B == "eroski":
        tasks.append(run_eroski_workflow())

    # --- Makro ---

    async def run_makro_workflow():
        s3bucket = S3Bucket(
            bucket_name=AWS_BUCKET,
            bucket_folder=f"report/{execution_id}/makro",
            credentials=aws_credentials
        )
        await s3bucket.save("s3-makro", overwrite=True)  # type: ignore
        print(f"S3 makro bucket block for '{AWS_BUCKET}' saved.")

        await run_deployment(  # type: ignore
            name=f"makro_workflow/{VERSION}",  # flow/deployment_name
            parameters={
                "execution_id": execution_id,
                "version": VERSION
            }
        )

    if SUPERMARKET_A == "makro" or SUPERMARKET_B == "makro":
        tasks.append(run_makro_workflow())

    # Multithread
    await asyncio.gather(*tasks)
    print(f"Running {SUPERMARKET_A} and {SUPERMARKET_B} workflows concurrently...")
 
    # --- Category mapper ---
    await run_deployment(  # type: ignore
        name=f"generate_category_map/{VERSION}",
        parameters={
            "supermarket_a": SUPERMARKET_A,
            "supermarket_b": SUPERMARKET_B
        }
    )

    # --- Tokenizer ---
    await run_deployment(  # type: ignore
        name=f"tokenizer/{VERSION}",  # flow/deployment_name
        parameters={
            "execution_id": execution_id,
            "supermarket_a": SUPERMARKET_A,
            "supermarket_b": SUPERMARKET_B,
        }
    )

    # --- Similarities ---
    await run_deployment(  # type: ignore
        name=f"find_similarities/{VERSION}",  # flow/deployment_name
        parameters={
            "execution_id": execution_id,
            "supermarket_a": SUPERMARKET_A,
            "supermarket_b": SUPERMARKET_B,
        }
    )

    # --- flat2json ---
    await run_deployment(  # type: ignore
        name=f"flatten_json/{VERSION}",
        parameters={
            "execution_id": execution_id,
            "input_filename": "rawreport.json",
            "output_filename": "report.json",
            "scraper": ""
        }
    )

    # --- json2csv ---
    await run_deployment(  # type: ignore
        name=f"json2csv/{VERSION}",
        parameters={
            "execution_id": execution_id,
            "input_filename": "report.json",
            "output_filename": "report.csv",
            "scraper": ""
        }
    )

    # --- csv2xlsx ---
    await run_deployment(  # type: ignore
        name=f"csv2excel/{VERSION}",
        parameters={
            "execution_id": execution_id,
            "input_filename": "report.csv",
            "output_filename": "report.xlsx",
            "scraper": ""
        }
    )

    # --- Multithread ---
    tasks = []
 
    async def run_evaluate_matches_workflow():
        await run_deployment(  # type: ignore
            name=f"evaluate_matches/{VERSION}",
            parameters={
                "execution_id": execution_id,
                "input_filename": "rawreport.json",  # similarities report
                "output_filename": "evaluation_details.csv"
            }
        )
 
    if (SUPERMARKET_A == "eroski" or SUPERMARKET_A == "makro") and (SUPERMARKET_B == "makro" or SUPERMARKET_B == "eroski"): 
        tasks.append(run_evaluate_matches_workflow())
 
    async def run_evaluate_norepeat_workflow():
        await run_deployment(  # type: ignore
            name=f"evaluate_norepeat/{VERSION}",
            parameters={
                "execution_id": execution_id,
                "input_filename": "rawreport.json",
            }
        )
    tasks.append(run_evaluate_norepeat_workflow())
 
    await asyncio.gather(*tasks)
    print("Running evaluate workflows concurrently...")

    # --- Notify ---
    # TODO: send email with report
    # await send_email()


if __name__ == "__main__":
    # NOTE: this script should only run locally for now
    asyncio.run(report())


