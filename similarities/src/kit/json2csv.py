import json
import csv

EXECUTION_ID = "000000000000000002"

INPUT_FILEPATH = f"./results/{EXECUTION_ID}/report.json"
OUTPUT_FILEPATH = f"./results/{EXECUTION_ID}/report.csv"

def json_to_csv(json_file, csv_file):
    """
    Converts a JSON file to a CSV file, ensuring all keys are considered as columns and missing values are filled with "Not found".

    :param json_file: Path to the input JSON file.
    :param csv_file: Path to the output CSV file.
    """
    try:
        # Read the JSON file
        with open(json_file, 'r', encoding='utf-8') as file:
            data = json.load(file)

        # Ensure the JSON data is a list of dictionaries
        if not isinstance(data, list):
            raise ValueError("JSON data must be a list of dictionaries.")

        # Get all unique keys across all items in the JSON
        all_keys = set()
        for item in data:
            if isinstance(item, dict):
                all_keys.update(item.keys())
            else:
                raise ValueError("Each item in the JSON must be a dictionary.")

        # Convert the set of keys to a sorted list
        all_keys = sorted(all_keys)

        # Write to CSV
        row_count = 0
        with open(csv_file, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=all_keys, delimiter=';')
            writer.writeheader()  # Write the header row
            
            for idx, item in enumerate(data):
                try:
                    writer.writerow({key: item.get(key, "Not found") for key in all_keys})  # Write each row
                    row_count += 1
                except Exception as e:
                    print(f"Error writing row {idx}: {e}")

        print(f"Total rows written to CSV: {row_count}")
        print(f"JSON data has been successfully converted to '{csv_file}'.")

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    # Ensure the output directory exists
    # os.makedirs(os.path.dirname(OUTPUT_FILEPATH), exist_ok=True)

    # Convert JSON to CSV
    json_to_csv(INPUT_FILEPATH, OUTPUT_FILEPATH)

