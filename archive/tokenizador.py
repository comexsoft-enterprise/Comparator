from transformers import AutoTokenizer, AutoModel
import torch
import faiss
import numpy as np
import json

# Load pre-trained model and tokenizer from local directory
model_path = './models/all-MiniLM-L6-v2'
tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModel.from_pretrained(model_path)

# Load pre-trained model and tokenizer from local directory
model_path = './models/all-MiniLM-L6-v2'
tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModel.from_pretrained(model_path)

def get_embedding(text):
    inputs = tokenizer(text, return_tensors='pt', truncation=True, padding=True, max_length=128)
    with torch.no_grad():
        outputs = model(**inputs)
    return outputs.last_hidden_state.mean(dim=1).squeeze().numpy()

def generate_product_concatenation(product):
    measuring_unit = product.get('measuringUnit')

    # Ensure measuringUnit exists and has valid data
    if measuring_unit and measuring_unit.get('value'):
        try:
            # Attempt to convert 'value' to float
            unit_value = float(measuring_unit['value'])
            # Format the string properly
            measuring_unit_str = f"FORMAT: {str(measuring_unit.get('format', 'N/A'))}, AMOUNT: {unit_value} {str(measuring_unit.get('unit', 'N/A'))}"
            unit_price = product['price'] / unit_value
        except (ValueError, TypeError):
            print("Invalid measuring unit value:", measuring_unit['value'])
            unit_price = "N/A"
    else:
        # Fallback if measuring unit is invalid or missing
        unit_price = product['price']  # Assuming price without measurement unit

    # Return the concatenated product string
    if measuring_unit and measuring_unit.get('value'):
        return f"{product['denomination']}, BRAND: {product['brand']}, UNIT PRICE: {str(unit_price)}, {measuring_unit_str}"
    else:
        return f"{product['denomination']}, BRAND: {product['brand']}, UNIT PRICE: {str(unit_price)}"

def process_products(products):
    for i, product in enumerate(products):
        product_concatenation = generate_product_concatenation(product)
        product['product_concatenation'] = product_concatenation
        if not product_concatenation or product_concatenation == 'N/A':
            continue
        product['product_embedding'] = get_embedding(product_concatenation).tolist()
    return products

def find_most_similar(embeddings, query_embedding, top_k=5):
    index = faiss.IndexFlatL2(len(query_embedding))
    index.add(np.array(embeddings).astype('float32'))
    D, I = index.search(np.array([query_embedding]).astype('float32'), top_k)
    return I[0]

# Example usage
if __name__ == "__main__":
    # Load JSON input from file
    with open('procesado_eroski.json', 'r', encoding='utf-8') as file:
        products = json.load(file)

    print(len(products))

    processed_products = process_products(products)

    # Save processed products to file
    with open('eroski_processed_products.json', 'w', encoding='utf-8') as file:
        json.dump(processed_products, file, ensure_ascii=False, indent=4)
