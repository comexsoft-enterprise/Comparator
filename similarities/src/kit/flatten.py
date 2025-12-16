import json

EXECUTION_ID = "000000000000000002"

INPUT_FILEPATH = f"./results/{EXECUTION_ID}/rawreport.json"
OUTPUT_FILEPATH = f"./results/{EXECUTION_ID}/report.json"

def full_flat():
    # Abrir y cargar el JSON original
    with open(INPUT_FILEPATH, "r") as file:
        data = json.load(file)

    # Aplanar cada elemento del JSON si es una lista de objetos
    flattened_data = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                flattened_data.append(flattenjson(item, "."))
            else:
                print(f"Warning: Skipping non-dictionary item: {item}")
    elif isinstance(data, dict):  # Si el JSON no es una lista, solo aplanar el diccionario
        flattened_data = [flattenjson(data, '.')]
    else:
        raise ValueError("El JSON debe ser una lista o un diccionario.")

    # Guardar el JSON aplanado
    with open(OUTPUT_FILEPATH, "w") as file:
        json.dump(flattened_data, file, indent=4, ensure_ascii=False)

    print(f"El archivo JSON ha sido aplanado y guardado como {OUTPUT_FILEPATH}.")


def flattenjson(b, delim='.'):
    """
    Aplana un JSON anidado.
    :param b: El JSON a aplanar (diccionario).
    :param delim: El delimitador para unir claves.
    :return: Diccionario con estructura aplanada.
    """
    val = {}
    for i in b.keys():
        if isinstance(b[i], dict):  # Si el valor es otro diccionario
            get = flattenjson(b[i], delim)
            for j in get.keys():
                val[i + delim + j] = get[j]  # Une las claves
        elif isinstance(b[i], list):  # Si el valor es una lista
            for idx, elem in enumerate(b[i]):
                if isinstance(elem, dict):  # Aplana cada elemento si es un diccionario
                    get = flattenjson(elem, delim)
                    for j in get.keys():
                        val[f"{i}{delim}{idx}{delim}{j}"] = get[j]
                else:  # Almacena valores simples directamente
                    val[f"{i}{delim}{idx}"] = elem
        else:
            val[i] = b[i]
    return val


if __name__ == "__main__":
    full_flat()

