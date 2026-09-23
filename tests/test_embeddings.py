from dataclasses import replace
from datetime import date
from hashlib import sha256
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from matcher.model import Contractor, MatchRequest


MODEL = "text-embedding-3-small"
REQUEST = MatchRequest("Алматы", date(2026, 11, 14), "корпоратив", "Ведущий", 800_000, 5, "английский")
QUERY = "корпоратив Ведущий Алматы английский"
CONTRACTOR = Contractor(
    id="c1", name="Хикару", categories=("Ведущий",), city="Алматы",
    city_imputed=False, synthetic=False, price_from_kzt=600_000,
    price_imputed=False, event_formats=("корпоратив",), languages=("английский",),
    max_hours=8, busy_dates=frozenset(), description="Музыкальные игры. Деловые встречи!",
)


def cache_key(text, model=MODEL):
    return sha256((model + "\n" + text).encode()).hexdigest()


def write_cache(path, vectors, model=MODEL):
    path.write_text(json.dumps({"model": model, "vectors": {cache_key(t, model): v for t, v in vectors.items()}}))


class FakeEmbeddingsClient:
    def __init__(self, vectors=None, error=None):
        self.vectors, self.error, self.calls = vectors or {}, error, []
        self.embeddings = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        texts = kwargs["input"]
        texts = [texts] if isinstance(texts, str) else texts
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=self.vectors[t]) for i, t in enumerate(texts)])


@pytest.mark.parametrize(("vector", "expected"), [
    ([1.0, 0.0], 1.0), ([-1.0, 0.0], 0.0), ([0.0, 1.0], 0.5),
    ([1.0, 1.0], 0.854), ([0.0, 0.0], 0.5), ([3.0, 4.0], 0.8),
])
def test_cached_scores_map_cosine_and_round_without_client_calls(tmp_path, vector, expected):
    from matcher.embeddings import EmbeddingScorer

    path = tmp_path / "embeddings.json"
    write_cache(path, {QUERY: [1.0, 0.0], CONTRACTOR.description: vector})
    client = FakeEmbeddingsClient(error=AssertionError("cache hit must be offline"))
    scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL)

    assert scorer.name == "embeddings"
    assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": expected}
    assert client.calls == []


def test_missing_embeddings_are_written_through_and_reused_offline(tmp_path):
    from matcher.embeddings import EmbeddingScorer

    path = tmp_path / "embeddings.json"
    write_cache(path, {CONTRACTOR.description: [1.0, 1.0]})
    client = FakeEmbeddingsClient({QUERY: [1.0, 0.0]})
    scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL)

    assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": 0.854}
    assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": 0.854}
    assert [text for call in client.calls for text in call["input"]] == [QUERY]
    assert json.loads(path.read_text()) == {"model": MODEL, "vectors": {
        cache_key(QUERY): [1.0, 0.0], cache_key(CONTRACTOR.description): [1.0, 1.0]}}
    offline = EmbeddingScorer(cache_path=path, model=MODEL, api_key="")
    assert offline.score(REQUEST, [CONTRACTOR]) == {"c1": 0.854}


@pytest.mark.parametrize("long_sentence", [False, True])
def test_snippet_splits_all_boundaries_and_picks_highest_cosine_with_stable_ties(tmp_path, long_sentence):
    from matcher.embeddings import EmbeddingScorer

    selected = "Деловые встречи" + (" для большой команды" * 15 if long_sentence else "")
    contractor = replace(CONTRACTOR, description=f"Игры. {selected}! Церемонии?\nМузыка\nТанцы.")
    path = tmp_path / "embeddings.json"
    write_cache(path, {QUERY: [1, 0], "Игры": [-1, 0], selected: [1, 0],
                       "Церемонии": [1, 0], "Музыка": [0, 1], "Танцы": [0, 1]})
    client = FakeEmbeddingsClient(error=AssertionError("cache hit must be offline"))
    scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL)

    assert scorer.snippet(REQUEST, contractor) == selected[:200]
    assert client.calls == []


