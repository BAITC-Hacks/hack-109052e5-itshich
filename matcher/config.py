"""Load local settings without creating a client or making network calls."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def create_client(api_key: str | None = None):
    key = os.getenv("OPENAI_API_KEY") if api_key is None else api_key
    if not key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=key, timeout=8, max_retries=0)
