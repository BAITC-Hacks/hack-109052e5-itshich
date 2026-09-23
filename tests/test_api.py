"""API seam tests; domain collaborators are supplied by the other workers."""
import json
import re
import sys
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from matcher.model import (
    CardFacts,
    Contractor,
    Explanation,
    MatchResult,
    Outcome,
    Rejection,
    RejectReason,
    ScoreBreakdown,
)

REQUEST = {
    "city": "Алматы", "event_date": "2026-11-14", "event_format": "корпоратив",
    "category": "Ведущий", "budget_kzt": 800_000, "duration_hours": 5,
    "language": "русский",
}
DEMOS = [
    {"name": "Три ведущих", "request": REQUEST, "expected_outcome": "matched", "note": "Три карточки"},
    {"name": "Нет категории", "request": REQUEST | {"category": "Нет категории"},
     "expected_outcome": "no_category_in_city", "note": "Пустая категория"},
    {"name": "Не проходят", "request": REQUEST | {"budget_kzt": 1},
     "expected_outcome": "none_eligible", "note": "Никто не проходит"},
]
PROFILE = Contractor(
    id="z-first", name="Арман", categories=("Ведущий",), city="Алматы",
    city_imputed=True, synthetic=True, price_from_kzt=600_000, price_imputed=True,
    event_formats=("корпоратив", "свадьба"), languages=("русский", "казахский"),
    max_hours=8, busy_dates=frozenset(), description="Ведёт деловые корпоративы.",
)
PROFILES = (
    PROFILE,
    replace(PROFILE, id="a-second", name="Дана", price_from_kzt=640_000,
            synthetic=False, price_imputed=False, city_imputed=False),
    replace(PROFILE, id="m-third", name="Ерлан", price_from_kzt=700_000, max_hours=None),
    replace(PROFILE, id="rejected", name="Занятый ведущий", price_from_kzt=1_200_000,
            event_formats=("свадьба",), languages=("казахский",), max_hours=3,
            busy_dates=frozenset({date(2026, 11, 14)})),
    replace(PROFILE, id="astana", name="Флорист", city="Астана", categories=("Флорист",)),
)


def fake_run(request, contractors, scorer):
    """Fixed examples with deliberately non-alphabetic rank order."""
    if request.category == "Нет категории":
        return MatchResult(request, Outcome.NO_CATEGORY_IN_CITY, (), (), 0, 0,
                           "В этом городе нет профилей этой категории.", scorer.name)
    rejected = (Rejection(PROFILES[3], tuple(RejectReason)),)
    if request.budget_kzt == 1:
        return MatchResult(request, Outcome.NONE_ELIGIBLE, (), rejected, 1, 0,
                           "Кандидат есть, но условия не выполнены.", scorer.name)
    scores = (ScoreBreakdown(.25, .75, 1., .375, 0., .4875),
              ScoreBreakdown(.2, .25, 1., .375, 1., .395),
              ScoreBreakdown(.125, .125, 1., .5, 0., .2375))
    cards = tuple(CardFacts(
        contractor=c, rank=rank, score=score,
        budget_kzt=800_000, budget_headroom_pct=headroom, format_matched="корпоратив",
        requested_language="русский", languages_matched=("русский",), requested_hours=5,
        duration_note="not_applicable" if c.max_hours is None else "fits",
        semantic_snippet=c.description, semantic_score=score.semantic, free_on_date=request.event_date,
        caveats=tuple(flag for flag in ("price_imputed", "city_imputed", "synthetic")
                      if getattr(c, flag)),
    ) for rank, (c, headroom, score) in enumerate(zip(PROFILES[:3], (25, 20, 12), scores), 1))
    return MatchResult(request, Outcome.MATCHED, cards, rejected, 4, 3, None, scorer.name)


def fake_explain(result):
    return tuple(Explanation(card.contractor.id,
                             f"{card.contractor.name}: цена от {card.contractor.price_from_kzt} ₸, "
                             "свободен 14.11.2026.", "template") for card in result.cards)


@pytest.fixture
def fake_pipeline(monkeypatch):
    from matcher import pipeline
    monkeypatch.setattr(pipeline, "get_contractors", lambda: list(PROFILES))
    monkeypatch.setattr(pipeline, "choose_scorer", lambda: SimpleNamespace(name="lexical"))
    monkeypatch.setattr(pipeline, "run", fake_run)
    monkeypatch.setattr(pipeline, "explain", fake_explain)
    return pipeline


