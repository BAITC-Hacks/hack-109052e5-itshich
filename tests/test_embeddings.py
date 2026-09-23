from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from matcher.embeddings import EmbeddingScorer, SemanticUnavailable, content_key
from matcher.model import Contractor, MatchRequest


MODEL = "text-embedding-3-large"
DIMENSIONS = 2
REQUEST = MatchRequest("Алматы", date(2026, 11, 14), "корпоратив", "Ведущий", 800_000, 5, "английский")
QUERY = "корпоратив Ведущий Алматы английский"
CONTRACTOR = Contractor(
    id="c1", name="Хикару", categories=("Ведущий",), city="Алматы",
    city_imputed=False, synthetic=False, price_from_kzt=600_000,
    price_imputed=False, event_formats=("корпоратив",), languages=("английский",),
    max_hours=8, busy_dates=frozenset(), description="Музыкальные игры. Деловые встречи!",
)


def cache_key(text, model=MODEL, dimensions=DIMENSIONS):
    return sha256(f"{model}\n{dimensions}\n{text}".encode()).hexdigest()


def write_cache(path, vectors, model=MODEL, dimensions=DIMENSIONS, anchors=(0.2, 0.8, 20)):
    data = {"model": model, "dimensions": dimensions,
            "vectors": {cache_key(t, model, dimensions): v for t, v in vectors.items()}}
    if anchors is not None:
        data["anchors"] = dict(zip(("low", "high", "pairs"), anchors))
    path.write_text(json.dumps(data))


class FakeEmbeddingsClient:
    def __init__(self, vectors=None, error=None):
        self.vectors, self.error, self.calls = vectors or {}, error, []
        self.embeddings = self

    def create(self, *, model, dimensions, input):
        self.calls.append({"model": model, "dimensions": dimensions, "input": input})
        if self.error:
            raise self.error
        texts = input
        texts = [texts] if isinstance(texts, str) else texts
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=self.vectors[t]) for i, t in enumerate(texts)])


@pytest.mark.parametrize(("vector", "expected"), [
    ([1.0, 0.0], 1.0), ([-1.0, 0.0], 0.0), ([0.0, 1.0], 0.0),
    ([1.0, 1.0], 0.845), ([0.0, 0.0], 0.0), ([3.0, 4.0], 0.667),
])
def test_cached_scores_map_cosine_and_round_without_client_calls(tmp_path, vector, expected):
    path = tmp_path / "embeddings.json"
    write_cache(path, {QUERY: [1.0, 0.0], CONTRACTOR.description: vector})
    client = FakeEmbeddingsClient(error=AssertionError("cache hit must be offline"))
    scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL, dimensions=DIMENSIONS)

    assert scorer.name == "embeddings"
    assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": expected}
    assert client.calls == []


def test_missing_embeddings_are_written_through_and_reused_offline(tmp_path):
    path = tmp_path / "embeddings.json"
    write_cache(path, {CONTRACTOR.description: [1.0, 1.0]})
    client = FakeEmbeddingsClient({QUERY: [1.0, 0.0]})
    scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL, dimensions=DIMENSIONS)

    assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": 0.845}
    assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": 0.845}
    assert [text for call in client.calls for text in call["input"]] == [QUERY]
    assert json.loads(path.read_text()) == {"model": MODEL, "dimensions": DIMENSIONS,
        "anchors": {"low": 0.2, "high": 0.8, "pairs": 20}, "vectors": {
        cache_key(QUERY): [1.0, 0.0], cache_key(CONTRACTOR.description): [1.0, 1.0]}}
    offline = EmbeddingScorer(cache_path=path, model=MODEL, dimensions=DIMENSIONS, api_key="")
    assert offline.score(REQUEST, [CONTRACTOR]) == {"c1": 0.845}


def test_persist_rounds_existing_and_new_vectors_to_six_decimals(tmp_path):
    path = tmp_path / "embeddings.json"
    vector = [0.123456789, -0.987654321]
    write_cache(path, {CONTRACTOR.description: vector})
    scorer = EmbeddingScorer(cache_path=path, model=MODEL, dimensions=DIMENSIONS,
                             client=FakeEmbeddingsClient({QUERY: vector}))
    scores = scorer.score(REQUEST, [CONTRACTOR])
    data = json.loads(path.read_text())
    assert data["vectors"] == {cache_key(text): [0.123457, -0.987654]
                               for text in (QUERY, CONTRACTOR.description)}
    assert path.read_text() == json.dumps(data, sort_keys=True, separators=(",", ":"))
    assert scores == EmbeddingScorer(cache_path=path, model=MODEL, dimensions=DIMENSIONS, api_key="").score(REQUEST, [CONTRACTOR])
    assert all(score == round(score, 3) for score in scores.values())


