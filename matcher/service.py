"""Match orchestration and factual Russian shortfall explanations."""
from __future__ import annotations

from collections import Counter

from matcher import ranking, reasons
from matcher.filtering import filter_pool
from matcher.model import MAX_CARDS, Contractor, MatchRequest, MatchResult, Outcome, Rejection, RejectReason, SemanticScorer


_RELATED = (
    ("Декоратор", "Флорист", "Подарки и сувениры"),
    ("Ведущий", "Ведущий церемонии"),
    ("Фотограф", "Видеограф", "Фото и видеобудки"),
    ("Инструменталист", "Лайв-бэнд", "Национальный ансамбль", "Танцевальный коллектив", "Шоу-программа"),
    ("Банкетный зал", "Загородная площадка", "Ресторан", "Отель"),
)


def _plural(count: int, one: str, few: str, many: str) -> str:
    if 11 <= count % 100 <= 14:
        return many
    return one if count % 10 == 1 else few if 2 <= count % 10 <= 4 else many


def _no_category(request: MatchRequest, contractors: list[Contractor]) -> str:
    note = f"В городе {request.city} нет подрядчиков категории «{request.category}»."
    present = {category for c in contractors if c.city == request.city for category in c.categories}
    related = {category for group in _RELATED if request.category in group for category in group}
    suggestions = sorted(present & related)[:3]
    if suggestions:
        note += " Ближайшие варианты: категории с похожим назначением в этом городе: "
        note += ", ".join(suggestions) + "."
    elif present:
        note += " Другие категории в этом городе: " + ", ".join(sorted(present)[:3]) + "."
    else:
        note += " В этом городе нет других категорий."
    elsewhere = Counter(c.city for c in contractors if request.category in c.categories and c.city != request.city)
    for city, count in sorted(elsewhere.items()):
        note += f" Категория есть в {city}: {count} {_plural(count, 'профиль', 'профиля', 'профилей')}."
    return note


def _money(value: int) -> str:
    return f"{value:,}".replace(",", "\u2009") + " ₸"


def _rejection_summary(request: MatchRequest, rejections: tuple[Rejection, ...]) -> str:
    parts = []
    for reason in RejectReason:
        rejected = [r.contractor for r in rejections if reason in r.reasons]
        if not rejected:
            continue
        count = len(rejected)
        names = ", ".join(c.name for c in rejected)
        if reason == RejectReason.BUSY_ON_DATE:
            verb = _plural(count, "занят", "заняты", "заняты")
            text = f"{count} {verb} на {request.event_date:%d.%m.%Y} ({names})"
        elif reason == RejectReason.OVER_BUDGET:
            prices = sorted({c.price_from_kzt for c in rejected})
            if len(prices) == 1:
                price_note = f"цена от {_money(prices[0])} при бюджете {_money(request.budget_kzt)}"
            else:
                price_note = "цены от: " + ", ".join(_money(price) for price in prices)
                price_note += f"; бюджет {_money(request.budget_kzt)}"
            text = f"{count} дороже бюджета ({names}; {price_note})"
        elif reason in (RejectReason.FORMAT_NOT_SUPPORTED, RejectReason.LANGUAGE_NOT_SUPPORTED):
            noun, value = ("формат", request.event_format) if reason == RejectReason.FORMAT_NOT_SUPPORTED else ("язык", request.language)
            verb = _plural(count, "не поддерживает", "не поддерживают", "не поддерживают")
            text = f"{count} {verb} {noun} «{value}» ({names})"
        else:
            verb = _plural(count, "не подходит", "не подходят", "не подходят")
            text = f"{count} {verb} по длительности ({names}; запрошено {request.duration_hours} ч)"
        parts.append(text)
    return "; ".join(parts)


def run(request: MatchRequest, contractors: list[Contractor], scorer: SemanticScorer) -> MatchResult:
    pool, eligible, rejections = filter_pool(contractors, request)
    all_scored = ranking.score_all(eligible, request, scorer) if eligible else ()
    cards = all_scored[:MAX_CARDS]
    outcome, note = Outcome.MATCHED, None
    if not pool:
        outcome, note = Outcome.NO_CATEGORY_IN_CITY, _no_category(request, contractors)
    elif not eligible:
        outcome = Outcome.NONE_ELIGIBLE
        note = f"Кандидаты в категории есть ({len(pool)}), но ни один не проходит: "
        note += _rejection_summary(request, rejections) + "."
    elif len(cards) < MAX_CARDS:
        profiles = f"{len(pool)} {_plural(len(pool), 'профиль', 'профиля', 'профилей')}"
        note = f"Показано {len(cards)} из {MAX_CARDS}: "
        if len(pool) < MAX_CARDS:
            note += f"в городе всего {profiles} этой категории"
        else:
            note += f"в категории {profiles}"
        if rejections:
            note += ", " + _rejection_summary(request, rejections)
        note += "."
    if note is not None and any(len(r.reasons) > 1 for r in rejections):
        note += " Причины могут пересекаться."
    result = MatchResult(
        request=request, outcome=outcome, cards=cards, rejections=rejections,
        pool_size=len(pool), eligible_count=len(eligible), shortfall_note=note,
        semantic_backend=scorer.name,
    )
    return reasons.assign(result, all_scored, contractors, scorer)
