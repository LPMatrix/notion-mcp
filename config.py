"""Load config from environment."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-1.5-flash").strip()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip() or None
