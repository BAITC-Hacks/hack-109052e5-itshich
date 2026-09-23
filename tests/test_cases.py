"""Live-case report contracts, with external matcher calls replaced at the boundary."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from matcher import pipeline
from matcher.api_types import MatchRequestDTO

ROOT = Path(__file__).resolve().parents[1]
REQUEST = dict(city="Алматы", event_date="2026-10-04", event_format="корпоратив",
               category="Ведущий", budget_kzt=2_000_000)


def runner():
    from scripts import run_cases
    return run_cases


@pytest.fixture
def response():
    return dict(outcome="matched", outcome_title_ru="Подобрали", shortfall_note="Есть запас",
                cards=[dict(id="b", name="Бек", price_from_kzt=100_000,
                            explanation="Работает на казахском", explanation_source="template",
                            facts={"reasons": [{"code": "LANGUAGE_REQUEST_MATCH", "primary": True},
                                                {"code": "BUDGET_FITS", "primary": False}]}),
                       dict(id="a", name="Алия", price_from_kzt=200_000,
                            explanation="Опыт свадьбы", explanation_source="llm",
                            facts={"reasons": [{"code": "DESCRIPTION_ASPECT", "primary": True}]})],
                rejections=[], pool_size=2, eligible_count=2, semantic_backend="lexical", timing_ms=1)


def execute(tmp_path, monkeypatch, response, expect):
    monkeypatch.setattr(pipeline, "answer", lambda request: response)
    source, output = tmp_path / "cases.json", tmp_path / "report.json"
    source.write_text(json.dumps([dict(id="fake", title="Проверка", request=REQUEST, expect=expect)]))
    report = runner().run_cases(source, output)
    assert json.loads(output.read_text()) == report
    assert report["cases"][0]["response"] == response
    return report["cases"][0]


@pytest.mark.parametrize("expect,name,ok", [
    ({"outcome": ["none_eligible", "matched"]}, "outcome", True),
    ({"outcome": "none_eligible"}, "outcome", False),
    ({"min_cards": 2}, "min_cards", True), ({"min_cards": 3}, "min_cards", False),
    ({"max_cards": 2}, "max_cards", True), ({"max_cards": 1}, "max_cards", False),
    ({"card_ids": ["b", "a"]}, "card_ids", True),
    ({"card_ids": ["a", "b"]}, "card_ids", False),
    ({"must_include_ids": ["a"]}, "must_include_ids", True),
    ({"must_include_ids": ["missing"]}, "must_include_ids", False),
    ({"must_exclude_ids": ["missing"]}, "must_exclude_ids", True),
    ({"must_exclude_ids": ["a"]}, "must_exclude_ids", False),
    ({"primary_codes_any": ["LANGUAGE_REQUEST_MATCH", "DESCRIPTION_ASPECT"]}, "primary_codes_any", True),
    ({"primary_codes_any": ["LANGUAGE_REQUEST_MATCH", "BUDGET_FITS"]}, "primary_codes_any", False),
    ({"text_must_contain": ["казахском", "запас"]}, "text_must_contain", True),
    ({"text_must_contain": ["казахском", "нет текста"]}, "text_must_contain", False),
    ({"status_code": 422}, "status_code", False),
])
def test_checks_report_expected_and_actual(tmp_path, monkeypatch, response, expect, name, ok):
    entry = execute(tmp_path, monkeypatch, response, {"outcome": "matched"} | expect)
    check = next(c for c in entry["checks"] if c["name"] == name)
    assert check["ok"] is ok
    assert "Ожидалось" in check["detail"] and "Получено" in check["detail"]
    assert entry["status"] == ("passed" if ok else "failed")
    assert entry["explanation_source"] == ["template", "llm"]
    assert entry["semantic_backend"] == "lexical"


def test_request_error_and_order_and_summary(tmp_path, monkeypatch):
    def invalid(request):
        assert request.event_date.isoformat() == "2026-10-04"
        raise pipeline.RequestError("Календарь недоступен")
    monkeypatch.setattr(pipeline, "answer", invalid)
    source = tmp_path / "cases.json"
    source.write_text(json.dumps([
        dict(id=id_, title="Календарь", request=REQUEST,
             expect=dict(outcome="request_error", status_code=422, max_cards=0,
                         text_must_contain=["Календарь"])) for id_ in ["z", "a"]]))
    report = runner().run_cases(source, tmp_path / "out.json")
    assert report["summary"] == dict(total=2, passed=2, failed=0)
    assert [c["id"] for c in report["cases"]] == ["z", "a"]
    assert report["cases"][0]["response"] == {"status_code": 422, "detail": "Календарь недоступен"}
    assert report["cases"][0]["timing_ms"] >= 0


def test_cli_strict_only_changes_exit_code(tmp_path, monkeypatch, response):
    module = runner()
    monkeypatch.setattr(pipeline, "answer", lambda request: response)
    source = tmp_path / "cases.json"
    source.write_text(json.dumps([dict(id="fail", title="Сбой", request=REQUEST,
                                     expect={"outcome": "none_eligible"})]))
    args = ["--cases", str(source), "--output", str(tmp_path / "out.json")]
    assert module.main(args) == 0
    assert module.main(args + ["--strict"]) == 1
    assert json.loads((tmp_path / "out.json").read_text())["summary"]["failed"] == 1


def test_cases_schema_and_committed_report():
    cases = runner().load_cases(ROOT / "demo/cases.json")
    assert len(cases) >= 17
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        MatchRequestDTO.model_validate(case["request"])
        assert case["title"] and case["expect"]["outcome"]
    report = json.loads((ROOT / "data/live_cases.json").read_text())
    assert [c["id"] for c in report["cases"]] == [c["id"] for c in cases]
    assert report["summary"] == dict(total=len(report["cases"]),
        passed=sum(c["status"] == "passed" for c in report["cases"]),
        failed=sum(c["status"] == "failed" for c in report["cases"]))
    for case in report["cases"]:
        assert (case["status"] == "passed") == all(c["ok"] for c in case["checks"])


@pytest.mark.parametrize("exists", [False, True])
def test_get_cases_optional_report(tmp_path, monkeypatch, exists):
    import app
    monkeypatch.setattr(app, "ROOT", tmp_path)
    report = dict(cases=[], summary=dict(total=0, passed=0, failed=0))
    if exists:
        (tmp_path / "data").mkdir()
        (tmp_path / "data/live_cases.json").write_text(json.dumps(report))
    with TestClient(app.app) as client:
        response = client.get("/api/cases")
    assert response.status_code == 200
    assert response.json() == report if exists else response.json()["cases"] == []
    if not exists:
        assert response.json()["note"]


def test_post_cases_returns_and_persists_fresh_report(tmp_path, monkeypatch):
    import app
    module = runner()
    monkeypatch.setattr(app, "ROOT", tmp_path)
    report = dict(cases=[], summary=dict(total=0, passed=0, failed=0), generated_at="now", git_sha="test")
    def fresh(cases_path, output_path):
        assert cases_path == tmp_path / "demo/cases.json"
        output_path.parent.mkdir(parents=True)
        output_path.write_text(json.dumps(report))
        return report
    monkeypatch.setattr(module, "run_cases", fresh)
    with TestClient(app.app) as client:
        response = client.post("/api/cases/run")
        assert response.status_code == 200
        assert response.json() == report
        assert client.get("/api/cases").json() == report


@pytest.mark.parametrize("change", [
    {"expect": {"outcome": "typo"}},
    {"expect": {"outcome": []}},
    {"expect": {"outcome": "matched", "min_cards": 3, "max_cards": 1}},
    {"expect": {"outcome": "matched", "max_card": 3}},
    {"request": REQUEST | {"event_date": "invalid"}},
    {"title": ""},
])
def test_case_schema_rejects_invalid_inputs(tmp_path, change):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([dict(id="one", title="Проверка", request=REQUEST,
                                    expect={"outcome": "matched"}) | change]))
    with pytest.raises(ValueError):
        runner().load_cases(path)


def test_duplicate_case_ids_are_rejected(tmp_path):
    path = tmp_path / "cases.json"
    case = dict(id="same", title="Проверка", request=REQUEST, expect={"outcome": "matched"})
    path.write_text(json.dumps([case, case]))
    with pytest.raises(ValueError, match="unique"):
        runner().load_cases(path)


def test_omitted_dto_reasons_are_replayed_without_changing_response(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from matcher.api_types import to_dto
    from matcher.model import Reason, ReasonFamily
    from tests.test_api import fake_run, fake_explain

    request = MatchRequestDTO.model_validate(REQUEST).to_domain()
    scorer = SimpleNamespace(name="lexical")
    result = fake_run(request, [], scorer)
    result = replace(result, cards=tuple(replace(card, reasons=(Reason(
        "BUDGET_HEADROOM", ReasonFamily.BUDGET, {"headroom_pct": "25"}, primary=True),))
        for card in result.cards))
    response = to_dto(result, fake_explain(result), 1, "lexical")
    monkeypatch.setattr(pipeline, "lexical_scorer", lambda: scorer)
    monkeypatch.setattr(pipeline, "get_contractors", lambda: [])
    monkeypatch.setattr(pipeline, "run", lambda req, contractors, used_scorer: result)
    entry = execute(tmp_path, monkeypatch, response,
                    {"outcome": "matched", "primary_codes_any": ["BUDGET_HEADROOM"]})
    assert entry["status"] == "passed"
    assert entry["card_reasons"]["z-first"][0]["primary"] is True
    assert "reasons" not in entry["response"]["cards"][0]["facts"]
    monkeypatch.setattr(pipeline, "run", lambda *args: replace(result, cards=result.cards[::-1]))
    with pytest.raises(ValueError, match="card order"):
        runner().card_reasons(request, response)
