"""Hard eligibility checks; every rejection keeps all applicable reasons."""
from __future__ import annotations

from matcher.model import (
    CALENDAR_END, CALENDAR_START, CITIES, EVENT_FORMATS, LANGUAGES,
    Contractor, MatchRequest, Rejection, RejectReason,
)


class RequestError(ValueError):
    """Invalid matching request, with a Russian message suitable for HTTP 422."""


def _validate(request: MatchRequest) -> None:
    if not CALENDAR_START <= request.event_date <= CALENDAR_END:
        raise RequestError(
            "Календарь занятости известен только на "
            f"{CALENDAR_START:%d.%m.%Y} — {CALENDAR_END:%d.%m.%Y}, "
            f"подбор на {request.event_date:%d.%m.%Y} невозможен."
        )
    if request.city not in CITIES:
        raise RequestError(f"Неизвестный город: {request.city}.")
    if request.event_format not in EVENT_FORMATS:
        raise RequestError(f"Неизвестный формат мероприятия: {request.event_format}.")
    if request.language is not None and request.language not in LANGUAGES:
        raise RequestError(f"Неизвестный язык: {request.language}.")
    if request.budget_kzt <= 0:
        raise RequestError("Бюджет должен быть больше нуля.")
    if request.duration_hours is not None and request.duration_hours <= 0:
        raise RequestError("Продолжительность должна быть больше нуля.")


def filter_pool(
    contractors: list[Contractor], request: MatchRequest,
) -> tuple[list[Contractor], list[Contractor], tuple[Rejection, ...]]:
    _validate(request)
    pool = sorted(
        (c for c in contractors if c.city == request.city and request.category in c.categories),
        key=lambda c: c.id,
    )
    eligible, rejections = [], []
    for contractor in pool:
        failures = (
            (RejectReason.BUSY_ON_DATE, request.event_date in contractor.busy_dates),
            (RejectReason.OVER_BUDGET, contractor.price_from_kzt > request.budget_kzt),
            (RejectReason.FORMAT_NOT_SUPPORTED, request.event_format not in contractor.event_formats),
            (RejectReason.LANGUAGE_NOT_SUPPORTED,
             request.language is not None and request.language not in contractor.languages),
            (RejectReason.DURATION_EXCEEDS_MAX,
             request.duration_hours is not None and contractor.max_hours is not None
             and contractor.max_hours < request.duration_hours),
        )
        reasons = tuple(reason for reason, failed in failures if failed)
        if reasons:
            rejections.append(Rejection(contractor, reasons))
        else:
            eligible.append(contractor)
    return pool, eligible, tuple(rejections)
