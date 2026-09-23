"""Local semantic fallback using distinct, case-insensitive keyword stems."""
from __future__ import annotations

import re
from types import MappingProxyType
from typing import Literal

from matcher.model import Contractor, MatchRequest


_KEYWORDS = MappingProxyType({
    "свадьба": ("свадьб", "невест", "молодожён", "загс", "wedding"),
    "корпоратив": ("корпоратив", "компани", "тимбилдинг", "team", "бизнес"),
    "конференция": ("конференц", "форум", "спикер", "модератор", "делов"),
    "юбилей": ("юбил",),
    "день рождения": ("день рожд", "именин", "birthday", "детск"),
    "той": ("той", "беташар", "кыз узату", "сундет", "национальн", "казах"),
    "ведущий": ("ведущ", "ведени", "тамад", "модератор"),
    "ведущий церемонии": ("церемони", "регистрац", "бракосочетан"),
    "флорист": ("флорист", "цветоч", "букет", "цветы"),
    "декоратор": ("декор", "оформлен", "сценограф", "инсталляц"),
    "фотограф": ("фотограф", "фотосъ", "фотосесс"),
    "видеограф": ("видеограф", "видеосъ", "видеоролик", "видеомонтаж"),
    "подарки и сувениры": ("подар", "сувенир", "мерч"),
    "инструменталист": ("инструмент", "скрип", "саксофон", "пиан"),
    "лайв-бэнд": ("лайв", "бэнд", "живая музык", "кавер"),
    "национальный ансамбль": ("ансамбл", "национальн", "домбр"),
    "танцевальный коллектив": ("танц", "хореограф"),
    "шоу-программа": ("шоу", "выступлен", "артист"),
    "фото и видеобудки": ("фотобуд", "видеобуд", "фотозеркал", "booth"),
    "банкетный зал": ("банкет", "зал", "вместим"),
    "загородная площадка": ("загород", "площадк", "террас", "природ"),
    "ресторан": ("ресторан", "кухн", "ужин"),
    "отель": ("отел", "гостиниц", "hotel"),
    "английский": ("английск", "english", "international", "международн"),
    "казахский": ("казахск", "қазақ"),
    "русский": ("русск",),
})


def _keywords(request: MatchRequest) -> tuple[str, ...]:
    # The same query terms as embeddings: format, category, city, optional language.
    terms = (request.event_format, request.category, request.city)
    if request.language is not None:
        terms += (request.language,)
    return tuple(dict.fromkeys(
        stem for term in terms for stem in _KEYWORDS.get(term.casefold(), (term.casefold(),))
    ))


def _hits(text: str, keywords: tuple[str, ...]) -> int:
    text = text.casefold()
    return sum(stem in text for stem in keywords)


class LexicalScorer:
    name: Literal["lexical"] = "lexical"

    def score(self, request: MatchRequest, contractors: list[Contractor]) -> dict[str, float]:
        keywords = _keywords(request)
        return {c.id: round(min(1.0, _hits(c.description, keywords) / 4), 3) for c in contractors}

    def snippet(self, request: MatchRequest, contractor: Contractor) -> str | None:
        keywords = _keywords(request)
        sentences = [part.strip() for part in re.split(r"[.!?\n]", contractor.description) if part.strip()]
        best = max(sentences, key=lambda text: _hits(text, keywords), default="")
        return best[:200] if _hits(best, keywords) else None
