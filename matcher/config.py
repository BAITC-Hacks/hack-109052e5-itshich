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


NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


def create_nvidia_client(api_key: str | None = None):
    """Hosted NIM needs NVIDIA_API_KEY; a self-hosted OpenAI-compatible server
    (TEI / NIM on a Brev GPU, NVIDIA_BASE_URL) works without a key."""
    base_url = os.getenv("NVIDIA_BASE_URL", NVIDIA_DEFAULT_BASE_URL)
    key = os.getenv("NVIDIA_API_KEY") if api_key is None else api_key
    if not key:
        if base_url == NVIDIA_DEFAULT_BASE_URL:
            return None
        key = "none"
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=key, timeout=8, max_retries=0)
