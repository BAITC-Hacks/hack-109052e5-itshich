import csv
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from matcher.model import Contractor, MatchRequest


ROOT = Path(__file__).resolve().parents[1]
CSV_FIELDS = (
    "id", "anon_name", "categories", "city", "city_imputed", "synthetic",
    "price_from_kzt", "price_imputed", "event_formats", "languages",
    "max_hours", "busy_dates", "description",
)


@pytest.fixture(scope="session")
def real_contractors():
    from matcher.data import load_contractors

    return load_contractors(ROOT / "data" / "contractors.csv")


@pytest.fixture(scope="session")
def demo_queries():
    return json.loads((ROOT / "demo" / "queries.json").read_text(encoding="utf-8"))


@pytest.fixture
def offline_demo_scorer(monkeypatch, real_contractors, demo_queries):
    # Import first: config loads .env once, then the test explicitly removes its key.
    from matcher.embeddings import EmbeddingScorer, SemanticUnavailable, request_text, split_sentences
    from matcher.pipeline import choose_scorer

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    scorer = choose_scorer()
    assert isinstance(scorer, EmbeddingScorer), "Demo verification must use the production EmbeddingScorer"
    texts = [text for contractor in real_contractors
             for text in [contractor.description, *split_sentences(contractor.description)]]
    texts.extend(request_text(MatchRequest(**(entry["request"] | {
        "event_date": date.fromisoformat(entry["request"]["event_date"])}))) for entry in demo_queries)
    try:
        scorer.cache_texts(texts)
    except SemanticUnavailable as error:
        pytest.fail(f"Offline demo embedding cache is incomplete; lexical fallback is forbidden: {error}")
    return scorer


@pytest.fixture
def match_request():
    return MatchRequest("Алматы", date(2026, 11, 14), "корпоратив", "Ведущий", 800_000)


@pytest.fixture
def contractor():
    return Contractor(
        id="c-01", name="Алия", categories=("Ведущий", "Ведущий церемонии"),
        city="Алматы", city_imputed=False, synthetic=False,
        price_from_kzt=200_000, price_imputed=False,
        event_formats=("корпоратив", "свадьба"),
        languages=("русский", "казахский"), max_hours=8,
        busy_dates=frozenset(), description="Ведущая корпоративов и свадеб.",
    )


@pytest.fixture
def sample_contractors(contractor):
    return [
        contractor,
        replace(contractor, id="c-02", name="Бек", price_from_kzt=400_000),
        replace(contractor, id="c-03", name="Дана", price_from_kzt=600_000),
    ]


@pytest.fixture
def csv_row():
    return dict(zip(CSV_FIELDS, (
        "csv-01", "Имя", "Ведущий|Ведущий церемонии", "Алматы", "False", "False",
        "200000", "True", "корпоратив|свадьба", "русский|казахский", "",
        "2026-09-23|2026-12-31", "Корпоративы, свадьбы.\nДве строки описания.",
    )))


@pytest.fixture
def write_csv(tmp_path):
    def write(rows, name="contractors.csv"):
        path = tmp_path / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    return write
