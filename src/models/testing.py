import os
from openai import OpenAI
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = "text-embedding-3-large"

# Inicializa el cliente con tu API key
client = OpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    base_url=AZURE_OPENAI_ENDPOINT
)

# Textos de ejemplo para comparar
textos = [
    "",
    "Leche desnatada sin lactosa 1L",
    "Queso manchego curado 250g",
    "Yogur natural sin azúcar pack 4 unidades",
    "Producto lácteo leche semidesnatada"
]

print("="*70)
print("COMPARACIÓN DE SIMILITUD DE COSENO CON OPENAI EMBEDDINGS")
print("="*70)

# Generar embeddings para todos los textos
print(f"\n🤖 Generando embeddings para {len(textos)} textos...")
embeddings = []
for i, texto in enumerate(textos, 1):
    print(f"  [{i}/{len(textos)}] {texto[:50]}...")
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texto
    )
    embeddings.append(response.data[0].embedding)

embeddings = np.array(embeddings)
print(f"\n✅ Embeddings generados: {embeddings.shape[0]} textos x {embeddings.shape[1]} dimensiones")

# Calcular similitud de coseno entre el primer texto y todos los demás
texto_referencia = textos[0]
print(f"\n📊 TEXTO DE REFERENCIA:")
print(f"   '{texto_referencia}'")
print(f"\n🔍 SIMILITUD DE COSENO CON OTROS TEXTOS:")
print("-"*70)

# Calcular similitudes
similarities = cosine_similarity([embeddings[0]], embeddings[1:])[0]

# Mostrar resultados ordenados por similitud
resultados = [(textos[i+1], similarities[i]) for i in range(len(similarities))]
resultados.sort(key=lambda x: x[1], reverse=True)

for i, (texto, sim) in enumerate(resultados, 1):
    # Barra de progreso visual
    bar_length = int(sim * 50)
    bar = "█" * bar_length + "░" * (50 - bar_length)
    
    # Color según similitud
    if sim >= 0.8:
        emoji = "🟢"
    elif sim >= 0.6:
        emoji = "🟡"
    else:
        emoji = "🔴"
    
    print(f"\n{emoji} Ranking #{i} - Similitud: {sim:.4f}")
    print(f"   Texto: '{texto}'")
    print(f"   [{bar}] {sim*100:.1f}%")

# Matriz completa de similitudes
print(f"\n\n📈 MATRIZ COMPLETA DE SIMILITUDES:")
print("-"*70)
matriz_similitud = cosine_similarity(embeddings)

print(f"\n{'':30}", end="")
for i in range(len(textos)):
    print(f"T{i+1:2d}  ", end="")
print()

for i, texto in enumerate(textos):
    print(f"{texto[:28]:30}", end="")
    for j in range(len(textos)):
        sim = matriz_similitud[i][j]
        if i == j:
            print(f"{'1.00':>5}", end="")
        else:
            print(f"{sim:5.2f}", end="")
    print()

print("\n" + "="*70)
print("✅ Comparación completada")
print("="*70)
