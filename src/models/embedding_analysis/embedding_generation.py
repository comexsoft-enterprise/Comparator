import os
from pathlib import Path
from openai import AzureOpenAI
from dotenv import load_dotenv


AZURE_OPENAI_DEPLOYMENT = "text-embedding-3-large"

# Lazy initialization of OpenAI client
_openai_client = None

def _get_openai_client():
    """Get or create the OpenAI client instance (lazy initialization)"""
    global _openai_client
    if _openai_client is None:
        # Ensure environment variables are loaded from project root
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        env_path = project_root / '.env'
        load_dotenv(dotenv_path=env_path)
        
        AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
        AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
        AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
        
        if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
            raise ValueError(f"Missing Azure OpenAI credentials. Endpoint: {AZURE_OPENAI_ENDPOINT}, API Key: {'SET' if AZURE_OPENAI_API_KEY else 'NOT SET'}")
        
        _openai_client = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION
        )
    return _openai_client

def generate_single_embedding(idx_text_tuple):
    """Generate embedding for a single text"""
    idx, text = idx_text_tuple
    try:
        openai_client = _get_openai_client()
        response = openai_client.embeddings.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            input=text
        )
        return idx, response.data[0].embedding, None
    except Exception as e:
        return idx, None, str(e)
    

