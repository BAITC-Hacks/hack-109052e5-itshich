"""Deterministic explanations of already-selected contractor facts."""
import json
import logging
import os
import re
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from itertools import permutations

from matcher import aspects
from matcher.config import create_client
from matcher.model import CardFacts, Explanation, MatchResult, Reason, ReasonFamily, RejectReason
from matcher.textfmt import format_date, money

BANNED_PHRASES = (
    "отличный выбор", "идеально подойдёт", "идеально подходит",
    "прекрасный вариант", "лучший выбор", "не пожалеете",
    "профессионал своего дела", "высокое качество", "индивидуальный подход",
)
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
NUMBER_KEYS = {"price", "budget", "headroom_pct", "next_price", "diff_pct", "requested_hours",
               "max_hours", "date", "scarcity"}

PHRASES = {
    "BUDGET_HEADROOM": (
        "Цена от {price}, бюджет {budget}: запас {headroom_pct} %",
        "При цене от {price} остаётся {headroom_pct} % бюджета {budget}",
        "Из бюджета {budget} остаётся {headroom_pct} % при цене от {price}",
    ),
    "BUDGET_LOWER_THAN_SHOWN": (
        "Цена от {price} ниже остальных: следующая — {next_price}, разница {diff_pct} %",
        "Среди показанных дешевле: цена от {price} против {next_price}, разница {diff_pct} %",
        "Цена от {price} на {diff_pct} % ниже следующей — {next_price}",
    ),
    "BUDGET_FITS": (
        "Цена от {price} укладывается в бюджет {budget}",
        "Бюджет {budget} покрывает стартовую цену от {price}",
        "При бюджете {budget} подходит цена от {price}",
    ),
    "FORMAT_SUPPORTED": (
        "Принимает заказы на формат {format}",
        "В анкете указан нужный формат: {format}",
        "Поддерживает формат из запроса — {format}",
    ),
    "LANGUAGE_REQUEST_MATCH": (
        "Работает на запрошенном языке: {language}",
        "Нужный язык — {language} — заявлен в анкете",
        "По языку совпадает с запросом: {language}",
    ),
    "LANGUAGE_UNIQUE_IN_SHOWN": (
        "Среди показанных только здесь заявлен язык {language}",
        "Язык {language} есть только в этой анкете среди показанных",
        "В отличие от соседей, заявлен рабочий язык {language}",
    ),
    "LANGUAGE_OPTIONS": (
        "Есть выбор языка проведения: {languages}",
        "Доступны рабочие языки: {languages}",
        "В анкете указаны языки {languages}",
    ),
    "DURATION_HEADROOM": (
        "Лимит {max_hours} ч закрывает запрошенные {requested_hours} ч с запасом",
        "На запрошенные {requested_hours} ч есть запас: доступно до {max_hours} ч",
        "По времени есть резерв: нужно {requested_hours} ч, лимит — {max_hours} ч",
    ),
    "DURATION_MAX_IN_SHOWN": (
        "Самый большой лимит среди показанных — до {max_hours} ч",
        "Среди этих вариантов дольше всех может работать: до {max_hours} ч",
        "Опережает соседей по длительности: лимит — {max_hours} ч",
    ),
    "DURATION_NOT_APPLICABLE": (
        "Работа не привязана к присутствию на площадке",
        "Для этой услуги работа не привязана к присутствию на площадке",
        "Здесь работа не привязана к присутствию на площадке",
    ),
    "DESCRIPTION_ASPECT": (
        "К формату из запроса относится {aspect}",
        "Для вашего формата: {aspect}",
        "С запросом совпадает {aspect}",
    ),
    "DESCRIPTION_CLOSEST_IN_SHOWN": (
        "Описание ближе всего к запросу среди показанных",
        "Среди этих анкет описание наиболее близко к запросу",
        "По близости описания к запросу опережает соседей",
    ),
    "AVAILABILITY_REPLACEMENT": (
        "В тройке, потому что более привлекательный вариант на эту дату занят ({competitor})",
        "Более привлекательный вариант ({competitor}) на эту дату занят, поэтому здесь {name}",
        "Поднялся в тройку: {competitor} на эту дату занят",
    ),
    "AVAILABILITY_ONLY_FREE": (
        "Единственный подходящий вариант на {date}",
        "Только этот кандидат прошёл все условия на {date}",
        "На {date} остался один подходящий профиль",
    ),
    "AVAILABILITY_SCARCE": (
        "В категории {scarcity}",
        "По доступности: {scarcity}",
        "Выбор на дату ограничен каталогом: {scarcity}",
    ),
    "PRICE_IMPUTED": ("цена проставлена при подготовке датасета, уточняйте",) * 3,
    "CITY_IMPUTED": ("город проставлен при подготовке датасета",) * 3,
    "SYNTHETIC": ("синтетический профиль",) * 3,
}