@pytest.mark.parametrize("long_sentence", [False, True])
def test_snippet_splits_all_boundaries_and_picks_highest_cosine_with_stable_ties(tmp_path, long_sentence):
    selected = "Деловые встречи" + (" для большой команды" * 15 if long_sentence else "")
    contractor = replace(CONTRACTOR, description=f"Игры. {selected}! Церемонии?\nМузыка\nТанцы.")
    path = tmp_path / "embeddings.json"
    write_cache(path, {QUERY: [1, 0], "Игры": [-1, 0], selected: [1, 0],
                       "Церемонии": [1, 0], "Музыка": [0, 1], "Танцы": [0, 1]})
    client = FakeEmbeddingsClient(error=AssertionError("cache hit must be offline"))
    scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL, dimensions=DIMENSIONS)

    assert scorer.snippet(REQUEST, contractor) == selected[:200]
    assert client.calls == []


@pytest.mark.parametrize("storage", ["readonly", "unwritable_parent"])
def test_vectors_stay_in_memory_when_cache_cannot_be_written(tmp_path, storage):
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
        scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL, dimensions=DIMENSIONS)
        assert scorer.cache_texts([QUERY, CONTRACTOR.description]) == 2
        client.error = AssertionError("already cached in memory")
        assert scorer.cache_texts([QUERY, CONTRACTOR.description]) == 0
        if storage == "readonly":
            assert path.read_bytes() == original
    finally:
        if storage == "readonly":
            path.chmod(0o644)


@pytest.mark.parametrize("source", ["missing", "partial", "other_model", "other_dimensions", "legacy", "corrupt", "dimensions", "api_timeout"])
@pytest.mark.parametrize("operation", ["score", "snippet"])
def test_unavailable_vectors_raise_semantic_unavailable(tmp_path, source, operation):
    path = tmp_path / "embeddings.json"
    vectors = {QUERY: [1, 0], CONTRACTOR.description: [1, 1], "Музыкальные игры": [1, 1], "Деловые встречи": [1, 1]}
    if source in {"partial", "api_timeout"}:
        write_cache(path, {QUERY: [1, 0]} if source == "partial" else {})
    if source == "other_model":
        write_cache(path, vectors, model="another-model")
    if source == "other_dimensions":
        write_cache(path, vectors, dimensions=3)
    if source == "legacy":
        write_cache(path, vectors)
        data = json.loads(path.read_text())
        del data["dimensions"]
        path.write_text(json.dumps(data))
    if source == "corrupt":
        path.write_text("{broken json")
    if source == "dimensions":
        write_cache(path, {**vectors, QUERY: [1, 0, 0]})
    client = FakeEmbeddingsClient(error=TimeoutError("offline")) if source == "api_timeout" else None
    scorer = EmbeddingScorer(cache_path=path, model=MODEL, dimensions=DIMENSIONS, client=client, api_key="")

    with pytest.raises(SemanticUnavailable):
        if operation == "score":
            scorer.score(REQUEST, [CONTRACTOR])
        else:
            scorer.snippet(REQUEST, CONTRACTOR)


@pytest.mark.parametrize("vector", [[], [float("nan"), 0], [float("inf"), 1], ["invalid", 1], None, [1, 0, 0]])
@pytest.mark.parametrize("cached", [False, True])
def test_invalid_vectors_are_unavailable_instead_of_producing_a_false_score(tmp_path, vector, cached):
    path = tmp_path / "embeddings.json"
    vectors = {QUERY: [1, 0], CONTRACTOR.description: vector}
    write_cache(path, vectors if cached else {})
    scorer = EmbeddingScorer(cache_path=path, model=MODEL, dimensions=DIMENSIONS, api_key="",
                             client=None if cached else FakeEmbeddingsClient(vectors))
    with pytest.raises(SemanticUnavailable):
        scorer.score(REQUEST, [CONTRACTOR])


def test_empty_candidates_and_descriptions_do_not_need_embeddings(tmp_path):
    client = FakeEmbeddingsClient(error=AssertionError("no vectors are needed"))
    scorer = EmbeddingScorer(cache_path=tmp_path / "cache.json", client=client)
    assert scorer.score(REQUEST, []) == {}
    assert scorer.snippet(REQUEST, replace(CONTRACTOR, description=". !\n?")) is None
    assert client.calls == []


