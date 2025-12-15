import pandas as pd
import os
from openai import OpenAI
import json
import time
from typing import Dict, List, Optional
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm
from dataclasses import dataclass

from dotenv import load_dotenv
import logging
from data.schemas.taxonomy import ColumnRegistry
from data.schemas.categories import InternalType, InternalCategoryFood, InternalCategoryNonFood, get_category_subcategory_map
from config.settings import PROJECT_ROOT
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Validate environment variables
logging.info("Validating environment variables...")
api_key = os.getenv("AZURE_OPENAI_API_KEY")
api_version = os.getenv("AZURE_OPENAI_API_VERSION")
api_base = os.getenv("AZURE_OPENAI_API_BASE")
api_type = os.getenv("AZURE_OPENAI_API_TYPE")

if not api_key:
    raise ValueError("Missing AZURE_OPENAI_API_KEY in environment variables.")
if not api_version:
    raise ValueError("Missing AZURE_OPENAI_API_VERSION in environment variables.")
if not api_base:
    raise ValueError("Missing AZURE_OPENAI_API_BASE in environment variables.")

logging.info("Environment variables loaded successfully.")

# Test connection to Azure OpenAI
try:
    client = OpenAI(
        api_key=api_key,
        base_url=api_base
    )
    logging.info("Successfully connected to Azure OpenAI.")
except Exception as e:
    logging.error(f"Error connecting to Azure OpenAI: {e}")


@dataclass
class ChunkResult:
    """Data class to store chunk processing results."""
    chunk_index: int
    chunk_data: pd.DataFrame
    success: bool
    rows_processed: int
    fields_completed: int
    error: Optional[str] = None
    duration: float = 0.0


