#!/usr/bin/env python3
"""Run editable live matcher cases and publish their actual responses and checks."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict, Field, model_validator

from matcher import pipeline
from matcher.api_types import MatchRequestDTO

Outcome = Literal["matched", "no_category_in_city", "none_eligible", "request_error"]


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Outcome | list[Outcome]
    min_cards: int | None = Field(default=None, ge=0)
    max_cards: int | None = Field(default=None, ge=0)
    card_ids: list[str] | None = None
    must_include_ids: list[str] | None = None
    must_exclude_ids: list[str] | None = None
    primary_codes_any: list[str] | None = None
    text_must_contain: list[str] | None = None
    status_code: Literal[200, 422] = 200

    @model_validator(mode="after")
    def consistent(self):
        if self.outcome == []:
            raise ValueError("outcome must not be an empty list")
        if self.min_cards is not None and self.max_cards is not None and self.min_cards > self.max_cards:
            raise ValueError("min_cards exceeds max_cards")
        return self


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    request: MatchRequestDTO
    expect: Expectations


def load_cases(path):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("cases.json must contain a list")
    for case in cases:
        Case.model_validate(case)
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    return cases


def card_reasons(request, response):
    """Keep the API DTO intact; expose internal reasons when that DTO omits them.

    Replay only deterministic matching, using the backend that answered. No extra
    explanation call and no process-global monkeypatch (HTTP requests can overlap).
    Refuse to attach reasons if the replay produces a different card order.
    """
    cards = response.get("cards", [])
    if all("reasons" in card for card in cards):
        return {card["id"]: card["reasons"] for card in cards}
    if all("reasons" in card.get("facts", {}) for card in cards):
        return {card["id"]: card["facts"]["reasons"] for card in cards}
    if response["semantic_backend"] == "lexical":
        scorer = pipeline.lexical_scorer()
    else:
        from matcher.embeddings import EmbeddingScorer
        scorer = EmbeddingScorer()
    result = pipeline.run(request, pipeline.get_contractors(), scorer)
    if [c.contractor.id for c in result.cards] != [c["id"] for c in cards]:
        raise ValueError("Reason replay differs from the actual response card order")
    return {card.contractor.id: [asdict(reason) for reason in card.reasons] for card in result.cards}


def evaluate(expect, response, reasons):
    checks = []
    def check(name, expected, actual, ok):
        checks.append(dict(name=name, ok=bool(ok), detail=
            f"Ожидалось: {json.dumps(expected, ensure_ascii=False)}. "
            f"Получено: {json.dumps(actual, ensure_ascii=False)}."))

    status_code = response.get("status_code", 200)
    expected_status = expect.get("status_code", 200)
    check("status_code", expected_status, status_code, expected_status == status_code)
    outcome = "request_error" if status_code == 422 else response.get("outcome")
    outcomes = expect["outcome"] if isinstance(expect["outcome"], list) else [expect["outcome"]]
    check("outcome", expect["outcome"], outcome, outcome in outcomes)
    cards = response.get("cards", [])
    ids = [card["id"] for card in cards]
    for name in ("min_cards", "max_cards"):
        if name in expect:
            ok = len(cards) >= expect[name] if name == "min_cards" else len(cards) <= expect[name]
            check(name, expect[name], len(cards), ok)
    if "card_ids" in expect:
        check("card_ids", expect["card_ids"], ids, ids == expect["card_ids"])
    for name in ("must_include_ids", "must_exclude_ids"):
        if name in expect:
            ok = all((id_ in ids) == (name == "must_include_ids") for id_ in expect[name])
            check(name, expect[name], ids, ok)
    if "primary_codes_any" in expect:
        codes = [reason["code"] for card in cards for reason in reasons.get(card["id"], [])
                 if reason.get("primary")]
        check("primary_codes_any", expect["primary_codes_any"], codes,
              all(code in codes for code in expect["primary_codes_any"]))
    if "text_must_contain" in expect:
        texts = [response.get("shortfall_note") or "", response.get("detail") or "",
                 *(card.get("explanation", "") for card in cards)]
        check("text_must_contain", expect["text_must_contain"], texts,
              all(any(text in value for value in texts) for text in expect["text_must_contain"]))
    return checks


def run_cases(cases_path=ROOT / "demo/cases.json", output_path=ROOT / "data/live_cases.json"):
    cases = []
    for case in load_cases(cases_path):
        started = perf_counter()
        request = MatchRequestDTO.model_validate(case["request"]).to_domain()
        try:
            response = pipeline.answer(request)
        except pipeline.RequestError as exc:
            response = dict(status_code=422, detail=str(exc))
        reasons = card_reasons(request, response)
        checks = evaluate(case["expect"], response, reasons)
        sources = list(dict.fromkeys(card["explanation_source"] for card in response.get("cards", [])))
        cases.append(dict(**case, status="passed" if all(c["ok"] for c in checks) else "failed",
                          checks=checks, response=response, card_reasons=reasons,
                          timing_ms=round((perf_counter() - started) * 1000, 2),
                          explanation_source=sources, semantic_backend=response.get("semantic_backend")))
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    passed = sum(case["status"] == "passed" for case in cases)
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), git_sha=sha,
                  summary=dict(total=len(cases), passed=passed, failed=len(cases) - passed), cases=cases)
    write_json(output_path, report)
    return report


def write_json(output_path, report):
    """Atomically publish reports for HTTP readers."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    # HTTP readers always see a complete report, including during a fresh run.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "demo/cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/live_cases.json")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero when an expectation fails")
    args = parser.parse_args(argv)
    report = run_cases(args.cases, args.output)
    summary = report["summary"]
    print(f"{summary['passed']}/{summary['total']} passed; {summary['failed']} failed → {args.output}")
    return int(args.strict and summary["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
