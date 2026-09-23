"""Offline-tagged description aspects (stream B, decision 3).

`data/aspects.json` is produced once by scripts/tag_aspects.py with an LLM and
committed. Each tag carries a verbatim quote used ONLY as proof
(`quote in description`); card texts never quote descriptions, they paraphrase
the tag label. Schema:

{
  "model": "gpt-5.4-mini",
  "version": 1,
  "aspects": {
    "HK-44733": [{"tag": "business_forum", "polarity": "positive", "quote": "..."}],
    ...
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ASPECTS_PATH = Path(__file__).resolve().parents[1] / "data" / "aspects.json"

# Closed enum: tag -> (Russian label for texts, event formats it speaks to).
TAGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "business_forum": ("опыт бизнес-форумов и конференций", ("конференция", "корпоратив")),
    "team_building": ("формат тимбилдинга и командных активностей", ("корпоратив",)),
    "wedding_ceremony": ("опыт свадебных церемоний", ("свадьба",)),
    "toi_traditions": ("знание традиций той и казахских обрядов", ("той", "свадьба", "юбилей")),
    "kids_program": ("детская программа и семейные праздники", ("день рождения",)),
    "improvisation": ("импровизация и живое общение с залом", ("корпоратив", "юбилей", "день рождения")),
    "custom_script": ("сценарий под заказчика", ("корпоратив", "свадьба", "юбилей")),
    "large_scale_events": ("опыт крупных мероприятий на сотни гостей", ("корпоратив", "конференция", "свадьба")),
    "documentary_photo": ("репортажная съёмка без постановки", ("свадьба", "корпоратив", "той")),
    "posing_guidance": ("помощь с позированием", ("свадьба", "день рождения")),
    "fast_delivery": ("быстрая отдача материала", ("свадьба", "корпоратив", "конференция")),
    "live_instruments": ("живые инструменты", ("свадьба", "корпоратив", "юбилей")),
    "kazakh_repertoire": ("казахский репертуар", ("той", "свадьба", "юбилей")),
    "international_repertoire": ("мировые хиты и разножанровый репертуар", ("корпоратив", "свадьба")),
    "turnkey_decor": ("оформление под ключ", ("свадьба", "юбилей", "корпоратив")),
    "branded_merch": ("брендированная продукция и мерч", ("корпоратив", "конференция")),
    "instant_print": ("мгновенная печать фото", ("корпоратив", "свадьба", "день рождения")),
    "presentation_equipment": ("оборудование для презентаций и сцены", ("конференция", "корпоратив")),
    "panoramic_view": ("панорамный вид и зона для фото", ("свадьба", "юбилей")),
    "own_kitchen": ("своя кухня и банкетное меню", ("свадьба", "той", "юбилей", "корпоратив")),
    "outdoor_area": ("открытая площадка или терраса", ("свадьба", "корпоратив")),
    "accommodation": ("размещение гостей на месте", ("свадьба", "той", "конференция")),
    "multilingual_hosting": ("ведение на нескольких языках", ("корпоратив", "конференция", "свадьба")),
    "premium_clients": ("работа с крупными брендами и премиум-сегментом", ("корпоратив", "конференция")),
}

POLARITIES = ("positive", "neutral", "negative")


@dataclass(frozen=True)
class Aspect:
    tag: str
    polarity: str
    quote: str

    @property
    def label(self) -> str:
        return TAGS[self.tag][0]

    def relevant_to(self, event_format: str) -> bool:
        return event_format in TAGS[self.tag][1]


@lru_cache(maxsize=4)
def load_aspects(path: Path | str = ASPECTS_PATH) -> dict[str, tuple[Aspect, ...]]:
    """contractor id -> aspects. Missing file => {} (structural reasons only).
    Unknown tags / polarities are dropped; quotes are kept as-is (proof only)."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, tuple[Aspect, ...]] = {}
    for cid, items in sorted(raw.get("aspects", {}).items()):
        kept = [Aspect(i["tag"], i.get("polarity", "positive"), i.get("quote", ""))
                for i in items if i.get("tag") in TAGS and i.get("polarity", "positive") in POLARITIES]
        if kept:
            out[cid] = tuple(kept)
    return out
