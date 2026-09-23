"""OpenAI similarity with a portable content-addressed vector cache."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from matcher import config  # Loads the worktree's .env without creating a client.
from matcher.model import Contractor, MatchRequest

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1024


class SemanticUnavailable(RuntimeError):
    """Required vectors or catalogue anchors are unavailable; use the lexical scorer."""


@dataclass(frozen=True)
class CatalogueAnchors:
    low: float
    high: float
    pairs: int

    def __post_init__(self):
        if (not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in (self.low, self.high))
                or self.high <= self.low or type(self.pairs) is not int or self.pairs <= 0):
            raise ValueError("Некорректные якоря семантической шкалы")


def request_text(request: MatchRequest) -> str:
    return " ".join(filter(None, (request.event_format, request.category, request.city, request.language)))


def content_key(model: str, text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> str:
    return sha256(f"{model}\n{dimensions}\n{text}".encode()).hexdigest()


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.!?\n]", text) if part.strip()]


def _valid_vector(vector) -> bool:
    return isinstance(vector, list) and bool(vector) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in vector)


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise SemanticUnavailable("Embedding dimensions do not match")
    # Normalize even API vectors: six-decimal storage can change their unit norm.
    length = math.hypot(*left) * math.hypot(*right)
    if not length:
        return 0.0
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(left, right)) / length))


def _percentile(ordered: list[float], fraction: float) -> float:
    """Inclusive percentile with linear interpolation at (n - 1) * fraction."""
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


class EmbeddingScorer:
    name = "embeddings"

    def __init__(self, cache_path: str | Path | None = None, *, client=None,
                 model: str | None = None, dimensions: int | None = None,
                 api_key: str | None = None):
        self.model = model or os.getenv("EMBEDDING_MODEL", EMBEDDING_MODEL)
        self.dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", EMBEDDING_DIMENSIONS)) if dimensions is None else dimensions
        self.cache_path = Path(cache_path) if cache_path is not None else Path(__file__).resolve().parents[1] / "data/embeddings.json"
        self.client, self.api_key = client, api_key
        self._vectors = {}
        self._anchors: CatalogueAnchors | None = None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if (data["model"] == self.model and data["dimensions"] == self.dimensions
                    and isinstance(data["vectors"], dict)):
                self._vectors = {key: [round(value, 6) for value in vector]
                                 for key, vector in data["vectors"].items()
                                 if _valid_vector(vector) and len(vector) == self.dimensions}
                self._anchors = CatalogueAnchors(**data["anchors"])
        except (OSError, ValueError, KeyError, TypeError):
            pass  # A missing or damaged cache is a cache miss, not a ranking error.

    def score(self, request: MatchRequest, contractors: list[Contractor]) -> dict[str, float]:
        if not contractors:
            return {}
        if self._anchors is None:
            raise SemanticUnavailable(
                "В кэше нет корректных якорей каталога; выполните scripts/build_embeddings.py --anchors")
        self.cache_texts([request_text(request), *(c.description for c in contractors)])
        query = self._vectors[content_key(self.model, request_text(request), self.dimensions)]
        low, high = self._anchors.low, self._anchors.high
        return {c.id: round(max(0.0, min(1.0, (
                    _cosine(query, self._vectors[content_key(self.model, c.description, self.dimensions)]) - low
                ) / (high - low))), 3)
                for c in contractors}

    def snippet(self, request: MatchRequest, contractor: Contractor) -> str | None:
        sentences = split_sentences(contractor.description)
        if not sentences:
            return None
        self.cache_texts([request_text(request), *sentences])
        query = self._vectors[content_key(self.model, request_text(request), self.dimensions)]
        return max(sentences, key=lambda text: _cosine(query, self._vectors[content_key(self.model, text, self.dimensions)]))[:200]

    def cache_texts(self, texts: list[str]) -> int:
        """Embed missing texts only; return the number of new content hashes."""
        missing = {content_key(self.model, text, self.dimensions): text for text in texts
                   if content_key(self.model, text, self.dimensions) not in self._vectors}
        if not missing:
            return 0
        if self.client is None:
            self.client = config.create_client(self.api_key)
        if self.client is None:
            raise SemanticUnavailable(
                f"OPENAI_API_KEY is missing and {len(missing)} required vector(s) are not cached "
                f"in {self.cache_path}: {', '.join(missing)}")
        try:
            response = self.client.embeddings.create(model=self.model, dimensions=self.dimensions,
                                                     input=list(missing.values()))
            entries = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in entries] != list(range(len(missing))) or not all(
                    _valid_vector(item.embedding) and len(item.embedding) == self.dimensions for item in entries):
                raise ValueError("Malformed embedding response")
            # Score the same precision used on disk so first-use and offline results agree.
            self._vectors.update({key: [round(value, 6) for value in item.embedding]
                                  for key, item in zip(missing, entries, strict=True)})
        except Exception as error:
            raise SemanticUnavailable("Embedding API did not return the required vectors") from error
        self._persist()
        return len(missing)

    def calibrate_anchors(self, queries: list[str], descriptions: list[str]) -> CatalogueAnchors:
        """Explicit catalogue build step; never called from scoring or filtering."""
        queries, descriptions = sorted(set(queries)), sorted(set(descriptions))
        if not queries or not descriptions:
            raise SemanticUnavailable("Для расчёта якорей нужны запросы и описания каталога")
        self.cache_texts([*queries, *descriptions])
        vectors = {text: self._vectors[content_key(self.model, text, self.dimensions)]
                   for text in (*queries, *descriptions)}
        similarities = sorted(_cosine(vectors[query], vectors[description])
                              for query in queries for description in descriptions)
        low = _percentile(similarities, 0.05)
        self._anchors = CatalogueAnchors(low, max(_percentile(similarities, 0.95), low + 0.10), len(similarities))
        self._persist()
        return self._anchors

    def _persist(self) -> None:
        temporary = None
        try:
            if self.cache_path.exists() and not self.cache_path.stat().st_mode & 0o222:
                return
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_path.parent,
                                    prefix=".embeddings-", delete=False) as handle:
                temporary = Path(handle.name)
                data = {"model": self.model, "dimensions": self.dimensions, "vectors": self._vectors}
                if self._anchors is not None:
                    data["anchors"] = asdict(self._anchors)
                json.dump(data, handle, sort_keys=True, separators=(",", ":"))
            temporary.replace(self.cache_path)
        except OSError:
            pass  # Read-only deployments can still reuse vectors in memory.
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
