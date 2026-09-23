"""OpenAI similarity with a portable content-addressed vector cache."""
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from matcher import config  # Loads the worktree's .env without creating a client.
from matcher.model import Contractor, MatchRequest


class SemanticUnavailable(RuntimeError):
    """A required vector could not be obtained; use the lexical scorer."""


def request_text(request: MatchRequest) -> str:
    return " ".join(filter(None, (request.event_format, request.category, request.city, request.language)))


def content_key(model: str, text: str) -> str:
    return sha256((model + "\n" + text).encode()).hexdigest()


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.!?\n]", text) if part.strip()]


def _valid_vector(vector) -> bool:
    return isinstance(vector, list) and bool(vector) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in vector)


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise SemanticUnavailable("Embedding dimensions do not match")
    length = math.hypot(*left) * math.hypot(*right)
    if not length:
        return 0.0
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(left, right)) / length))


class EmbeddingScorer:
    name = "embeddings"

    def __init__(self, cache_path: str | Path | None = None, *, client=None,
                 model: str | None = None, api_key: str | None = None):
        self.model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        self.cache_path = Path(cache_path) if cache_path is not None else Path(__file__).resolve().parents[1] / "data/embeddings.json"
        self.client, self.api_key = client, api_key
        self._vectors = {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data["model"] == self.model and isinstance(data["vectors"], dict):
                self._vectors = {key: [round(value, 6) for value in vector]
                                 for key, vector in data["vectors"].items() if _valid_vector(vector)}
        except (OSError, ValueError, KeyError, TypeError):
            pass  # A missing or damaged cache is a cache miss, not a ranking error.

    def score(self, request: MatchRequest, contractors: list[Contractor]) -> dict[str, float]:
        if not contractors:
            return {}
        self.cache_texts([request_text(request), *(c.description for c in contractors)])
        query = self._vectors[content_key(self.model, request_text(request))]
        return {c.id: round((_cosine(query, self._vectors[content_key(self.model, c.description)]) + 1) / 2, 3)
                for c in contractors}

    def snippet(self, request: MatchRequest, contractor: Contractor) -> str | None:
        sentences = split_sentences(contractor.description)
        if not sentences:
            return None
        self.cache_texts([request_text(request), *sentences])
        query = self._vectors[content_key(self.model, request_text(request))]
        return max(sentences, key=lambda text: _cosine(query, self._vectors[content_key(self.model, text)]))[:200]

    def cache_texts(self, texts: list[str]) -> int:
        """Embed missing texts only; return the number of new content hashes."""
        missing = {content_key(self.model, text): text for text in texts
                   if content_key(self.model, text) not in self._vectors}
        if not missing:
            return 0
        if self.client is None:
            self.client = config.create_client(self.api_key)
        if self.client is None:
            raise SemanticUnavailable(
                f"OPENAI_API_KEY is missing and {len(missing)} required vector(s) are not cached "
                f"in {self.cache_path}: {', '.join(missing)}")
        try:
            response = self.client.embeddings.create(model=self.model, input=list(missing.values()))
            entries = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in entries] != list(range(len(missing))) or not all(_valid_vector(item.embedding) for item in entries):
                raise ValueError("Malformed embedding response")
            # Score the same precision used on disk so first-use and offline results agree.
            self._vectors.update({key: [round(value, 6) for value in item.embedding]
                                  for key, item in zip(missing, entries, strict=True)})
        except Exception as error:
            raise SemanticUnavailable("Embedding API did not return the required vectors") from error
        self._persist()
        return len(missing)

    def _persist(self) -> None:
        temporary = None
        try:
            if self.cache_path.exists() and not self.cache_path.stat().st_mode & 0o222:
                return
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_path.parent,
                                    prefix=".embeddings-", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"model": self.model, "vectors": self._vectors}, handle,
                          sort_keys=True, separators=(",", ":"))
            temporary.replace(self.cache_path)
        except OSError:
            pass  # Read-only deployments can still reuse vectors in memory.
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
