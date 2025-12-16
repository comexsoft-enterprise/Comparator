import faiss
import numpy as np
import json

def create_faiss_index(embeddings):
    # Create the FAISS index for L2 distance
    index = faiss.IndexFlatL2(len(embeddings[0]))
    index.add(np.array(embeddings).astype('float32'))
    return index

def find_most_similar(index, query_embedding, top_k=10):
    D, I = index.search(np.array([query_embedding]).astype('float32'), top_k)
    return D[0], I[0]

def add_similar_products(eroski_products, second_supermarket_products, productos_resultado_makro, makro_brands):
    # Crear el índice FAISS para los embeddings de Makro
    top_k = 1
    for i, eroski_product in enumerate(eroski_products):
        query_embedding = eroski_product['product_embedding']
        eroski_brand = eroski_product['brand']  # Convertir la marca a minúsculas para comparación

        filtered_second_products = []
        if eroski_brand in eroski_brands:
            filtered_second_products = [product for product in second_supermarket_products if product["brand"] in makro_brands]
        else:
            filtered_second_products = [product for product in second_supermarket_products if product["brand"] not in makro_brands]

        index = create_faiss_index([product['product_embedding'] for product in filtered_second_products])

        # Buscar productos similares
        distances, similar_indices = find_most_similar(index, query_embedding, top_k)

        if i % 20 == 0:
            print("---------------------------------------------------")
        print(similar_indices)

        # Filtrar los productos de Makro que tienen marcas en makro_brands y que coincidan con la de Eroski

        similar_products = [
            {
                'productIdInSupermarket': filtered_second_products[idx]['productIdInSupermarket'],
                'denomination': filtered_second_products[idx]['denomination'],
                'product_concatenation': filtered_second_products[idx]['product_concatenation'],
                'distance': float(distances[j]),
                'brand': filtered_second_products[idx]['brand']
            }
            for j, idx in enumerate(similar_indices)
        ]

        eroski_product['similar_makro_products'] = similar_products

    return eroski_products

# Example usage
if __name__ == "__main__":
    # Cargar los productos desde los archivos JSON

    eroski_brands = ["Eroski Bio/ECO + E.Natur BIO", "SELEQTIA", "EROSKI", "BELLE", "Belle", "Arnalte", "NO BRAND", "-", " ", "granel", "n/a", "BRAND:none", "Sannia", "SERVIHOSTEL", "B. EROSKI", "BBQ EROSKI", "D.O. EROSKI BIO",  "D.O. EROSKI SELEQTIA",  "D.O.P. EROSKI", "D.O.P. EROSKI NATUR", "D.O.P. EROSKI SABORES", "EROSKI BASIC", "EROSKI BIO", "EROSKI BIO/ECO", "EROSKI ECO", "EROSKI MAESTRO", "EROSKI NATUR", "EROSKI NATUR BIO", "EROSKI NATUR GGN", "EROSKI SELEQTIA", "EROSKI VEGGIE", "EROSKI ZERO", "EUSKAL OKELA EROSKI", "I.G.P. EROSKI", "I.G.P. EROSKI BIO", "IGP EROSKI", "IGP EROSKI NATUR", "IGP MG EROSKI", "MSC EROSKI", "MSC EROSKI SELEQTIA", "WC EROSKI", "XXL EROSKI"]
    makro_brands = ["MAKRO CHEF", "RIOBA", "MAKRO PREMIUM", "METRO CHEF", "ARO", "RAMA PROFESSIONAL", "METRO PREMIUM","makro PROFESSIONAL", "METRO PROFESSIONAL", "SIGMA", "Tarrington House", "NO BRAND", "-", " ", "granel", "n/a", "BRAND:none", "MAKRO", "METRO CHEF BIO"]
    with open('eroski_processed_products.json', 'r', encoding='utf-8') as file:
        processed_eroski_products = json.load(file)

    filtered_processed_eroski_products = {producto["productIdInSupermarket"]: producto for producto in processed_eroski_products}
    productos_resultado_eroski = list(filtered_processed_eroski_products.values())


    with open('makro_processed_products.json', 'r', encoding='utf-8') as file:
        processed_makro_products = json.load(file)

    filtered_processed_makro_products = {producto["productIdInSupermarket"]: producto for producto in processed_makro_products}
    productos_resultado_makro = list(filtered_processed_makro_products.values())


    # Agregar productos similares filtrados de Makro a los productos de Eroski
    updated_eroski_products = add_similar_products(productos_resultado_eroski, productos_resultado_makro, eroski_brands, makro_brands)

    # Guardar los productos de Eroski actualizados en un nuevo archivo JSON
    with open('computed_eroski_makro_similarities_3.json', 'w', encoding='utf-8') as file:
        json.dump(updated_eroski_products, file, ensure_ascii=False, indent=4)

    print("Updated Eroski products with similar Makro products saved to 'computed_eroski_makro_similarities.json'")
