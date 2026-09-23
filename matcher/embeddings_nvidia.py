"""NVIDIA NIM embeddings with typed content keys and fixed catalogue anchors."""
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Literal

from matcher import config
from matcher.embeddings import SemanticUnavailable, request_text, split_sentences
from matcher.model import CALENDAR_START, Contractor, MatchRequest

EMBEDDING_MODEL = "nvidia/nemotron-3-embed-1b"
EMBEDDING_DIMENSIONS = 2048
InputType = Literal["query", "passage"]


def content_key(model: str, input_type: InputType, text: str) -> str:
    return sha256(f"{model}\n{input_type}\n{text}".encode()).hexdigest()


def _valid_vector(vector, dimensions: int) -> bool:
    return isinstance(vector, list) and len(vector) == dimensions and bool(vector) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in vector)


def _cosine(left: list[float], right: list[float]) -> float:
    length = math.hypot(*left) * math.hypot(*right)
    if not length:
        return 0.0
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(left, right, strict=True)) / length))


def calibration_anchors(cosines: list[float]) -> dict[str, float]:
    """Linear-interpolated P05/P95 over the full query-description product."""
    values = sorted(cosines)
    if not values:
        raise ValueError("Для калибровки нужны запросы и описания")

    def percentile(fraction):
        position = (len(values) - 1) * fraction
        lower, upper = math.floor(position), math.ceil(position)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    low = percentile(0.05)
    return {"low": low, "high": max(percentile(0.95), low + 0.10)}