@pytest.mark.parametrize("dimensions", [None, 3])
def test_default_model_and_configured_dimensions_reach_api_and_cache(tmp_path, monkeypatch, dimensions):
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    if dimensions is not None:
        monkeypatch.setenv("EMBEDDING_DIMENSIONS", str(dimensions))
    expected_dimensions = dimensions or 1024
    vector = [1.0] + [0.0] * (expected_dimensions - 1)
    client = FakeEmbeddingsClient({QUERY: vector, CONTRACTOR.description: vector})
    path = tmp_path / "cache.json"
    write_cache(path, {}, dimensions=expected_dimensions)
    scorer = EmbeddingScorer(cache_path=path, client=client)

    assert scorer.score(REQUEST, [CONTRACTOR]) == {"c1": 1.0}
    assert client.calls == [{"model": MODEL, "dimensions": expected_dimensions,
                             "input": [QUERY, CONTRACTOR.description]}]
    data = json.loads(path.read_text())
    assert data == {"model": MODEL, "dimensions": expected_dimensions,
        "anchors": {"low": 0.2, "high": 0.8, "pairs": 20}, "vectors": {
        cache_key(text, dimensions=expected_dimensions): vector
        for text in (QUERY, CONTRACTOR.description)}}
    assert EmbeddingScorer(cache_path=path, api_key="").score(REQUEST, [CONTRACTOR]) == {"c1": 1.0}


@pytest.mark.parametrize("old_model,old_dimensions", [("text-embedding-3-small", 2), (MODEL, 3)])
def test_incompatible_cache_is_replaced_without_mixing_vectors(tmp_path, old_model, old_dimensions):
    path = tmp_path / "cache.json"
    write_cache(path, {QUERY: [1, 0], CONTRACTOR.description: [1, 0], "obsolete": [1, 0]},
                model=old_model, dimensions=old_dimensions)
    client = FakeEmbeddingsClient({QUERY: [1, 0], CONTRACTOR.description: [0, 1]})
    scorer = EmbeddingScorer(cache_path=path, client=client, model=MODEL, dimensions=2)
    assert scorer.cache_texts([QUERY, CONTRACTOR.description]) == 2
    with pytest.raises(SemanticUnavailable, match="--anchors"):
        scorer.score(REQUEST, [CONTRACTOR])
    assert json.loads(path.read_text()) == {"model": MODEL, "dimensions": 2, "vectors": {
        cache_key(QUERY): [1, 0], cache_key(CONTRACTOR.description): [0, 1]}}


def test_content_hash_isolated_by_model_and_dimensions():
    assert content_key(MODEL, QUERY, 2) == cache_key(QUERY)
    assert len({content_key(model, QUERY, dimensions)
                for model in (MODEL, "text-embedding-3-small")
                for dimensions in (2, 3)}) == 4


@pytest.mark.parametrize("anchors", [None, (0.5, 0.5, 1), (0.8, 0.2, 1),
    (float("nan"), 1, 1), (0, float("inf"), 1), (False, 1, 1), (0, 1, 0), (0, 1, True)])
def test_missing_or_invalid_anchors_require_rebuilding_without_api_calls(tmp_path, anchors):
    path = tmp_path / "cache.json"
    write_cache(path, {QUERY: [1, 0], CONTRACTOR.description: [1, 0]}, anchors=anchors)
    client = FakeEmbeddingsClient(error=AssertionError("anchors cannot be inferred online"))
    scorer = EmbeddingScorer(cache_path=path, model=MODEL, dimensions=2, client=client)
    with pytest.raises(SemanticUnavailable, match="--anchors"):
        scorer.score(REQUEST, [CONTRACTOR])
    assert client.calls == []


def test_anchor_percentiles_use_every_query_description_pair_and_linear_interpolation(tmp_path):
    path = tmp_path / "cache.json"
    descriptions = [f"Описание {i}" for i in range(10)]
    vectors = {"запрос A": [1, 0, 0], "запрос B": [0, 1, 0],
               **dict(zip(descriptions, [[1, 0, 0], [-1, 0, 0]] + [[0, 0, 1]] * 8))}
    write_cache(path, vectors, dimensions=3, anchors=None)
    scorer = EmbeddingScorer(cache_path=path, model=MODEL, dimensions=3, api_key="")
    scorer.calibrate_anchors(["запрос A", "запрос B", "запрос A"], descriptions + descriptions)
    assert json.loads(path.read_text())["anchors"] == pytest.approx({"low": -0.05, "high": 0.05, "pairs": 20})
