import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4")
TDP_TTL_DAYS = int(os.getenv("TDP_TTL_DAYS", "90"))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "oppintelai.db")

# External integrations
CLEARSIGNALS_URL = os.getenv("CLEARSIGNALS_URL", "")          # e.g. https://clearsignals.up.railway.app
OPPINTELAI_PUBLIC_URL = os.getenv("OPPINTELAI_PUBLIC_URL", "") # e.g. https://oppintelai.up.railway.app