@pytest.fixture
def client(fake_pipeline):
    from app import app
    with TestClient(app) as client:
        yield client


def test_match_returns_ranked_cards_and_grounded_facts(client):
    response = client.post("/api/match", json=REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "matched"
    assert body["outcome_title_ru"] == "Подобрали"
    assert [card["id"] for card in body["cards"]] == ["z-first", "a-second", "m-third"]
    assert body["pool_size"] == 4 and body["eligible_count"] == 3
    assert body["semantic_backend"] == "lexical" and body["timing_ms"] >= 0
    assert body["cards"][0] == {
        "id": "z-first", "name": "Арман", "category": "Ведущий", "city": "Алматы",
        "price_from_kzt": 600_000, "price_imputed": True, "city_imputed": True,
        "synthetic": True, "explanation": "Арман: цена от 600000 ₸, свободен 14.11.2026.",
        "explanation_source": "template", "facts": {
            "budget_headroom_pct": 25, "format_matched": "корпоратив",
            "languages_matched": ["русский"], "requested_language": "русский",
            "requested_hours": 5, "max_hours": 8, "duration_note": "fits",
            "semantic_snippet": "Ведёт деловые корпоративы.", "semantic_score": .75,
            "free_on_date": "2026-11-14", "caveats": ["price_imputed", "city_imputed", "synthetic"],
            "score": {"budget_fit": .25, "semantic": .75, "language_fit": 1.,
                      "duration_fit": .375, "data_quality": 0., "total": .4875},
        },
    }


@pytest.mark.parametrize(("changes", "outcome", "title", "note"), [
    ({"category": "Нет категории"}, "no_category_in_city", "В этом городе такой категории нет",
     "В этом городе нет профилей этой категории."),
    ({"budget_kzt": 1}, "none_eligible", "Кандидаты есть, но ни один не проходит по условиям",
     "Кандидат есть, но условия не выполнены."),
])
def test_empty_outcomes_keep_their_distinct_title_and_reason(client, changes, outcome, title, note):
    response = client.post("/api/match", json=REQUEST | changes)
    assert response.status_code == 200
    body = response.json()
    assert (body["outcome"], body["outcome_title_ru"], body["shortfall_note"]) == (outcome, title, note)
    assert body["cards"] == [] and body["eligible_count"] == 0


def test_rejection_reasons_include_stable_codes_and_russian_labels(client):
    body = client.post("/api/match", json=REQUEST).json()
    assert body["rejections"] == [{"id": "rejected", "name": "Занятый ведущий", "reasons": [
        {"code": "busy_on_date", "label": "занят на эту дату"},
        {"code": "over_budget", "label": "цена от выше бюджета"},
        {"code": "format_not_supported", "label": "не берёт этот формат"},
        {"code": "language_not_supported", "label": "не работает на этом языке"},
        {"code": "duration_exceeds_max", "label": "максимум часов меньше запрошенной длительности"},
    ]}]


@pytest.mark.parametrize("changes", [{}, {"category": "Нет категории"}, {"budget_kzt": 1}])
def test_identical_posts_have_byte_identical_json_except_timing(client, changes):
    responses = [client.post("/api/match", json=REQUEST | changes) for _ in range(2)]
    assert all(response.status_code == 200 for response in responses)
    normalized = [re.sub(rb'"timing_ms":\d+(?:\.\d+)?', b'"timing_ms":0', response.content)
                  for response in responses]
    assert normalized[0] == normalized[1]


@pytest.mark.parametrize("filtering_installed", [False, True])
def test_request_error_returns_422_with_russian_detail(client, fake_pipeline, monkeypatch,
                                                      filtering_installed):
    error = type("RequestError", (ValueError,), {}) if filtering_installed else fake_pipeline.RequestError
    monkeypatch.setitem(sys.modules, "matcher.filtering",
                        SimpleNamespace(RequestError=error) if filtering_installed else None)
    detail = "Календарь занятости известен только на 23.09.2026 — 31.12.2026, подбор на 2027-01-01 невозможен."

    def invalid(*args):
        raise error(detail)

    monkeypatch.setattr(fake_pipeline, "run", invalid)
    response = client.post("/api/match", json=REQUEST | {"event_date": "2027-01-01"})
    assert response.status_code == 422
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize("availability", ["missing", "init_semantic", "init_import",
                                         "run_semantic", "run_import", "available"])
def test_semantic_backend_reports_the_scorer_that_actually_succeeded(monkeypatch, availability):
    from app import app
    from matcher import pipeline
    unavailable = type("SemanticUnavailable", (RuntimeError,), {})

    class Embeddings:
        name = "embeddings"

        def __init__(self):
            if availability.startswith("init_"):
                raise ImportError() if availability.endswith("import") else unavailable()

    def match(request, contractors, scorer):
        if scorer.name == "embeddings" and availability.startswith("run_"):
            raise ImportError() if availability.endswith("import") else unavailable()
        return fake_run(request, contractors, scorer)

    monkeypatch.setitem(sys.modules, "matcher.embeddings", None if availability == "missing"
                        else SimpleNamespace(EmbeddingScorer=Embeddings, SemanticUnavailable=unavailable))
    monkeypatch.setitem(sys.modules, "matcher.lexical",
                        SimpleNamespace(LexicalScorer=lambda: SimpleNamespace(name="lexical")))
    monkeypatch.setattr(pipeline, "get_contractors", lambda: list(PROFILES))
    monkeypatch.setattr(pipeline, "run", match)
    monkeypatch.setattr(pipeline, "explain", fake_explain)
    with TestClient(app) as client:
        response = client.post("/api/match", json=REQUEST)
    assert response.status_code == 200
    assert response.json()["semantic_backend"] == ("embeddings" if availability == "available" else "lexical")
    assert [card["id"] for card in response.json()["cards"]] == ["z-first", "a-second", "m-third"]


def test_meta_describes_loaded_contractors_and_calendar(client):
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json() == {
        "cities": ["Алматы", "Астана"], "categories": ["Ведущий", "Флорист"],
        "formats": ["корпоратив", "свадьба"], "languages": ["казахский", "русский"],
        "calendar_start": "2026-09-23", "calendar_end": "2026-12-31",
        "counts": {"Алматы": {"Ведущий": 4, "Флорист": 0},
                   "Астана": {"Ведущий": 0, "Флорист": 1}},
    }


@pytest.mark.parametrize("file_exists", [False, True])
def test_demo_returns_file_contents_or_an_empty_list(client, monkeypatch, tmp_path, file_exists):
    import app
    path = tmp_path / "queries.json"
    if file_exists:
        path.write_text(json.dumps(DEMOS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(app, "DEMO_PATH", path)
    response = client.get("/api/demo")
    assert response.status_code == 200
    assert response.json() == (DEMOS if file_exists else [])


def test_legacy_serves_a_self_contained_russian_page(client):
    response = client.get("/legacy")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<html lang="ru">' in response.text
    assert "Подбор подрядчиков" in response.text and "Подобрать" in response.text
    assert '<script src=' not in response.text


def test_home_serves_qalau_and_docs_keep_separate_routes(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "QALAU" in response.text
    assert 'src="app.mjs"' in response.text
    assert 'href="/docs-ui" aria-current="page"' in client.get("/docs-ui").text
    assert "swagger-ui" in client.get("/docs").text


@pytest.mark.parametrize("asset", ["style.css", "app.mjs", "api.mjs", "services.mjs",
                                    "docs.css", "docs.mjs", "fonts/OFL.txt",
                                    "assets/generated/service-host.png"])
def test_qalau_assets_are_served(client, asset):
    response = client.get("/" + asset)
    assert response.status_code == 200
    assert len(response.content) > 0


@pytest.mark.parametrize("file_exists", [False, True])
def test_report_routes_read_optional_files(client, monkeypatch, tmp_path, file_exists):
    import app
    monkeypatch.setattr(app, "ROOT", tmp_path)
    report = {"runs": [{"name": "demo", "passed": True}]}
    if file_exists:
        (tmp_path / "data").mkdir()
        (tmp_path / "data/test_report.json").write_text(json.dumps(report), encoding="utf-8")
        (tmp_path / "web/qalau").mkdir(parents=True)
        (tmp_path / "web/qalau/tests.html").write_text("<h1>Результаты проверок</h1>", encoding="utf-8")
    response = client.get("/api/tests")
    assert response.status_code == 200
    assert response.json() == (report if file_exists else {"runs": [], "note": "отчёт ещё не сформирован"})
    page = client.get("/tests")
    assert page.status_code == (200 if file_exists else 404)
    assert ("Результаты проверок" if file_exists else "ещё не") in page.text
