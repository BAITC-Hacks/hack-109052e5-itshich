from dataclasses import replace
from datetime import date

import pytest

from matcher.lexical import LexicalScorer
from matcher.filtering import RequestError, filter_pool
from matcher.model import MatchRequest, Outcome, RejectReason
from matcher.service import run


def test_full_match_has_grounded_cards_and_no_shortfall(sample_contractors, match_request):
    result = run(match_request, sample_contractors, LexicalScorer())
    assert result.outcome == Outcome.MATCHED
    assert [c.contractor.id for c in result.cards] == ["c-01", "c-02", "c-03"]
    assert result.pool_size == result.eligible_count == 3
    assert result.rejections == () and result.shortfall_note is None
    assert result.request == match_request and result.semantic_backend == "lexical"
    assert result == run(match_request, list(reversed(sample_contractors)), LexicalScorer())


def test_absent_category_suggests_present_related_categories_and_other_city(contractor, match_request):
    candidates = [
        replace(contractor, id="d2", categories=("Декоратор",)),
        replace(contractor, id="d1", categories=("Декоратор",)),
        replace(contractor, id="f1", categories=("Флорист",), city="Астана"),
        replace(contractor, id="v1", categories=("Ведущий",), city="Астана"),
    ]
    request = replace(match_request, city="Астана", category="Декоратор")
    result = run(request, candidates, LexicalScorer())
    assert result.outcome == Outcome.NO_CATEGORY_IN_CITY
    assert result.pool_size == result.eligible_count == 0
    assert result.cards == result.rejections == ()
    assert result.shortfall_note == (
        "В городе Астана нет подрядчиков категории «Декоратор». "
        "Ближайшие варианты: категории с похожим назначением в этом городе: Флорист. "
        "Категория есть в Алматы: 2 профиля."
    )


def test_none_eligible_explains_all_reasons_with_counts_names_and_from_prices(contractor, match_request):
    candidates = [
        replace(contractor, price_from_kzt=1_200_000, max_hours=2, languages=("казахский",),
                event_formats=("свадьба",), busy_dates=frozenset({match_request.event_date})),
        replace(contractor, id="c-02", name="Бек", price_from_kzt=1_200_000,
                languages=("английский",), busy_dates=frozenset({match_request.event_date})),
    ]
    request = replace(match_request, language="английский", duration_hours=5)
    result = run(request, list(reversed(candidates)), LexicalScorer())
    assert result.outcome == Outcome.NONE_ELIGIBLE
    assert result.pool_size == 2 and result.eligible_count == 0 and result.cards == ()
    assert len(result.rejections) == 2
    assert result.shortfall_note == (
        "Кандидаты в категории есть (2), но ни один не проходит: "
        "2 заняты на 14.11.2026 (Алия, Бек); "
        "2 дороже бюджета (Алия, Бек; цена от 1\u2009200\u2009000 ₸ при бюджете 800\u2009000 ₸); "
        "1 не поддерживает формат «корпоратив» (Алия); "
        "1 не поддерживает язык «английский» (Алия); "
        "1 не подходит по длительности (Алия; запрошено 5 ч). "
        "Причины могут пересекаться."
    )


@pytest.mark.parametrize("size,busy,note", [
    (1, False, "Показано 1 из 3: в городе всего 1 профиль этой категории."),
    (2, False, "Показано 2 из 3: в городе всего 2 профиля этой категории."),
    (2, True, "Показано 1 из 3: в городе всего 2 профиля этой категории, 1 занят на 14.11.2026 (Алия)."),
    (3, True, "Показано 2 из 3: в категории 3 профиля, 1 занят на 14.11.2026 (Алия)."),
])
def test_matched_shortfall_distinguishes_small_pool_from_rejections(sample_contractors, match_request, size, busy, note):
    candidates = sample_contractors[:size]
    if busy:
        candidates[0] = replace(candidates[0], busy_dates=frozenset({match_request.event_date}))
    result = run(match_request, candidates, LexicalScorer())
    assert result.outcome == Outcome.MATCHED
    assert result.pool_size == size and len(result.cards) == size - int(busy)
    assert result.shortfall_note == note


