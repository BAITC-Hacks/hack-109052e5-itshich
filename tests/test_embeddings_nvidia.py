"""NVIDIA's public scoring and cache-building boundaries, without network."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from matcher import config


@pytest.fixture(autouse=True)
def offline_clients(monkeypatch):
    import openai

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: pytest.fail("Real SDK clients are forbidden"))


class FakeClient:
    def __init__(self, vectors=None, dimensions=2, error=None):
        self.embeddings, self.calls = self, []
        self.vectors, self.dimensions, self.error = vectors, dimensions, error

    def create(self, *, model, input, encoding_format, extra_body=None):
        self.calls.append(dict(model=model, input=input, encoding_format=encoding_format, extra_body=extra_body))
        if self.error:
            raise self.error
        kind = extra_body["input_type"] if extra_body else "passage"
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=(
            self.vectors[kind, text] if self.vectors is not None
            else [1.0] + [0.0] * (self.dimensions - 1))) for i, text in reversed(list(enumerate(input)))])


def test_nvidia_client_uses_its_own_key_and_endpoint(monkeypatch):
    import openai

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.invalid/v1")
    calls = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: calls.append(kwargs) or object())
    assert config.create_nvidia_client() is None
    assert calls == []
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-test-key")
    assert config.create_nvidia_client() is not None
    assert calls == [{"base_url": "https://integrate.api.nvidia.com/v1",
                      "api_key": "nvidia-test-key", "timeout": 8, "max_retries": 0}]
    assert config.create_nvidia_client("") is None


def test_typed_requests_are_cached_separately_and_reused_offline(tmp_path):
    from matcher.embeddings_nvidia import NvidiaEmbeddingScorer, content_key

    path = tmp_path / "cache.json"
    client = FakeClient({("query", "Совпадение"): [0.123456789, 1],
                         ("passage", "Совпадение"): [1, -0.987654321], ("passage", "Текст"): [0, 1]})
    scorer = NvidiaEmbeddingScorer(cache_path=path, client=client, dimensions=2)
    assert scorer.name == "nvidia"
    assert scorer.cache_texts(["Совпадение", "Совпадение"], input_type="query") == 1
    assert scorer.cache_texts(["Совпадение", "Текст"]) == 2
    assert client.calls == [dict(model="nvidia/nemotron-3-embed-1b", input=texts, encoding_format="float",
                                 extra_body={"input_type": kind, "truncate": "NONE"})
                            for kind, texts in [("query", ["Совпадение"]), ("passage", ["Совпадение", "Текст"])]]
    assert len({content_key(model, kind, "Совпадение") for model in (scorer.model, "another")
                for kind in ("query", "passage")}) == 4
    data = json.loads(path.read_text())
    assert data == {"model": scorer.model, "dimensions": 2, "anchors": None, "vectors": {
        content_key(scorer.model, "query", "Совпадение"): [0.123457, 1],
        content_key(scorer.model, "passage", "Совпадение"): [1, -0.987654],
        content_key(scorer.model, "passage", "Текст"): [0, 1]}}
    assert path.read_text() == json.dumps(data, sort_keys=True, separators=(",", ":"))
    offline = NvidiaEmbeddingScorer(cache_path=path, dimensions=2, api_key="")
    assert offline.cache_texts(["Совпадение"], input_type="query") == 0
    assert offline.cache_texts(["Совпадение", "Текст"]) == 0
    assert not list(tmp_path.glob(".embeddings-nvidia-*"))


@pytest.mark.parametrize("narrow", [False, True])
def test_catalogue_anchors_calibrate_all_pairs_and_stay_fixed_offline(tmp_path, match_request, contractor, narrow):
    from matcher.embeddings import request_text
    from matcher.embeddings_nvidia import NvidiaEmbeddingScorer

    query = request_text(match_request)
    client = FakeClient({("query", query): [1, 0], ("query", "Другой запрос"): [1 if narrow else -1, 0],
                         ("passage", contractor.description): [1, 0], ("passage", "Другая"): [1 if narrow else 0, 1 if not narrow else 0],
                         ("passage", "Средняя"): [3, 4], ("passage", "Противоположная"): [-1, 0]})
    path = tmp_path / "cache.json"
    scorer = NvidiaEmbeddingScorer(cache_path=path, client=client, dimensions=2)
    anchors = scorer.calibrate_anchors([query, "Другой запрос"], [contractor.description, "Другая"])
    assert anchors == pytest.approx({"low": 1.0, "high": 1.1} if narrow else {"low": -0.85, "high": 0.85})
    candidates = [contractor, replace(contractor, id="mid", description="Средняя"),
                  replace(contractor, id="low", description="Противоположная")]
    expected = {contractor.id: 0.0 if narrow else 1.0, "mid": 0.0 if narrow else 0.853, "low": 0.0}
    assert scorer.score(match_request, candidates) == expected
    assert scorer.score(match_request, candidates[1:]) == {key: expected[key] for key in ("mid", "low")}
    assert json.loads(path.read_text())["anchors"] == pytest.approx(anchors)
    offline = NvidiaEmbeddingScorer(cache_path=path, dimensions=2, api_key="")
    assert offline.score(match_request, candidates) == expected


@pytest.mark.parametrize("selected", ["Деловые встречи", "Деловые встречи " * 20])
def test_snippets_embed_sentences_as_passages_and_resolve_ties_offline(tmp_path, match_request, contractor, selected):
    from matcher.embeddings import request_text
    from matcher.embeddings_nvidia import NvidiaEmbeddingScorer

    selected = selected.strip()
    contractor = replace(contractor, description=f"Игры. {selected}! Музыка?\nТанцы")
    client = FakeClient({("query", request_text(match_request)): [1, 0], ("passage", "Игры"): [-1, 0],
                         ("passage", selected): [1, 0], ("passage", "Музыка"): [1, 0], ("passage", "Танцы"): [0, 1]})
    path = tmp_path / "cache.json"
    scorer = NvidiaEmbeddingScorer(cache_path=path, dimensions=2, client=client)
    assert scorer.score(match_request, []) == {}
    assert scorer.snippet(match_request, replace(contractor, description=". !?\n")) is None
    assert client.calls == []
    assert scorer.snippet(match_request, contractor) == selected[:200]
    assert [call["extra_body"]["input_type"] for call in client.calls] == ["query", "passage"]
    offline = NvidiaEmbeddingScorer(cache_path=path, dimensions=2, api_key="")
    assert offline.snippet(match_request, contractor) == selected[:200]


@pytest.mark.parametrize("provider,expected", [(None, "embeddings"), ("openai", "embeddings"), ("nvidia", "nvidia")])
def test_provider_selection_defaults_to_openai(monkeypatch, provider, expected):
    from matcher.pipeline import choose_scorer

    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    if provider is not None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", provider)
    assert choose_scorer().name == expected


@pytest.mark.parametrize("missing", ["query", "passage", "snippet", "anchors", "api"])
def test_pipeline_reports_lexical_when_nvidia_cannot_serve(tmp_path, monkeypatch, match_request, contractor, missing):
    import openai
    from matcher import embeddings_nvidia as nvidia, pipeline
    from matcher.embeddings import request_text, split_sentences
    from matcher.explain import TemplateExplainer

    monkeypatch.setattr(nvidia, "__file__", str(tmp_path / "matcher/embeddings_nvidia.py"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "nvidia")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    scorer = nvidia.NvidiaEmbeddingScorer(client=FakeClient(dimensions=2048))
    scorer.calibrate_anchors([request_text(match_request)], [contractor.description])
    scorer.cache_texts(split_sentences(contractor.description))
    assert pipeline.choose_scorer().name == "nvidia"
    assert pipeline.run(match_request, [contractor], pipeline.choose_scorer()).semantic_backend == "nvidia"
    data = json.loads(scorer.cache_path.read_text())
    if missing == "anchors":
        data["anchors"] = None
    else:
        kind, text = {"query": ("query", request_text(match_request)), "api": ("query", request_text(match_request)),
                      "passage": ("passage", contractor.description),
                      "snippet": ("passage", split_sentences(contractor.description)[0])}[missing]
        del data["vectors"][nvidia.content_key(scorer.model, kind, text)]
    scorer.cache_path.write_text(json.dumps(data))
    if missing == "api":
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: FakeClient(error=TimeoutError()))
    monkeypatch.setattr(pipeline, "get_contractors", lambda: [contractor])
    monkeypatch.setattr(pipeline, "explain", TemplateExplainer().explain)
    response = pipeline.answer(match_request)
    assert response["semantic_backend"] == "lexical"
    assert response["cards"][0]["id"] == contractor.id


def test_builder_reports_missing_nvidia_key_before_reading_csv(tmp_path, capsys):
    from scripts.build_embeddings import main

    assert main(["--provider", "nvidia", "--anchors", "--csv", str(tmp_path / "missing.csv")]) == 2
    assert "Не задан NVIDIA_API_KEY" in capsys.readouterr().err


def test_builder_caches_all_supported_queries_descriptions_and_sentences(tmp_path, monkeypatch, csv_row, write_csv):
    import openai
    from scripts import build_embeddings as script

    source = write_csv([csv_row | {"categories": "Ведущий", "event_formats": "корпоратив", "languages": "русский",
                                  "description": "Игры. Форум!"}])
    write_csv([csv_row | {"id": "extra", "synthetic": "True", "categories": "Флорист", "event_formats": "свадьба",
                         "languages": "казахский", "description": "Цветы. Букеты!"}], name="synthetic_extra.csv")
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    client = FakeClient(dimensions=2048)
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
    command = ["--provider", "nvidia", "--anchors", "--csv", str(source)]
    assert script.main(command) == 0
    path = tmp_path / "data/embeddings_nvidia.json"
    original = path.read_bytes()
    data = json.loads(original)
    assert data["anchors"] == {"low": 1, "high": 1.1} and len(data["vectors"]) == 10
    assert {text for call in client.calls if call["extra_body"]["input_type"] == "query" for text in call["input"]} == {
        "корпоратив Ведущий Алматы", "корпоратив Ведущий Алматы русский", "свадьба Флорист Алматы", "свадьба Флорист Алматы казахский"}
    client.error = AssertionError("Completed cache must not call the API again")
    assert script.main(command) == 0
    assert path.read_bytes() == original
    assert not (tmp_path / "data/embeddings.json").exists()


def test_large_cache_builds_use_bounded_batches(tmp_path):
    from matcher.embeddings_nvidia import NvidiaEmbeddingScorer

    client = FakeClient()
    scorer = NvidiaEmbeddingScorer(cache_path=tmp_path / "cache.json", dimensions=2, client=client)
    assert scorer.cache_texts([f"Текст {index}" for index in range(130)]) == 130
    assert [len(call["input"]) for call in client.calls] == [64, 64, 2]


@pytest.mark.parametrize("vector,index", [([], 0), ([float("nan"), 0], 0), ([float("inf"), 0], 0),
                                         ([1, 0, 0], 0), ([True, 0], 0), (["bad", 0], 0), ([1, 0], 1)])
def test_malformed_responses_never_enter_the_cache(tmp_path, vector, index):
    from matcher.embeddings import SemanticUnavailable
    from matcher.embeddings_nvidia import NvidiaEmbeddingScorer

    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
        data=[SimpleNamespace(index=index, embedding=vector)])))
    path = tmp_path / "cache.json"
    scorer = NvidiaEmbeddingScorer(cache_path=path, dimensions=2, client=client)
    with pytest.raises(SemanticUnavailable):
        scorer.cache_texts(["Текст"])
    assert not path.exists()


def test_failed_atomic_replace_preserves_disk_and_reuses_memory(tmp_path, monkeypatch):
    from pathlib import Path
    from matcher.embeddings_nvidia import NvidiaEmbeddingScorer

    path, client = tmp_path / "cache.json", FakeClient()
    scorer = NvidiaEmbeddingScorer(cache_path=path, dimensions=2, client=client)
    scorer.cache_texts(["Первый"])
    original = path.read_bytes()

    def denied(*args):
        raise OSError("Read-only cache")

    monkeypatch.setattr(Path, "replace", denied)
    assert scorer.cache_texts(["Второй"]) == 1
    client.error = AssertionError("Vector is already in memory")
    assert scorer.cache_texts(["Второй"]) == 0
    assert path.read_bytes() == original and not list(tmp_path.glob(".embeddings-nvidia-*"))


def test_self_hosted_tei_mode_needs_no_key_and_sends_no_nim_fields(monkeypatch, tmp_path):
    """A TEI/NIM server on a Brev GPU: NVIDIA_BASE_URL set, no NVIDIA_API_KEY, non-nvidia model."""
    from matcher import embeddings_nvidia as nvidia
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("NVIDIA_EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.setenv("NVIDIA_EMBEDDING_DIMENSIONS", "4")
    import openai
    seen = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: seen.update(kwargs) or object())
    assert config.create_nvidia_client() is not None
    assert seen["base_url"] == "http://localhost:8000/v1" and seen["api_key"] == "none"
    fake = FakeClient(dimensions=4)
    scorer = nvidia.NvidiaEmbeddingScorer(cache_path=tmp_path / "nv.json", client=fake)
    assert (scorer.model, scorer.dimensions) == ("BAAI/bge-m3", 4)
    assert scorer.cache_texts(["корпоратив Ведущий Алматы"], "query") == 1
    assert fake.calls[0]["model"] == "BAAI/bge-m3" and fake.calls[0]["extra_body"] is None
    assert nvidia.content_key("BAAI/bge-m3", "query", "корпоратив Ведущий Алматы") in scorer._vectors


def test_hosted_nim_without_key_is_still_refused(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_BASE_URL", raising=False)
    assert config.create_nvidia_client() is None