@pytest.mark.parametrize("storage", ["readonly", "unwritable_parent"])
def test_vectors_stay_in_memory_when_cache_cannot_be_written(tmp_path, storage):
    from matcher.embeddings import EmbeddingScorer

    path = tmp_path / "embeddings.json"
    if storage == "readonly":
        write_cache(path, {})
        original = path.read_bytes()
        path.chmod(0o444)
    else:
        blocker = tmp_path / "not_a_directory"
        blocker.write_text("occupied")
        path = blocker / "embeddings.json"
    client = FakeEmbeddingsClient({QUERY: [1, 0], CONTRACTOR.description: [1, 1]})
    try:
        scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL)
        assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": 0.854}
        client.error = AssertionError("already cached in memory")
        assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": 0.854}
        if storage == "readonly":
            assert path.read_bytes() == original
    finally:
        if storage == "readonly":
            path.chmod(0o644)


@pytest.mark.parametrize("source", ["missing", "partial", "other_model", "corrupt", "dimensions", "api_timeout"])
@pytest.mark.parametrize("operation", ["score", "snippet"])
def test_unavailable_vectors_raise_semantic_unavailable(tmp_path, source, operation):
    from matcher.embeddings import EmbeddingScorer, SemanticUnavailable

    path = tmp_path / "embeddings.json"
    vectors = {QUERY: [1, 0], CONTRACTOR.description: [1, 1], "Музыкальные игры": [1, 1], "Деловые встречи": [1, 1]}
    if source == "partial":
        write_cache(path, {QUERY: [1, 0]})
    if source == "other_model":
        write_cache(path, vectors, model="another-model")
    if source == "corrupt":
        path.write_text("{broken json")
    if source == "dimensions":
        write_cache(path, {**vectors, QUERY: [1, 0, 0]})
    client = FakeEmbeddingsClient(error=TimeoutError("offline")) if source == "api_timeout" else None
    scorer = EmbeddingScorer(cache_path=path, model=MODEL, client=client, api_key="")

    with pytest.raises(SemanticUnavailable):
        if operation == "score":
            scorer.score(REQUEST, [CONTRACTOR])
        else:
            scorer.snippet(REQUEST, CONTRACTOR)


def test_build_script_dry_run_embeds_csv_descriptions_and_sentences_once(tmp_path):
    source, target = tmp_path / "contractors.csv", tmp_path / "embeddings.json"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "description"])
        writer.writeheader()
        writer.writerows([{"id": "a", "description": "Игра. Деловой форум!"},
                         {"id": "b", "description": "Игра.\nНаграждение?"}])
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / "scripts/build_embeddings.py"), "--dry-run",
               "--csv", str(source), "--output", str(target)]
    env = {**os.environ, "OPENAI_API_KEY": "", "EMBEDDING_MODEL": MODEL}

    first = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert "contractors=2" in first.stdout and "sentences=4" in first.stdout
    assert "embedded=5" in first.stdout
    original = target.read_bytes()
    data = json.loads(original)
    expected_texts = {"Игра. Деловой форум!", "Игра.\nНаграждение?", "Игра", "Деловой форум", "Награждение"}
    assert data == {"model": MODEL, "vectors": {cache_key(t): [0.0] * 8 for t in expected_texts}}

    second = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert "embedded=0" in second.stdout
    assert target.read_bytes() == original


@pytest.mark.parametrize("vector", [[], [float("nan"), 0], [float("inf"), 1], ["invalid", 1], None])
@pytest.mark.parametrize("cached", [False, True])
def test_invalid_vectors_are_unavailable_instead_of_producing_a_false_score(tmp_path, vector, cached):
    from matcher.embeddings import EmbeddingScorer, SemanticUnavailable

    path = tmp_path / "embeddings.json"
    vectors = {QUERY: [1, 0], CONTRACTOR.description: vector}
    if cached:
        write_cache(path, vectors)
    scorer = EmbeddingScorer(cache_path=path, model=MODEL, api_key="",
                             client=None if cached else FakeEmbeddingsClient(vectors))
    with pytest.raises(SemanticUnavailable):
        scorer.score(REQUEST, [CONTRACTOR])


def test_empty_candidates_and_descriptions_do_not_need_embeddings(tmp_path):
    from matcher.embeddings import EmbeddingScorer

    client = FakeEmbeddingsClient(error=AssertionError("no vectors are needed"))
    scorer = EmbeddingScorer(cache_path=tmp_path / "cache.json", client=client)
    assert scorer.score(REQUEST, []) == {}
    assert scorer.snippet(REQUEST, replace(CONTRACTOR, description=". !\n?")) is None
    assert client.calls == []