def _primary(facts: CardFacts) -> Reason | None:
    return next((reason for reason in facts.reasons if reason.primary), next(iter(facts.reasons), None))


def _phrase(reason: Reason, facts: CardFacts, variant: int | None = None) -> str:
    evidence = {**reason.evidence, "name": facts.contractor.name}
    text = PHRASES[reason.code][facts.rank - 1 if variant is None else variant].format_map(evidence)
    if reason.code == "DESCRIPTION_CLOSEST_IN_SHOWN" and "aspect" in evidence:
        text += ": " + evidence["aspect"]
    if reason.family == ReasonFamily.BUDGET and facts.contractor.price_from_kzt * 100 > facts.budget_kzt * 85:
        text = "Бюджет впритык: " + text[:1].lower() + text[1:]
    if "scarcity" in evidence and reason.code != "AVAILABILITY_SCARCE" and (
            _says_scarcity(facts) or reason.code == "AVAILABILITY_ONLY_FREE"):
        text += "; " + evidence["scarcity"]
    return text


def _says_scarcity(facts: CardFacts) -> bool:
    """The date-scarcity note is a property of the whole result, so it is said
    once, on the first card, instead of being repeated on every card."""
    return facts.rank == 1


def _supporting(facts: CardFacts, primary: Reason | None) -> list[Reason]:
    return [r for r in facts.reasons if r is not primary and r.family != ReasonFamily.DATA_QUALITY
            and not (r.code == "AVAILABILITY_SCARCE" and not _says_scarcity(facts))]


def _reason_text(facts: CardFacts) -> str:
    primary = _primary(facts)
    text = _phrase(primary, facts)
    named = primary.code != "AVAILABILITY_REPLACEMENT" or facts.rank == 2
    if primary.code != "AVAILABILITY_REPLACEMENT":
        text = facts.contractor.name + ": " + text[:1].lower() + text[1:]
    caveats = [_phrase(r, facts) for r in facts.reasons if r.family == ReasonFamily.DATA_QUALITY]
    supports = [r for r in _supporting(facts, primary) if r.family != primary.family]
    # Fit complete phrases, never crop a fact or a name to the character limit.
    for support in [*supports[:1], None]:
        details = ([_phrase(support, facts)] if support else []) + caveats
        body = "; ".join(details)
        if not named:
            body = facts.contractor.name + (": " + body[:1].lower() + body[1:] if body else " подходит на эту дату")
        elif body:
            body = body[:1].upper() + body[1:]
        rendered = text + (". " + body if body else "") + "."
        if len(rendered) < 60:
            rendered = text + ". Формат из запроса — " + facts.format_matched + "."
        if len(rendered) <= 260:
            return rendered
    return rendered


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _numbers(text: str) -> set[str]:
    # Dates contain separate allowed day/month/year values; decimal hours or
    # prices are one value, so 5.8 cannot pass because both 5 and 8 are allowed.
    text = re.sub(r"\b(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})\b",
                  lambda m: re.sub(r"[.-]", " ", m[0]), text)
    text = re.sub(r"\d{1,3}(?:[ \u2009\u202f\xa0]\d{3})+(?!\d)",
                  lambda match: re.sub(r"\s", "", match[0]), text)
    values = set()
    for number in re.findall(r"-?\d+(?:[.,]\d+)?", text):
        whole, _, fraction = number.replace(",", ".").partition(".")
        fraction = fraction.rstrip("0")
        values.add(str(int(whole)) + ("." + fraction if fraction else ""))
    return values


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def _contains(text: str, value: str) -> bool:
    return bool(value and re.search(rf"(?<!\w){re.escape(_normalise(value))}(?!\w)", _normalise(text)))


def _allowed_numbers(facts: CardFacts) -> set[str]:
    c, day = facts.contractor, facts.free_on_date
    allowed = {c.price_from_kzt, facts.budget_kzt, facts.budget_headroom_pct,
               facts.requested_hours, c.max_hours, day.day, day.month, day.year} - {None}
    allowed = {str(n) for n in allowed}
    for reason in facts.reasons:
        for key, value in reason.evidence.items():
            if key in NUMBER_KEYS:
                allowed |= _numbers(value)
    return allowed


