import json
from pathlib import Path

def convert_dataset_robust(input_file: Path, output_file: Path):
    """
    Reads the complex nested-list format and converts it to a clean, robust
    list-of-dictionaries format, handling one-to-many and one-to-none mappings.
    """
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            legacy_data = json.load(f)
        
        new_data = []
        for i, pair in enumerate(legacy_data):
            # --- Robust Validation ---
            if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], list) and isinstance(pair[1], list)):
                print(f"[yellow]Warning:[/yellow] Skipping malformed entry #{i+1}: {pair}")
                continue
            
            if not pair[0]: # Si la lista de source está vacía
                print(f"[yellow]Warning:[/yellow] Skipping entry #{i+1} with no source category: {pair}")
                continue
            
            source_cat = pair[0][0].strip() # .strip() para limpiar espacios extra
            target_list = pair[1]

            # --- Logic to handle different cases ---
            if not target_list:
                # Caso: Uno-a-Ninguno (lista de targets vacía)
                new_data.append({
                    "source_category": source_cat,
                    "target_category": None
                })
            else:
                # Caso: Uno-a-Uno o Uno-a-Muchos
                for target_cat in target_list:
                    new_data.append({
                        "source_category": source_cat,
                        "target_category": target_cat.strip()
                    })

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=2, ensure_ascii=False)
            
        print(f"[bold green]Successfully converted {len(legacy_data)} source entries into {len(new_data)} mapping rules.[/bold green]")
        print(f"Input: '{input_file}'")
        print(f"Output: '{output_file}'")

    except FileNotFoundError:
        print(f"[red]Error:[/red] Input file '{input_file}' not found.")
    except Exception as e:
        print(f"[red]An error occurred:[/red] {e}")

if __name__ == "__main__":
    input_filename = Path("evaluation_set_old.json") 
    output_filename = Path("evaluation_set.json")
    
    convert_dataset_robust(input_filename, output_filename)