class NvidiaEmbeddingScorer:
    name = "nvidia"

    def __init__(self, cache_path: str | Path | None = None, *, client=None,
                 model: str = EMBEDDING_MODEL, dimensions: int = EMBEDDING_DIMENSIONS,
                 api_key: str | None = None):
        self.model, self.dimensions = model, dimensions
        self.cache_path = Path(cache_path) if cache_path is not None else Path(__file__).resolve().parents[1] / "data/embeddings_nvidia.json"
        self.client, self.api_key = client, api_key
        self._vectors = {}
        self.anchors = None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data["model"] == model and data["dimensions"] == dimensions:
                self._vectors = {key: [round(value, 6) for value in vector]
                                 for key, vector in data["vectors"].items() if _valid_vector(vector, dimensions)}
                anchors = data.get("anchors")
                if (isinstance(anchors, dict) and _valid_vector([anchors.get("low"), anchors.get("high")], 2)
                        and -1 <= anchors["low"] <= 1 and anchors["high"] >= anchors["low"] + 0.10):
                    self.anchors = {"low": anchors["low"], "high": anchors["high"]}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass  # Missing or damaged caches are cache misses.

    def score(self, request: MatchRequest, contractors: list[Contractor]) -> dict[str, float]:
        if not contractors:
            return {}
        if self.anchors is None:
            raise SemanticUnavailable("В кэше NVIDIA нет якорей; запустите сборку с --provider nvidia --anchors")
        self.cache_texts([request_text(request)], "query")
        self.cache_texts([contractor.description for contractor in contractors])
        query = self._vector(request_text(request), "query")
        low, high = self.anchors["low"], self.anchors["high"]
        return {contractor.id: round(max(0.0, min(1.0, (
            _cosine(query, self._vector(contractor.description)) - low) / (high - low))), 3)
                for contractor in contractors}

    def snippet(self, request: MatchRequest, contractor: Contractor) -> str | None:
        sentences = split_sentences(contractor.description)
        if not sentences:
            return None
        self.cache_texts([request_text(request)], "query")
        self.cache_texts(sentences)
        query = self._vector(request_text(request), "query")
        return max(sentences, key=lambda sentence: _cosine(query, self._vector(sentence)))[:200]

    def calibrate_anchors(self, queries: list[str], descriptions: list[str]) -> dict[str, float]:
        """Explicit offline preparation only: never recalibrate an eligible pool."""
        queries, descriptions = list(dict.fromkeys(queries)), list(dict.fromkeys(descriptions))
        self.cache_texts(queries, "query")
        self.cache_texts(descriptions)
        self.anchors = calibration_anchors([_cosine(self._vector(query, "query"), self._vector(description))
                                            for query in queries for description in descriptions])
        self._persist()
        return dict(self.anchors)

    def _vector(self, text: str, input_type: InputType = "passage") -> list[float]:
        return self._vectors[content_key(self.model, input_type, text)]

    def cache_texts(self, texts: list[str], input_type: InputType = "passage") -> int:
        """Embed each missing typed content hash once; return the number added."""
        if input_type not in ("query", "passage"):
            raise ValueError("input_type должен быть query или passage")
        missing = {content_key(self.model, input_type, text): text for text in texts
                   if content_key(self.model, input_type, text) not in self._vectors}
        if not missing:
            return 0
        if self.client is None:
            self.client = config.create_nvidia_client(self.api_key)
        if self.client is None:
            raise SemanticUnavailable(f"Не задан NVIDIA_API_KEY; в кэше отсутствуют векторы: {len(missing)}")
        try:
            items = list(missing.items())
            for offset in range(0, len(items), 64):
                batch = dict(items[offset:offset + 64])
                response = self.client.embeddings.create(model=self.model, input=list(batch.values()),
                                                         encoding_format="float",
                                                         extra_body={"input_type": input_type, "truncate": "NONE"})
                entries = sorted(response.data, key=lambda item: item.index)
                if [item.index for item in entries] != list(range(len(batch))) or not all(
                        _valid_vector(item.embedding, self.dimensions) for item in entries):
                    raise ValueError("Некорректный ответ API эмбеддингов NVIDIA")
                self._vectors.update({key: [round(value, 6) for value in item.embedding]
                                      for key, item in zip(batch, entries, strict=True)})
        except Exception as error:
            raise SemanticUnavailable("API NVIDIA не вернул необходимые векторы") from error
        self._persist()
        return len(missing)

    def _persist(self) -> None:
        temporary = None
        try:
            if self.cache_path.exists() and not self.cache_path.stat().st_mode & 0o222:
                return
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_path.parent,
                                    prefix=".embeddings-nvidia-", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"model": self.model, "dimensions": self.dimensions,
                           "anchors": self.anchors, "vectors": self._vectors}, handle,
                          sort_keys=True, separators=(",", ":"), allow_nan=False)
            temporary.replace(self.cache_path)
        except OSError:
            pass  # Read-only deployments can reuse vectors in memory.
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def build_cache(source: Path, output: Path, *, anchors: bool = False) -> int:
    """Build all catalogue queries, descriptions and sentences for the CLI."""
    if not os.getenv("NVIDIA_API_KEY"):
        print("Не задан NVIDIA_API_KEY: добавьте ключ для сборки кэша NVIDIA NIM.", file=sys.stderr)
        return 2
    from matcher.data import load_contractors

    contractors = load_contractors(source)
    descriptions = sorted({contractor.description for contractor in contractors})
    passages = sorted({text for description in descriptions for text in [description, *split_sentences(description)]})
    queries = sorted({request_text(MatchRequest(contractor.city, CALENDAR_START, event_format, category, 1, language=language))
                      for contractor in contractors for category in contractor.categories
                      for event_format in contractor.event_formats for language in (None, *contractor.languages)})
    scorer = NvidiaEmbeddingScorer(cache_path=output)
    embedded = scorer.cache_texts(queries, "query") + scorer.cache_texts(passages)
    if anchors:
        scorer.calibrate_anchors(queries, descriptions)
    data = json.loads(output.read_text(encoding="utf-8"))
    if data != {"model": scorer.model, "dimensions": scorer.dimensions,
                "anchors": scorer.anchors, "vectors": scorer._vectors}:
        raise OSError(f"Не удалось сохранить полный кэш NVIDIA: {output}")
    print(f"NVIDIA: запросов={len(queries)}, описаний={len(descriptions)}, "
          f"векторов={len(data['vectors'])}, новых={embedded}, якоря={scorer.anchors}; кэш: {output}")
    return 0
