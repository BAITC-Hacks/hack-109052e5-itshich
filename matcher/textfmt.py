"""Consistent Russian display formatting."""
from datetime import date


def money(value: int) -> str:
    return f"{value:,}".replace(",", "\u2009") + " ₸"


def format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")
