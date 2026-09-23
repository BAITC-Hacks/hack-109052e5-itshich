from dataclasses import replace
from datetime import date

import pytest

from matcher.filtering import filter_pool
from matcher import filtering
from matcher.model import CALENDAR_END, CALENDAR_START, RejectReason


@pytest.mark.parametrize("changes,reasons", [
    ({"busy_dates": frozenset({date(2026, 11, 14)})}, (RejectReason.BUSY_ON_DATE,)),
    ({"price_from_kzt": 800_001}, (RejectReason.OVER_BUDGET,)),
    ({"event_formats": ("свадьба",)}, (RejectReason.FORMAT_NOT_SUPPORTED,)),
    ({"languages": ("казахский",)}, (RejectReason.LANGUAGE_NOT_SUPPORTED,)),
    ({"max_hours": 3}, (RejectReason.DURATION_EXCEEDS_MAX,)),
    ({"busy_dates": frozenset({date(2026, 11, 14)}), "price_from_kzt": 800_001,
      "event_formats": ("свадьба",), "languages": ("казахский",), "max_hours": 3},
     tuple(RejectReason)),
])
def test_rejections_report_every_failure_in_contract_order(contractor, match_request, changes, reasons):
    candidate = replace(contractor, **changes)
    request = replace(match_request, language="русский", duration_hours=4)
    pool, eligible, rejections = filter_pool([candidate], request)
    assert pool == [candidate]
    assert eligible == []
    assert [(r.contractor.id, r.reasons) for r in rejections] == [(candidate.id, reasons)]


@pytest.mark.parametrize("field,value,message", [
    ("event_date", date(2026, 9, 22),
     "Календарь занятости известен только на 23.09.2026 — 31.12.2026, подбор на 22.09.2026 невозможен."),
    ("event_date", date(2027, 1, 1),
     "Календарь занятости известен только на 23.09.2026 — 31.12.2026, подбор на 01.01.2027 невозможен."),
    ("city", "Москва", "Неизвестный город: Москва."),
    ("event_format", "бал", "Неизвестный формат мероприятия: бал."),
    ("language", "французский", "Неизвестный язык: французский."),
    ("language", "", "Неизвестный язык: ."),
    ("budget_kzt", 0, "Бюджет должен быть больше нуля."),
    ("budget_kzt", -1, "Бюджет должен быть больше нуля."),
    ("duration_hours", 0, "Продолжительность должна быть больше нуля."),
    ("duration_hours", -1, "Продолжительность должна быть больше нуля."),
])
def test_invalid_request_raises_even_for_empty_catalogue(match_request, field, value, message):
    with pytest.raises(filtering.RequestError) as error:
        filter_pool([], replace(match_request, **{field: value}))
    assert str(error.value) == message


@pytest.mark.parametrize("day", [CALENDAR_START, CALENDAR_END])
@pytest.mark.parametrize("hours,requested_hours", [(8, 8), (None, 500), (1, None)])
def test_boundaries_from_price_and_optional_hours_pass(contractor, match_request, day, hours, requested_hours):
    candidate = replace(contractor, price_from_kzt=800_000, max_hours=hours)
    request = replace(match_request, event_date=day, duration_hours=requested_hours)
    pool, eligible, rejections = filter_pool([candidate], request)
    assert pool == eligible == [candidate]
    assert rejections == ()


def test_pool_uses_exact_city_and_any_category_and_rejections_are_sorted(contractor, match_request):
    request = replace(match_request, category="Ведущий церемонии", budget_kzt=1)
    candidates = [
        replace(contractor, id="z"), replace(contractor, id="a"),
        replace(contractor, id="other-city", city="Астана"),
        replace(contractor, id="other-category", categories=("Ведущий",)),
    ]
    original = candidates.copy()
    pool, eligible, rejections = filter_pool(candidates, request)
    assert {c.id for c in pool} == {"a", "z"}
    assert eligible == []
    assert [r.contractor.id for r in rejections] == ["a", "z"]
    assert candidates == original
    assert filter_pool(candidates, replace(request, category="Неизвестная категория")) == ([], [], ())