def _fact_tokens(facts: CardFacts) -> set[tuple[str, str]]:
    tokens = {("number", n) for n in _allowed_numbers(facts)}
    tokens.add(("name", _normalise(facts.contractor.name)))
    for reason in facts.reasons:
        for key in ("competitor", "aspect"):
            if value := reason.evidence.get(key):
                tokens.add(("name" if key == "competitor" else "aspect", _normalise(value)))
    for aspect in aspects.load_aspects().get(facts.contractor.id, ()):
        if aspect.polarity == "positive" and aspect.relevant_to(facts.format_matched):
            tokens.add(("aspect", _normalise(aspect.label)))
    return tokens


def _grounding(facts: CardFacts, text: str) -> tuple[int, set[str]]:
    numbers, day = _numbers(text), facts.free_on_date
    allowed = _allowed_numbers(facts)
    hits = len(numbers & allowed)
    hits += _contains(text, facts.contractor.name)
    hits += _contains(text, facts.format_matched)
    hits += any(re.search(rf"\b{re.escape(language.removesuffix('ий'))}\w*\b", text.casefold())
                for language in facts.languages_matched)
    hits += _contains(text, f"{day.day} {MONTHS[day.month - 1]}")
    hits += sum(_contains(text, value) for kind, value in _fact_tokens(facts) if kind == "aspect")
    hits += any(_contains(text, r.evidence.get("competitor", "")) for r in facts.reasons)
    return hits, numbers - allowed


def validate_explanations(result: MatchResult, texts: list[str] | tuple[str, ...]) -> list[str]:
    problems = []
    if len(texts) != len(result.cards):
        problems.append("Explanation count does not match cards")
    for facts, text in zip(result.cards, texts):
        prefix = facts.contractor.id
        if not isinstance(text, str):
            problems.append(f"{prefix}: text must be a string")
            continue
        minimum = 60
        if not minimum <= len(text) <= 260:
            problems.append(f"{prefix}: text must be {minimum}..260 characters")
        # Date punctuation is not a sentence boundary.
        prose = re.sub(r"\b\d{2}\.\d{2}\.\d{4}\b", "date", text)
        sentences = [part for part in re.split(r"[.!?]+", prose) if _words(part)]
        if not 1 <= len(sentences) <= 2:
            problems.append(f"{prefix}: text must have 1..2 sentences")
        if any(phrase in text.casefold() for phrase in BANNED_PHRASES):
            problems.append(f"{prefix}: banned phrase")
        hits, unknown = _grounding(facts, text)
        if hits < 2:
            problems.append(f"{prefix}: fewer than two grounded facts")
        if unknown:
            problems.append(f"{prefix}: ungrounded numbers {sorted(unknown)}")
        primary = _primary(facts)
        if primary:
            required = set().union(*(
                _numbers(value) for key, value in primary.evidence.items()
                if key in NUMBER_KEYS and not (primary.code == "AVAILABILITY_REPLACEMENT" and key == "date")
                and not (key == "scarcity" and not _says_scarcity(facts))))
            missing = required - _numbers(text)
            if missing:
                problems.append(f"{prefix}: missing primary numbers {sorted(missing)}")
            if (primary.family == ReasonFamily.BUDGET and facts.contractor.price_from_kzt * 100 > facts.budget_kzt * 85
                    and "впритык" not in text.casefold()):
                problems.append(f"{prefix}: tight budget must say впритык")
            if (facts.contractor.price_from_kzt * 100 <= facts.budget_kzt * 85
                    and "впритык" in text.casefold()):
                problems.append(f"{prefix}: впритык is only for headroom below 15 %")
            if primary.code == "AVAILABILITY_REPLACEMENT" and not _contains(text, primary.evidence["competitor"]):
                problems.append(f"{prefix}: missing primary competitor")
        for quote in re.findall(r"«([^»]+)»", text):
            if len(_words(quote)) >= 4 and _normalise(quote.rstrip("…")) in _normalise(facts.contractor.description):
                problems.append(f"{prefix}: description quote is forbidden")
    primaries = [_primary(c) for c in result.cards]
    codes = [r.code for r in primaries if r]
    if len(set(codes)) != len(codes) and not result.diversity_limited:
        problems.append("Repeated primary codes require diversity_limited")
    allowed = [_fact_tokens(c) for c in result.cards]
    vocabulary = set().union(*allowed)
    for i, j in permutations(range(min(len(texts), len(result.cards))), 2):
        if not isinstance(texts[i], str) or (primaries[i] and primaries[j] and primaries[i].code == primaries[j].code):
            continue
        mentioned = {("number", n) for n in _numbers(texts[i])}
        mentioned |= {(kind, value) for kind, value in vocabulary if kind != "number" and _contains(texts[i], value)}
        if not mentioned - allowed[j]:
            problems.append(f"{result.cards[i].contractor.id}: swap-test failed against {result.cards[j].contractor.id}")
    return problems