@pytest.mark.parametrize("name", [
    "dense", "rare", "empty_no_category", "empty_none_eligible", "date_pair_a", "date_pair_b",
])
def test_saved_demo_outcomes_and_ordered_cards_are_reproducible(real_contractors, demo_queries, name):
    entry, = [entry for entry in demo_queries if entry["name"] == name]
    request = MatchRequest(**(entry["request"] | {"event_date": date.fromisoformat(entry["request"]["event_date"])}))
    result = run(request, real_contractors, LexicalScorer())
    assert result.outcome.value == entry["expected_outcome"]
    assert [card.contractor.id for card in result.cards] == entry["expected_card_ids"]
    assert result == run(request, real_contractors, LexicalScorer())


def test_demo_requests_have_required_real_data_characteristics(real_contractors, demo_queries):
    assert len(demo_queries) == 6
    results = {}
    for entry in demo_queries:
        assert set(entry) == {"name", "request", "expected_outcome", "expected_card_ids", "note"}
        assert entry["note"]
        request = MatchRequest(**(entry["request"] | {"event_date": date.fromisoformat(entry["request"]["event_date"])}))
        results[entry["name"]] = run(request, real_contractors, LexicalScorer())
    dense = results["dense"]
    assert (dense.request.category, dense.request.city, dense.request.event_format) == ("Ведущий", "Алматы", "корпоратив")
    assert dense.request.event_date.month in (10, 11) and dense.eligible_count >= 5
    _, eligible, _ = filter_pool(real_contractors, dense.request)
    cheapest = sorted(eligible, key=lambda c: (c.price_from_kzt, c.id))[:3]
    assert [c.contractor.id for c in dense.cards] != [c.id for c in cheapest]
    rare = results["rare"]
    assert rare.request.city == "Алматы" and rare.request.category in ("Флорист", "Декоратор")
    assert rare.outcome == Outcome.MATCHED and 0 < len(rare.cards) < 3 and rare.shortfall_note
    assert results["empty_no_category"].outcome == Outcome.NO_CATEGORY_IN_CITY
    none = results["empty_none_eligible"]
    assert none.outcome == Outcome.NONE_ELIGIBLE and none.pool_size > 0
    assert len({reason for rejection in none.rejections for reason in rejection.reasons}) >= 2
    first, second = results["date_pair_a"], results["date_pair_b"]
    assert first.request == replace(second.request, event_date=first.request.event_date)
    assert first.request.event_date != second.request.event_date
    top = first.cards[0].contractor
    assert second.request.event_date in top.busy_dates
    rejection, = [r for r in second.rejections if r.contractor.id == top.id]
    assert rejection.reasons == (RejectReason.BUSY_ON_DATE,)
    assert top.id not in {c.contractor.id for c in second.cards}
    assert [c.contractor.id for c in first.cards] != [c.contractor.id for c in second.cards]


def test_unknown_category_is_a_normal_empty_outcome_with_at_most_three_alternatives(contractor, match_request):
    categories = ("Флорист", "Банкетный зал", "Ведущий", "Декоратор", "Фотограф")
    candidates = [replace(contractor, id=str(i), categories=(category,)) for i, category in enumerate(categories)]
    result = run(replace(match_request, category="Чтец"), candidates, LexicalScorer())
    assert result.outcome == Outcome.NO_CATEGORY_IN_CITY
    assert result.shortfall_note == (
        "В городе Алматы нет подрядчиков категории «Чтец». "
        "Другие категории в этом городе: Банкетный зал, Ведущий, Декоратор."
    )


def test_calendar_error_remains_an_error_through_service(match_request):
    with pytest.raises(RequestError, match="подбор на 01.01.2027 невозможен"):
        run(replace(match_request, event_date=date(2027, 1, 1)), [], LexicalScorer())
