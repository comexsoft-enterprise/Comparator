import json
import io

from pathlib import Path
from src.evaluate_norepeat_matches import check_for_duplicate_matches


# --- Pytest Test for Success is working as intended ---
def test_does_not_find_duplicates_with_clean_data(capsys, monkeypatch):
    """
    Prueba si la función finaliza con éxito cuando no hay IDs de
    productos similares repetidos.
    """

    # Mock de datos no duplicados
    mock_data = json.dumps([
        {"productIdInSupermarket": "123", "denomination": "Product A", "similar_products": [{"productIdInSupermarket": "345"}]},
        {"productIdInSupermarket": "456", "denomination": "Product B", "similar_products": [{"productIdInSupermarket": "645"}]}
    ])

    mock_file = io.StringIO(mock_data)
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: mock_file)

    check_for_duplicate_matches("dummy.json")

    captured = capsys.readouterr()
    output = captured.out

    # La assertion ahora busca el mensage de exito.
    # El test PASA porque la funcion se comporta como se esperaba (no encuentra duplicados)
    assert "Success: No repeated similar product IDs were found" in output
    assert "Action Required" not in output


# --- Pytest Test for Finding Duplicates ---

def test_finds_duplicates_with_bad_data(capsys, monkeypatch):
    """
    Tests if the function correctly identifies a similar_product_id that is
    repeated across different rows, using pytest's monkeypatch fixture.
    """
    # Mock de datos duplicados
    mock_data = json.dumps([
        {"productIdInSupermarket": "123", "denomination": "Product A", "similar_products": [{"productIdInSupermarket": "345"}]},
        {"productIdInSupermarket": "456", "denomination": "Product B", "similar_products": [{"productIdInSupermarket": "345"}]}
    ])

    # Simulate the file object that `open` would return.
    # We use io.StringIO to treat a string as a file.
    mock_file = io.StringIO(mock_data)

    # NOTE
    # Mock the built-in `open` function using pytest's monkeypatch fixture.
    # This is the core of our unit test's isolation. When `check_for_duplicate_matches`
    # attempts to open a file, this patch intercepts the call. Instead of accessing
    # the filesystem, it returns our pre-defined `mock_file` StringIO object.
    # This makes the file path argument ("dummy_path.json", etc.) irrelevant for this test.
    monkeypatch.setattr('builtins.open', lambda *args, **kwargs: mock_file)

    check_for_duplicate_matches("dummy.json")

    captured = capsys.readouterr()
    output = captured.out

    # Assert that the output contains the expected strings for the repeated ID.
    assert "[!] Repeated Similar ID Found: '345'" in output
    assert "- First seen in Row 1" in output
    assert "- Repeated in Row 2" in output
    assert "Action Required: One or more similar product IDs are repeated in the report." in output


# --- Execution ---
# To run this test, you would typically use the pytest command in your terminal.
# 1. Make sure you have pytest installed: pip install pytest
# 2. Save this file (e.g., as 'test_script.py').
# 3. Run pytest from your terminal in the same directory: pytest

if __name__ == '__main__':
    # You can still run the main function directly to check your actual file.
    print("--- Running Duplicate Check on 'rawreport.json' ---")

    file_to_check = Path("input/rawreport.json")
    check_for_duplicate_matches(file_to_check)