class TemplateExplainer:
    def explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        explanations = []
        for facts in result.cards:
            if not facts.reasons:
                # Compatibility for callers of ranking.rank() without assign().
                c = facts.contractor
                reasons = [Reason("BUDGET_FITS", ReasonFamily.BUDGET,
                                  {"price": money(c.price_from_kzt), "budget": money(facts.budget_kzt)}, primary=True)]
                if c.max_hours is None:
                    reasons.append(Reason("DURATION_NOT_APPLICABLE", ReasonFamily.DURATION, {}))
                elif facts.requested_language:
                    reasons.append(Reason("LANGUAGE_REQUEST_MATCH", ReasonFamily.LANGUAGE, {"language": facts.requested_language}))
                elif facts.requested_hours is not None:
                    reasons.append(Reason("DURATION_HEADROOM", ReasonFamily.DURATION,
                                          {"requested_hours": str(facts.requested_hours), "max_hours": str(c.max_hours)}))
                else:
                    reasons.append(Reason("FORMAT_SUPPORTED", ReasonFamily.FORMAT, {"format": facts.format_matched}))
                reasons.extend(Reason(flag.upper(), ReasonFamily.DATA_QUALITY, {})
                               for flag in ("price_imputed", "city_imputed", "synthetic")
                               if flag in facts.caveats or getattr(c, flag))
                facts = replace(facts, reasons=tuple(reasons))
            explanations.append(Explanation(facts.contractor.id, _reason_text(facts), "template"))
        return tuple(explanations)


SYSTEM_PROMPT = (
    "Ты консультант площадки подрядчиков. Код уже выбрал карточки, порядок и причины; ты только формулируешь их. "
    "Русский язык, 1–2 предложения и 60–260 символов на карточку.\n"
    "1. Первое предложение раскрывает главную_причину: поле «смысл» задаёт точное значение, "
    "поле «факты» содержит разрешённые числа, имена и аспекты. Приведи её числа и имена. "
    "Слово «впритык» пиши ТОЛЬКО если в «смысле» сказано «Бюджет впритык» (запас меньше 15 %); "
    "при запасе 15 % и больше это слово запрещено.\n"
    "2. AVAILABILITY_REPLACEMENT объясни коротко и обязательно назови занятого конкурента. "
    "Дата брони не нужна. Пример: «В тройке, потому что более привлекательный вариант на эту дату занят (Кики).»\n"
    "3. Во втором предложении добавь одну поддерживающую причину или оговорку готовой формулировкой. "
    "Если есть scarcity, упомяни, сколько свободны из общего числа в категории.\n"
    "4. Аспекты передают особенности подрядчика. Не цитируй описание и не придумывай фактов; "
    "полного описания здесь нет. Не используй общие похвалы.\n"
    "5. Числа только из фактов карточки и запроса: цена от, бюджет, проценты, часы, части даты, "
    "число свободных и размер категории. Сводка отказов — лишь контекст, её числа не переносить в карточки. "
    "Сохраняй формат чисел; цена всегда «от», не обещай итоговую стоимость. Не упоминай баллы и коды.\n"
    "6. Каждый текст должен содержать имя этой карточки или число, имя конкурента либо аспект, "
    "которого нет у остальных карточек с другой главной причиной. Одной смены слов недостаточно. "
    "Меняй зачины, но не меняй главные причины и порядок карточек.\n"
    "Верни только JSON {\"explanations\":[{\"id\":\"...\",\"text\":\"...\"}]}. "
    "Запрещённые фразы: " + "; ".join(BANNED_PHRASES)
)

EVIDENCE_KEYS = NUMBER_KEYS | {"language", "languages", "aspect", "tag", "competitor", "format"}


def build_prompt_payload(result: MatchResult) -> dict:
    """Expose only selected codes and display evidence, never scores or raw flags."""
    def encoded(reason: Reason, card: CardFacts) -> dict:
        evidence = {k: v for k, v in reason.evidence.items() if k in EVIDENCE_KEYS}
        if not _says_scarcity(card):
            evidence.pop("scarcity", None)
        return {"код": reason.code, "смысл": _phrase(reason, card, 0), "факты": evidence}

    req = result.request
    cards = []
    for facts in result.cards:
        primary = _primary(facts)
        cards.append({
            "id": facts.contractor.id, "имя": facts.contractor.name, "позиция": facts.rank,
            "главная_причина": encoded(primary, facts) if primary else None,
            "поддерживающие": [encoded(r, facts) for r in _supporting(facts, primary)],
            "оговорки": [{"код": r.code, "формулировка": PHRASES[r.code][0]}
                         for r in facts.reasons if r.family == ReasonFamily.DATA_QUALITY],
        })
    return {
        "запрос": {"город": req.city, "дата": format_date(req.event_date), "формат": req.event_format,
                   "категория": req.category, "бюджет": money(req.budget_kzt),
                   "длительность": f"{req.duration_hours} ч" if req.duration_hours is not None else None,
                   "язык": req.language},
        "карточки": cards,
        "отказы": "; ".join(f"{reason.value}: {count}" for reason in RejectReason
                            if (count := sum(reason in r.reasons for r in result.rejections))) or "Нет отказов",
    }