class CSVCompleter:
    def __init__(
        self, 
        api_key: str, 
        deployment_name: str = "gpt-5-nano",
        api_base: str = None
    ):
        """
        Args:
            api_key: Azure OpenAI API key
            deployment_name: Azure OpenAI deployment name
            api_version: Azure OpenAI API version 
            api_base: Azure OpenAI endpoint/base URL 
            api_type: API type, usually "azure"
        """
        self.client = OpenAI(
            api_key=api_key,
            base_url=api_base
        )
        self.deployment_name = deployment_name
        self.lock = Lock()  # Para logging thread-safe
        self.internal_types = [t.value for t in InternalType]
        self.internal_categories_food = [cat.value for cat in InternalCategoryFood]
        self.internal_categories_non_food = [cat.value for cat in InternalCategoryNonFood]


        # Load the column registry
        registry_path = PROJECT_ROOT / "data" / "schemas" / "column_registry.json"
        self.column_registry = ColumnRegistry.load_from_json(registry_path)
        
        # Detectar precios según el modelo
        self.pricing = self._get_model_pricing(deployment_name)
        
        # Token tracking and cost calculation
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens_used = 0
        self.total_cached_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
    
    def _get_model_pricing(self, deployment_name: str) -> dict:
        """
        Detecta y retorna los precios según el modelo deployment.
        Precios en USD por millón de tokens.
        
        Returns:
            dict: {
                'input': precio por 1M tokens de entrada,
                'cached': precio por 1M tokens cacheados,
                'output': precio por 1M tokens de salida,
                'model_name': nombre del modelo detectado
            }
        """
        deployment_lower = deployment_name.lower()
        
        # GPT-5 nano (más barato)
        if 'nano' in deployment_lower or '5-nano' in deployment_lower or 'gpt-5-nano' in deployment_lower:
            return {
                'input': 0.050,
                'cached': 0.005,
                'output': 0.400,
                'model_name': 'GPT-5 nano'
            }
        
        # GPT-4.1-mini
        elif 'mini' in deployment_lower or '4.1-mini' in deployment_lower:
            return {
                'input': 0.40,
                'cached': 0.10,
                'output': 1.60,
                'model_name': 'GPT-4.1-mini Global'
            }
        
        # GPT-4.1 (standard)
        elif '4.1' in deployment_lower or 'gpt-4.1' in deployment_lower:
            return {
                'input': 2.00,
                'cached': 0.50,
                'output': 8.00,
                'model_name': 'GPT-4.1 Global'
            }
        
        # GPT-4o (fallback para compatibilidad)
        elif '4o' in deployment_lower or 'gpt-4o' in deployment_lower:
            return {
                'input': 2.50,
                'cached': 1.25,
                'output': 10.00,
                'model_name': 'GPT-4o'
            }
        
        # Default: GPT-4.1 pricing
        else:
            logger.warning(f"Modelo no reconocido: {deployment_name}. Usando precios de GPT-4.1 por defecto.")
            return {
                'input': 2.00,
                'cached': 0.50,
                'output': 8.00,
                'model_name': 'GPT-4.1 Global (default)'
            }

    def print_cost_summary(self):
        """Imprime un resumen detallado de costos y uso de tokens."""
        print("\n" + "="*80)
        print("💰 RESUMEN DE COSTOS Y TOKENS")
        print("="*80)
        print(f"🤖 Modelo utilizado:                {self.pricing['model_name']}")
        print(f"🔢 Total de llamadas al LLM:        {self.call_count}")
        print(f"📤 Total tokens de entrada:         {self.total_prompt_tokens:,}")
        print(f"📥 Total tokens de salida:          {self.total_completion_tokens:,}")
        print(f"📊 Total tokens usados:             {self.total_tokens_used:,}")
        
        if self.total_cached_tokens > 0:
            cache_percentage = (self.total_cached_tokens / self.total_prompt_tokens * 100) if self.total_prompt_tokens > 0 else 0
            print(f"🔄 Total tokens cacheados:          {self.total_cached_tokens:,}")
            print(f"💾 Porcentaje de cache:             {cache_percentage:.1f}%")
            
            # Calcular ahorro por cache (diferencia entre precio normal y precio con cache)
            savings_per_million = self.pricing['input'] - self.pricing['cached']
            savings = (self.total_cached_tokens / 1_000_000) * savings_per_million
            print(f"💵 Ahorro por cache:                ${savings:.4f}")
        
        print(f"💰 Costo total estimado:            ${self.total_cost:.4f}")
        
        if self.call_count > 0:
            avg_tokens = self.total_tokens_used / self.call_count
            avg_cost = self.total_cost / self.call_count
            print("\n📈 PROMEDIOS POR LLAMADA:")
            print(f"   Tokens promedio:                 {avg_tokens:,.1f}")
            print(f"   Costo promedio:                  ${avg_cost:.6f}")
        
        # Desglose de costos (dinámico según el modelo)
        non_cached_tokens = self.total_prompt_tokens - self.total_cached_tokens
        input_cost = (non_cached_tokens / 1_000_000) * self.pricing['input']
        cached_cost = (self.total_cached_tokens / 1_000_000) * self.pricing['cached']
        output_cost = (self.total_completion_tokens / 1_000_000) * self.pricing['output']
        
        print(f"\n💵 DESGLOSE DE COSTOS ({self.pricing['model_name']}):")
        print(f"   Input (no cacheado @ ${self.pricing['input']:.2f}/1M):  ${input_cost:.4f}")
        print(f"   Input (cacheado @ ${self.pricing['cached']:.2f}/1M):     ${cached_cost:.4f}")
        print(f"   Output (@ ${self.pricing['output']:.2f}/1M):             ${output_cost:.4f}")
        print(f"   {'─'*44}")
        print(f"   TOTAL:                           ${self.total_cost:.4f}")
        
        print("="*80 + "\n")
    
    def reset_cost_tracking(self):
        """Reinicia los contadores de costos y tokens."""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens_used = 0
        self.total_cached_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
        logging.info("Cost tracking reset")

    def load_csv(self, filepath: str) -> pd.DataFrame:
        """Load CSV file with semicolon separator and robust parsing."""
        try:
            # Try standard parsing first
            return pd.read_csv(filepath, sep=';', dtype=str, encoding='utf-8')
        except Exception as e:
            logging.error(f"Standard parsing failed: {e}")
            logging.info("Attempting robust parsing with python engine...")
            try:
                # Use python engine with more permissive settings
                return pd.read_csv(
                    filepath, 
                    sep=';', 
                    dtype=str, 
                    encoding='utf-8',
                    engine='python',
                    quoting=csv.QUOTE_ALL,
                    on_bad_lines='skip'
                )
            except Exception as e2:
                logging.error(f"Python engine parsing failed: {e2}")
                logging.info("Attempting final fallback with minimal parsing...")
                # Last resort: try with different quote handling
                return pd.read_csv(
                    filepath,
                    sep=';',
                    dtype=str,
                    encoding='utf-8',
                    engine='python',
                    quotechar='"',
                    doublequote=True,
                    escapechar='\\',
                    on_bad_lines='skip'
                )

    def load_csv_chunked(self, filepath: str, chunk_size: int = 10):
        """Yield DataFrame chunks for scalable processing"""
        try:
            for chunk in pd.read_csv(filepath, sep=';', dtype=str, encoding='utf-8', chunksize=chunk_size, engine='python', quoting=csv.QUOTE_ALL, on_bad_lines='skip'):
                yield chunk
        except Exception as e:
            logging.error(f"Error loading CSV in chunks: {e}")
            raise

    def save_csv(self, df: pd.DataFrame, filepath: str):
        df.to_csv(filepath, sep=';', index=False)

    def get_empty_fields(self, row: pd.Series) -> List[str]:
        return [col for col in row.index if col.lower() != 'id' and (pd.isna(row[col]) or str(row[col]).strip() == '')]    

    def _get_subcategories_prompt_section(self, category_subcategory_map: Dict) -> str:
        """
        Generate the subcategory section of the prompt with all categories and their subcategories.
        
        Args:
            category_subcategory_map: Dictionary mapping category names to subcategory enum classes
            
        Returns:
            str: Formatted prompt section with subcategories organized by category
        """
        subcategory_section = "\n            2.3. SUBCATEGORY (choose based on category):\n\n"
        
        for category, subcategory_enum in category_subcategory_map.items():
            subcategories = [sc.value for sc in subcategory_enum]
            subcategory_section += f"            If category is '{category}', choose from:\n"
            subcategory_section += "\n".join(f"               - {sc}" for sc in subcategories)
            subcategory_section += "\n\n"
        
        return subcategory_section

    def create_completion_prompt_chunk(self, df_chunk: pd.DataFrame, input_columns: List[str], output_columns: List[str]) -> tuple:
        """
        Creates the whole context to complete the template of columns that is provided, using the given information for a complete chunk of rows using a template approach.
        Returns: (context, prompt) where the context is the previous instructions and the prompt is the actual input to complete into the current chunk.
        """

        # Build subcategory map for prompt
        category_subcategory_map = get_category_subcategory_map()

        # Generate the subcategory section dynamically
        subcategory_prompt_section = self._get_subcategories_prompt_section(category_subcategory_map)

        context = f"""You are a highly skilled product data completion assistant. You will be provided with data containing multiple products, and your task is to adapt and 
                        complete this data to match the structure of a given template. Use the provided information to complete missing details. 
                        If the information is not explicitly given, infer the most probable and reasonable value 
                        based on the product name, description, category and other fields.
                        Only leave a field empty if it is truly impossible to infer anything coherent. Ensure all fields are filled with concise, accurate, and relevant values based strictly on the given data.

        Template columns: {json.dumps(output_columns)}

        Instructions:
        The output must be translate in English no matter the original language.

        1. For EACH product, just fill the fields with the existing data in the same row, if a information does not exist do not fill it.
        2. Categorize the product in the following way:

            You must provide the categorization in the following JSON format:
            {{
                "type": "<type>",
                "internal_category": "<internal_category>",
                "internal_subcategory": "<internal_subcategory>"
            }}

            VALID OPTIONS:

            2.1. TYPE (choose one):
            {chr(10).join(f"   - {t}" for t in self.internal_types)}

            2.2. INTERNAL_CATEGORY (choose based on type):
            
            If type is "Food", choose from:
            {chr(10).join(f"   - {c}" for c in self.internal_categories_food)}
            
            If type is "Non-Food", choose from:
            {chr(10).join(f"   - {c}" for c in self.internal_categories_non_food)}

            2.3. internal_subcategory (choose based on internal_category, BUT MUST BE ONE OF THE FOLLOWING):
            {subcategory_prompt_section}
            

        3. Return ONLY a valid JSON object with no markdown formatting, explanations, or code blocks.
        4. JSON structure:
        - Keys: Row indices as string (0, 1, 2, etc.)
        - Values: Objects containing ALL template column fields with their values
        5. Here is the context for each column in the template to be used as reference:

        {json.dumps(self.column_registry.get_description_from_columns(output_columns), indent=2)}

        Example response format (ALL fields must be present for each product):
        {{
        "0": {{
            "category": "Food/Dairy/Cheese/Semi-cured cheese",
            "brand": "President",
            "product_name": "Semi-cured cheese wedge",
            "product_type": "Fresh",
            "description": "Semi-cured cheese wedge made with pasteurized cow's milk. Smooth and creamy texture with mild flavor. Perfect for sandwiches and tapas.",
            "format": "Wedge",
            "unit_measure": "g",
            "weight": "250",
            "offer": null,
            "vat": "10",
            "ingredients": "Pasteurized cow's milk (90%), salt (2%), lactic ferments (1%), rennet (0.5%)",
            "components": null,
            "first_level_components": "Cow's milk",
            "first_component_extra_info": "90%, Pasteurized",
            "second_level_components": "Salt",
            "second_component_extra_info": "2%",
            "conservation_characteristics": "Keep refrigerated between +2ºC and +8ºC. Once opened, consume within 5 days.",
            "manufacturer": "Lactalis Iberia S.L., Pol. Ind. La Ermita, 28250 Torrelodones, Madrid",
            "country_origin": "ES",
            "colour": null,
            "model": null,
            "allergens": "milk"
        }},
        "1": {{
            "category": "Non-Food/Electronics/Smartphones/Premium smartphones",
            "brand": "Samsung",
            "product_name": "Galaxy S24 Ultra",
            "product_type": "Electronics",
            "description": "Samsung Galaxy S24 Ultra 5G smartphone with 6.8-inch Dynamic AMOLED display, Snapdragon 8 Gen 3 processor, 12GB RAM, 256GB storage, quad camera system with 200MP main sensor, S Pen included, 5000mAh battery with fast charging.",
            "format": "Box",
            "unit_measure": "ud",
            "weight": "1",
            "offer": "10% discount",
            "vat": "21",
            "ingredients": null,
            "components": "Snapdragon 8 Gen 3, 12GB RAM, 256GB storage, 6.8-inch AMOLED display, 200MP camera, S Pen, 5000mAh battery",
            "first_level_components": "Snapdragon 8 Gen 3",
            "first_component_extra_info": "Octa-core processor",
            "second_level_components": "12GB RAM",
            "second_component_extra_info": "LPDDR5X",
            "conservation_characteristics": "Store in a cool, dry place away from direct sunlight. Avoid extreme temperatures.",
            "manufacturer": "Samsung Electronics Co., Ltd., Suwon, South Korea",
            "country_origin": "KR",
            "colour": "Titanium Gray",
            "model": "S24 Ultra",
            "allergens": null
        }}
        }}

        CRITICAL: 
        - Description, product_name, product_type columns MUST be completed.
        - For every product, you MUST return ALL template columns.
        - Prefer inferring a reasonable value over leaving the field empty.
        - Only leave a field empty when there is absolutely no signal in the row.
        - Your response must be ONLY valid JSON. Do not include ```json``` markers, explanations, or any other text.
        - EVERY product must have ALL template columns with values (existing or completed)
        - Numeric keys like 0,1,2 MUST be returned as "0", "1", "2".
        
        """
        prompt = f"Complete the following product information:\n{df_chunk[input_columns].to_dict(orient='records')}"

        return context, prompt

    def complete_chunk(self, df_chunk: pd.DataFrame) -> pd.DataFrame:
        """
        Procesa un chunk completo con una sola llamada al LLM.
        Crea un nuevo DataFrame desde cero con las completions del LLM.
        """

        source_only_columns = [col.name for col in self.column_registry.get_llm_source_only_columns()]
        target_only_columns = [col.name for col in self.column_registry.get_llm_target_only_columns()]
        both_columns = [col.name for col in self.column_registry.get_llm_both_columns()]
        excluded_columns = [col.name for col in self.column_registry.get_not_llm_complete_columns()]

        output_columns = both_columns + target_only_columns
        input_columns = both_columns + source_only_columns
        not_in_output_columns = source_only_columns + excluded_columns

        neo4j_columns = {col.name for col in self.column_registry.get_neo4j_columns()}
        output_columns = neo4j_columns.intersection(output_columns)

        not_in_output_and_neo4j_columns = neo4j_columns.intersection(not_in_output_columns)
        print(f"Not in output but in Neo4j columns: {not_in_output_and_neo4j_columns}")

        input_columns = list(input_columns)
        output_columns = list(output_columns)
        not_in_output_and_neo4j_columns = list(not_in_output_and_neo4j_columns)

        # Generate row_index column with original indices
        filling_df_chunk = df_chunk.copy()
        filling_df_chunk['row_index'] = filling_df_chunk.index

        logging.debug(f"Filling chunk has columns: {filling_df_chunk.columns.tolist()}")
        
        # Crear el prompt para el chunk
        context, prompt = self.create_completion_prompt_chunk(filling_df_chunk, input_columns, output_columns)
        logging.debug(f"Prompt for chunk: {context, prompt}")
        try:
            # Realizar la llamada al LLM usando OpenAI
            response = self.client.responses.create(
                model=self.deployment_name,
                input=[
                    {"role": "system", "content": context},
                    {"role": "user", "content": prompt}
                ],
                reasoning={
                    "effort": "low"
                }, text={
                    "verbosity": "low"
                }
            )

            logger.info("LLM API call completed successfully. Processing response...")

            # ==================================Obtener los tokens cachados en el servidor===================================================================================
            usage = getattr(response, "usage", None)
            input_tokens = output_tokens = total_tokens = cached_tokens = reasoning_tokens = None
            
            if usage is not None:
                # Nueva estructura: input_tokens, output_tokens, total_tokens
                input_tokens = getattr(usage, "input_tokens", None) or (usage.get("input_tokens") if isinstance(usage, dict) else None)
                output_tokens = getattr(usage, "output_tokens", None) or (usage.get("output_tokens") if isinstance(usage, dict) else None)
                total_tokens = getattr(usage, "total_tokens", None) or (usage.get("total_tokens") if isinstance(usage, dict) else None)

                # Extraer cached_tokens de input_tokens_details
                input_details = getattr(usage, "input_tokens_details", None) or (usage.get("input_tokens_details") if isinstance(usage, dict) else None)
                if input_details is not None:
                    if hasattr(input_details, "cached_tokens"):
                        cached_tokens = input_details.cached_tokens
                    elif isinstance(input_details, dict):
                        cached_tokens = input_details.get("cached_tokens")
                
                # Extraer reasoning_tokens de output_tokens_details (si existe)
                output_details = getattr(usage, "output_tokens_details", None) or (usage.get("output_tokens_details") if isinstance(usage, dict) else None)
                if output_details is not None:
                    if hasattr(output_details, "reasoning_tokens"):
                        reasoning_tokens = output_details.reasoning_tokens
                    elif isinstance(output_details, dict):
                        reasoning_tokens = output_details.get("reasoning_tokens")
            
            # Calcular costos dinámicamente según el modelo
            # Los precios se detectan automáticamente en __init__ según el deployment_name
            
            input_tokens = input_tokens or 0
            output_tokens = output_tokens or 0
            total_tokens = total_tokens or 0
            cached_tokens = cached_tokens or 0
            reasoning_tokens = reasoning_tokens or 0
            
            # Calcular tokens de entrada no cacheados
            non_cached_input_tokens = input_tokens - cached_tokens
            
            # Costo de tokens de entrada (sin cachear)
            input_cost = (non_cached_input_tokens / 1_000_000) * self.pricing['input']
            
            # Costo de tokens cacheados
            cached_cost = (cached_tokens / 1_000_000) * self.pricing['cached']
            
            # Costo de tokens de salida (incluye reasoning_tokens)
            output_cost = (output_tokens / 1_000_000) * self.pricing['output']
            
            # Costo total de esta llamada
            call_cost = input_cost + cached_cost + output_cost
            
            # Actualizar contadores acumulativos
            with self.lock:  # Thread-safe
                self.call_count += 1
                self.total_prompt_tokens += input_tokens
                self.total_completion_tokens += output_tokens
                self.total_tokens_used += total_tokens
                self.total_cached_tokens += cached_tokens
                self.total_cost += call_cost
            
            # Log detallado (no file output)
            logger.info(f"[TOKENS] input={input_tokens} output={output_tokens} total={total_tokens} | cached={cached_tokens} reasoning={reasoning_tokens}")
            logger.info(f"[COST] this_call=${call_cost:.6f} | non_cached_input=${input_cost:.6f} cached_input=${cached_cost:.6f} output=${output_cost:.6f}")
            logger.info(f"[ACCUMULATED] calls={self.call_count} total_cost=${self.total_cost:.4f}")
            # ==========================================================================================================================================

            # Parsear la respuesta del LLM
            content = response.output[1].content[0].text.strip()

            # Remove markdown code blocks if present (```json ... ```)
            if content.startswith("```"):
                # Find the first newline after ```json or ```
                first_newline = content.find('\n')
                if first_newline != -1:
                    content = content[first_newline + 1:]
                # Remove trailing ```
                if content.endswith("```"):
                    content = content[:-3].strip()
            
            # Try to parse JSON
            try:
                llm_output = json.loads(content)
                logging.debug(f"LLM output parsed: {llm_output} and type: {type(llm_output)}")
                
                if not isinstance(llm_output, dict):
                    logger.error(f"LLM response is not a dictionary. Type: {type(llm_output)}")

                    return df_chunk[output_columns + not_in_output_and_neo4j_columns].copy()

                # Create a new DataFrame from llm_output
                rows_list = []
                for seq_idx_str in sorted(llm_output.keys(), key=int):
                    row_data = llm_output[seq_idx_str]
                    rows_list.append(row_data)

                result_chunk = pd.DataFrame(rows_list, columns=output_columns)
                # Add back the preserved keys
                if not_in_output_and_neo4j_columns:
                    result_chunk = pd.concat([df_chunk[not_in_output_and_neo4j_columns].reset_index(drop=True), result_chunk], axis=1)

                # Ordenar result_chunk de forma personalizada las columnas, en caso de no tener columnas del template_columns_coherent no las añade.
                template_columns_coherent = ['uuid', 'siid', 'id', 'ean', 'gtin', 'url', 'category','internaltype', 'internalcategory', 'internalsubcategory', 'product_type', 'brand', 'product_name', 'model', 'description', 'quantity', 'measure_value', 'format', 'colour', 'weight', 'unit_measure', 'ingredients', 'allergens', 'first_level_components', 'first_component_extra_info', 'second_level_components', 'second_component_extra_info', 'all_components', 'conservation_characteristics', 'manufacturer', 'country_origin', 'store', 'country', 'postcode', 'price', 'price_without_vat', 'vat', 'currency', 'offer', 'price_with_offer', 'shipping_cost', 'days_of_shipping', 'instalation_cost']
                # Filtrar las columnas que están en el DataFrame y en la lista
                existing_columns_in_order = [col for col in template_columns_coherent if col in result_chunk.columns]

                # Añadir las columnas que están en el DataFrame pero no en la lista
                additional_columns = [col for col in result_chunk.columns if col not in template_columns_coherent]

                # Crear el orden final
                final_column_order = existing_columns_in_order + additional_columns

                # Reordenar el DataFrame
                result_chunk = result_chunk[final_column_order]

                return result_chunk

            except json.JSONDecodeError as json_err:
                logger.error(f"JSON parsing error: {json_err}")
                print(f"\033[94m{content}\033[0m")
                raise
        except Exception as e:
            logger.error(f"Error processing chunk: {e}")
 
        
    def _process_chunk_with_metadata(self, chunk_data: tuple) -> ChunkResult:
        """
        Process a single chunk and return metadata.
        
        Args:
            chunk_data: Tuple of (chunk_index, chunk_dataframe)
            
        Returns:
            ChunkResult: Result object with processing metadata
        """

        chunk_index, chunk = chunk_data
        start_time = time.time()

        try:
            with self.lock:
                logger.info(f"Processing row #{chunk_index + 1}")

            # Process the chunk - now returns tuple with success info
            processed_chunk = self.complete_chunk(chunk)

            duration = time.time() - start_time

            return ChunkResult(
                chunk_index=chunk_index,
                chunk_data=processed_chunk,
                success=True,
                rows_processed=len(chunk),
                fields_completed=len(chunk) * (len(processed_chunk.columns) - len(chunk.columns)),
                error=None,
                duration=duration
            )

        except Exception as e:
            duration = time.time() - start_time
            with self.lock:
                logger.error(f"Error processing chunk #{chunk_index + 1}: {e}")
                print(f"\033[94m{chunk}\033[0m")  # Azul

            return ChunkResult(
                chunk_index=chunk_index,
                chunk_data=chunk,  # Return original chunk on error
                success=False,
                rows_processed=len(chunk),
                fields_completed=0,
                error=str(e),
                duration=duration
            )

    def process_csv(
        self, 
        input_filepath: str, 
        output_filepath: str, 
        max_workers: int = 1000,
        use_multithreading: bool = True,
    ):
        """
        Process the entire CSV file, completing empty fields.
        Can process row-by-row (one API call per row) or in chunks.

        Args:
            input_filepath: Path to input CSV file
            output_filepath: Path to save completed CSV file
            delay: Delay in seconds between API calls (only for sequential processing)
            chunk_size: Number of rows per chunk (only used when process_by_row=False)
            max_workers: Maximum number of concurrent threads (default: 20)
            use_multithreading: Whether to use multithreading (default: True)
            process_by_row: Whether to process one row at a time (default: True)
        """
        logging.info(f"Loading CSV from {input_filepath}...")
        
        # ROW-BY-ROW PROCESSING (Nueva implementación)
        logging.info(f"Processing mode: ROW-BY-ROW {'(MULTITHREADED)' if use_multithreading else '(SEQUENTIAL)'}")
        if use_multithreading:
            logging.info(f"Max workers: {max_workers}")
        
        # Load entire CSV
        df = self.load_csv(input_filepath)
        total_rows = len(df)
        logger.info(f"Loaded {total_rows} rows")
        
        # Convert to list of single-row DataFrames
        row_dfs = [df.iloc[[i]] for i in range(len(df))]
        

        results = self._process_rows_multithreaded(row_dfs, max_workers, total_rows)

        
        # Combine results
        processed_rows = [result.chunk_data for result in results if result.success]
        df_final = pd.concat(processed_rows, ignore_index=True)
        
        # Statistics
        successful = sum(1 for r in results if r.success)
        failed = total_rows - successful
        total_duration = sum(r.duration for r in results)
        avg_duration = total_duration / total_rows if total_rows > 0 else 0
        
        # Save
        logging.info(f"\nSaving completed CSV to {output_filepath}...")
        self.save_csv(df_final, output_filepath)
        
        # Print cost summary
        self.print_cost_summary()
        
        # Print summary
        logging.info(f"\n{'='*60}")
        logging.info("PROCESSING SUMMARY:")
        logging.info(f"  - Total rows: {total_rows}")
        logging.info(f"  - Successful rows: {successful}")
        logging.info(f"  - Failed rows: {failed}")
        logging.info(f"  - API calls made: {total_rows}")
        logging.info(f"  - Total processing time: {total_duration:.2f}s")
        logging.info(f"  - Average time per row: {avg_duration:.2f}s")
        if use_multithreading:
            logging.info(f"  - Speedup factor: ~{total_rows * avg_duration / total_duration:.2f}x")
        logging.info(f"{'='*60}")
            

    def _process_csv_multithreaded(
        self, 
        all_chunks: list, 
        max_workers: int, 
        total_chunks: int
    ) -> list:
        """
        Process chunks using multithreading with ThreadPoolExecutor.
        
        Args:
            all_chunks: List of DataFrame chunks
            max_workers: Maximum number of concurrent threads
            total_chunks: Total number of chunks (for progress bar)
            
        Returns:
            List[ChunkResult]: List of processing results
        """
        results = []
        
        # Create enumerated chunks (index, chunk) for tracking
        indexed_chunks = list(enumerate(all_chunks))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_chunk = {
                executor.submit(self._process_chunk_with_metadata, chunk_data): chunk_data[0]
                for chunk_data in indexed_chunks
            }
            
            # Process completed tasks with progress bar
            with tqdm(total=total_chunks, desc="Processing chunks", unit="chunk") as pbar:
                for future in as_completed(future_to_chunk):
                    result = future.result()
                    results.append(result)
                    
                    # Update progress bar with status
                    status = "✓" if result.success else "✗"
                    pbar.set_postfix({
                        'status': status,
                        'chunk': f"{result.chunk_index + 1}/{total_chunks}",
                        'time': f"{result.duration:.2f}s"
                    })
                    pbar.update(1)
        
        return results


    def _process_rows_multithreaded(
        self, 
        row_dfs: list, 
        max_workers: int, 
        total_rows: int
    ) -> list:
        """
        Process rows using multithreading with ThreadPoolExecutor.
        One API call per row.
        
        Args:
            row_dfs: List of single-row DataFrames
            max_workers: Maximum number of concurrent threads
            total_rows: Total number of rows (for progress bar)
            
        Returns:
            List[ChunkResult]: List of processing results
        """
        results = []
        
        # Create enumerated rows (index, row_df) for tracking
        indexed_rows = list(enumerate(row_dfs))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_row = {
                executor.submit(self._process_chunk_with_metadata, row_data): row_data[0]
                for row_data in indexed_rows
            }
            
            # Process completed tasks with progress bar
            with tqdm(total=total_rows, desc="Processing rows", unit="row") as pbar:
                for future in as_completed(future_to_row):
                    result = future.result()
                    results.append(result)
                    
                    # Update progress bar with status
                    status = "✓" if result.success else "✗"
                    pbar.set_postfix({
                        'status': status,
                        'row': f"{result.chunk_index + 1}/{total_rows}",
                        'time': f"{result.duration:.2f}s"
                    })
                    pbar.update(1)
        
        return results

   
