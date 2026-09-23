"""Read the CSV catalogue into the frozen domain contract."""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from matcher.model import CALENDAR_END, CALENDAR_START, CITIES, EVENT_FORMATS, LANGUAGES, Contractor


class DataError(ValueError):
    """A catalogue row violates the CSV contract."""


def _boolean(raw: str) -> bool:
    if raw not in ("True", "False"):
        raise ValueError("ожидается True или False")
    return raw == "True"


def _positive_int(raw: str) -> int:
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) <= 0:
        raise ValueError("ожидается положительное целое число")
    return int(raw)


def _busy_dates(raw: str) -> frozenset[date]:
    dates = set()
    for token in raw.split("|") if raw else ():
        day = date.fromisoformat(token)
        if token != day.isoformat():
            raise ValueError("ожидается дата YYYY-MM-DD")
        if not CALENDAR_START <= day <= CALENDAR_END:
            raise ValueError("дата занятости вне известного календаря")
        dates.add(day)
    return frozenset(dates)


def _parse(row: dict[str, str]) -> Contractor:
    formats = tuple(row["event_formats"].split("|"))
    languages = tuple(row["languages"].split("|"))
    if row["city"] not in CITIES:
        raise ValueError("неизвестный город")
    if any(value not in EVENT_FORMATS for value in formats):
        raise ValueError("неизвестный формат мероприятия")
    if any(value not in LANGUAGES for value in languages):
        raise ValueError("неизвестный язык")
    return Contractor(
        id=row["id"], name=row["anon_name"],
        categories=tuple(row["categories"].split("|")), city=row["city"],
        city_imputed=_boolean(row["city_imputed"]),
        synthetic=_boolean(row["synthetic"]),
        price_from_kzt=_positive_int(row["price_from_kzt"]),
        price_imputed=_boolean(row["price_imputed"]),
        event_formats=formats, languages=languages,
        max_hours=_positive_int(row["max_hours"]) if row["max_hours"] else None,
        busy_dates=_busy_dates(row["busy_dates"]), description=row["description"],
    )


def load_contractors(path: str | Path) -> list[Contractor]:
    """Load path and its optional synthetic_extra.csv sibling, sorted by id."""
    path = Path(path)
    extra = path.with_name("synthetic_extra.csv")
    sources = [path] + ([extra] if extra != path and extra.exists() else [])
    contractors = []
    for source in sources:
        with source.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                try:
                    contractor = _parse(row)
                    if source == extra and not contractor.synthetic:
                        raise ValueError("synthetic_extra.csv требует synthetic=True")
                    contractors.append(contractor)
                except (ValueError, TypeError, KeyError) as exc:
                    raise DataError(f"Строка {row.get('id', '?')}: {exc}") from exc
    return sorted(contractors, key=lambda c: c.id)