def _json(value) -> str:
    def encode(item):
        return sorted(item) if isinstance(item, (set, frozenset)) else str(item)

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=encode)


_log = logging.getLogger(__name__)
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "explanations_cache.json"
PROMPT_VERSION = sha256(_json([SYSTEM_PROMPT, PHRASES, "b6-aspects-v1"]).encode()).hexdigest()[:12]


class LLMExplainer:
    last_fallback_reason: str | None = None
    def __init__(self, client=None, *, model: str | None = None, api_key: str | None = None,
                 cache_path: "Path | None" = None):
        self.client, self.api_key = client, api_key
        self.model = model or os.getenv("LLM_MODEL", "gpt-5.4-mini")
        self._cache: dict[str, tuple[Explanation, ...]] = {}
        # Durable cache: demo requests replay word-for-word across restarts and
        # even without a key. The payload includes ordered ids, primary codes
        # and evidence, so changes to selected reasons invalidate old texts.
        self.cache_path = CACHE_PATH if cache_path is None else cache_path
        self._load_cache()

    def _key(self, result: MatchResult) -> str:
        # Hash complete cards locally; descriptions/snippets never enter the prompt.
        return sha256(_json([build_prompt_payload(result), [asdict(c) for c in result.cards],
                            self.model, PROMPT_VERSION]).encode()).hexdigest()

    def _load_cache(self) -> None:
        try:
            raw = json.loads(Path(self.cache_path).read_text(encoding="utf-8")) if self.cache_path else {}
        except (OSError, ValueError):
            raw = {}
        for key, items in raw.items():
            if isinstance(items, list):
                self._cache[key] = tuple(Explanation(i["contractor_id"], i["text"], "llm") for i in items)

    def _persist(self, key: str, explanations: tuple[Explanation, ...]) -> None:
        if not self.cache_path:
            return
        try:
            path = Path(self.cache_path)
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            raw[key] = [{"contractor_id": e.contractor_id, "text": e.text} for e in explanations]
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        except (OSError, ValueError) as exc:
            _log.warning("Could not persist explanation cache: %s", exc)

    def explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        key = self._key(result)
        if key not in self._cache:
            explanations = self._explain(result) if result.cards else ()
            self._cache[key] = explanations  # in-process: same request => same text
            if explanations and all(e.source == "llm" for e in explanations):
                self._persist(key, explanations)  # only real LLM texts survive restarts
        return self._cache[key]

    def _explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        if any(not facts.reasons for facts in result.cards):
            return TemplateExplainer().explain(result)
        payload = build_prompt_payload(result)
        try:
            if self.client is None:
                self.client = create_client(self.api_key)
            if self.client is None:
                raise RuntimeError("no OPENAI_API_KEY and no cached explanation")
            response = self.client.chat.completions.create(
                model=self.model, temperature=0, seed=42, timeout=8,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": _json(payload)}],
            )
            entries = json.loads(response.choices[0].message.content)["explanations"]
            if not isinstance(entries, list) or [entry["id"] for entry in entries] != [c.contractor.id for c in result.cards]:
                raise ValueError("Explanation ids must match card order")
            if validate_explanations(result, [entry["text"] for entry in entries]):
                raise ValueError("Explanations failed grounding validation")
            return tuple(Explanation(entry["id"], entry["text"], "llm") for entry in entries)
        except Exception as exc:
            # Failure of any card rejects the whole response, preserving one source.
            self.last_fallback_reason = f"{type(exc).__name__}: {exc}"[:300]
            _log.warning("LLM explanation fell back to template: %s", self.last_fallback_reason)
            return TemplateExplainer().explain(result)


def get_explainer() -> LLMExplainer | TemplateExplainer:
    """LLM when a key exists or cached LLM texts can be replayed; else template."""
    if os.getenv("OPENAI_API_KEY") or CACHE_PATH.exists():
        return LLMExplainer()
    return TemplateExplainer()
