"""Search the shipped catalogue for deterministic, reproducible demo requests."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path

# Support `uv run python scripts/find_demo_queries.py` from a source checkout.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from matcher.data import load_contractors
from matcher.filtering import filter_pool
from matcher.embeddings import EmbeddingScorer, request_text, split_sentences
from matcher.model import Contractor, MatchRequest, MatchResult, Outcome, RejectReason, SemanticScorer
from matcher.pipeline import choose_scorer
from matcher.service import run


def _entry(name: str, result: MatchResult, note: str) -> dict:
    request = {key: value for key, value in asdict(result.request).items() if value is not None}
    request["event_date"] = result.request.event_date.isoformat()
    return {
        "name": name, "request": request, "expected_outcome": result.outcome.value,
        "expected_card_ids": [card.contractor.id for card in result.cards], "note": note,
        "semantic_backend": result.semantic_backend,
    }


def _dense(contractors: list[Contractor], days: list[date], scorer: SemanticScorer) -> MatchResult:
    budgets = sorted({c.price_from_kzt for c in contractors if c.city == "Алматы" and "Ведущий" in c.categories})
    for day in days:
        for budget in budgets:
            request = MatchRequest("Алматы", day, "корпоратив", "Ведущий", budget, duration_hours=4)
            _, eligible, _ = filter_pool(contractors, request)
            if len(eligible) < 5:
                continue
            result = run(request, contractors, scorer)
            cheapest = [c.id for c in sorted(eligible, key=lambda c: (c.price_from_kzt, c.id))[:3]]
            if ([card.contractor.id for card in result.cards] != cheapest
                    and len({card.score.total for card in result.cards}) > 1):
                return result
    raise RuntimeError("Не найден плотный запрос с нетривиальным ранжированием")


def _rare(contractors: list[Contractor], days: list[date], scorer: SemanticScorer) -> MatchResult:
    for category in ("Флорист", "Декоратор"):
        pool = [c for c in contractors if c.city == "Алматы" and category in c.categories]
        if not pool:
            continue
        for day in days:
            request = MatchRequest("Алматы", day, "свадьба", category, max(c.price_from_kzt for c in pool))
            result = run(request, contractors, scorer)
            if result.outcome == Outcome.MATCHED and len(result.cards) == min(2, len(pool)):
                return result
    raise RuntimeError("Не найден редкий запрос с одной или двумя карточками")


def _none_eligible(contractors: list[Contractor], days: list[date], scorer: SemanticScorer) -> MatchResult:
    budgets = sorted({c.price_from_kzt for c in contractors if c.city == "Алматы" and "Ведущий" in c.categories})
    for day in days:
        for budget in budgets:
            request = MatchRequest("Алматы", day, "корпоратив", "Ведущий", budget, duration_hours=4)
            result = run(request, contractors, scorer)
            reasons = {reason for rejection in result.rejections for reason in rejection.reasons}
            # Require mixed causes and at least one affordable but rejected profile.
            if (result.outcome == Outcome.NONE_ELIGIBLE and len(reasons) >= 2
                    and any(RejectReason.OVER_BUDGET not in r.reasons for r in result.rejections)):
                return result
    raise RuntimeError("Не найден запрос с разными причинами отказа у всех кандидатов")


def _date_pair(first: MatchResult, contractors: list[Contractor], days: list[date], scorer: SemanticScorer) -> MatchResult:
    top = first.cards[0].contractor
    for day in days:
        if day <= first.request.event_date or day not in top.busy_dates:
            continue
        second = run(replace(first.request, event_date=day), contractors, scorer)
        if len(second.cards) >= 2 and top.id not in {card.contractor.id for card in second.cards}:
            return second
    raise RuntimeError("Не найдена вторая дата с занятым лидером первого запроса")


def main() -> None:
    contractors = load_contractors(ROOT / "data" / "contractors.csv")
    scorer = choose_scorer()
    if not isinstance(scorer, EmbeddingScorer):
        raise RuntimeError("Demo generation requires the production EmbeddingScorer")
    scorer.cache_texts([text for contractor in contractors
                       for text in [contractor.description, *split_sentences(contractor.description)]])
    start, end = date(2026, 10, 1), date(2026, 11, 30)
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    dense = _dense(contractors, days, scorer)
    rare = _rare(contractors, days, scorer)
    absent = run(replace(rare.request, city="Астана", category="Декоратор"), contractors, scorer)
    if absent.outcome != Outcome.NO_CATEGORY_IN_CITY:
        raise RuntimeError("В исходных данных больше нет пустой категории Декоратор / Астана")
    none = _none_eligible(contractors, days, scorer)
    second = _date_pair(dense, contractors, days, scorer)
    # Empty outcomes do not score candidates, but their query vectors ship too.
    scorer.cache_texts([request_text(result.request) for result in (dense, rare, absent, none, second)])
    top = dense.cards[0].contractor
    entries = [
        _entry("dense", dense, f"Доступно {dense.eligible_count} из {dense.pool_size} профилей; "
               "рейтинг отличается от выбора трёх самых дешёвых. Указаны цены от."),
        _entry("rare", rare, rare.shortfall_note),
        _entry("empty_no_category", absent, absent.shortfall_note),
        _entry("empty_none_eligible", none, none.shortfall_note),
        _entry("date_pair_a", dense, f"Лидер {top.name} ({top.id}) свободен {dense.request.event_date:%d.%m.%Y}."),
        _entry("date_pair_b", second, f"Все условия сохранены, изменена только дата: "
               f"{top.name} ({top.id}) занят {second.request.event_date:%d.%m.%Y} и исключён."),
    ]
    output = ROOT / "demo" / "queries.json"
    output.parent.mkdir(exist_ok=True)
    content = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    # Leave the file untouched on repeat runs when the catalogue has not changed.
    if not output.exists() or output.read_text(encoding="utf-8") != content:
        output.write_text(content, encoding="utf-8")
    for entry in entries:
        print(f"{entry['name']}: {entry['expected_outcome']} "
              f"{', '.join(entry['expected_card_ids']) or '—'} semantic_backend={entry['semantic_backend']}")
    print("Verified 6 demo queries in demo/queries.json")


if __name__ == "__main__":
    main()
