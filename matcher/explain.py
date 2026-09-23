"""Deterministic explanations of already-selected contractor facts."""
import json
import logging
import os
import re
from hashlib import sha256
from pathlib import Path
from itertools import combinations

from matcher.config import create_client
from matcher.model import CardFacts, Explanation, MatchResult, Reason, ReasonFamily
from matcher.textfmt import format_date, money

BANNED_PHRASES = (
    "отличный выбор", "идеально подойдёт", "идеально подходит",
    "прекрасный вариант", "лучший выбор", "не пожалеете",
    "профессионал своего дела", "высокое качество", "индивидуальный подход",
)
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
NUMBER_KEYS = {"price", "budget", "headroom_pct", "next_price", "diff_pct", "requested_hours",
               "max_hours", "hours", "date", "busy_count"}

PHRASES = {
    "BUDGET_HEADROOM": (
        "Цена от {price} при бюджете {budget} оставляет запас {headroom_pct} %",
        "Стартовая цена {price} ниже бюджета {budget} на {headroom_pct} %",
        "Из бюджета {budget} остаётся {headroom_pct} % при цене от {price}",
    ),
    "BUDGET_LOWER_THAN_SHOWN": (
        "Самая низкая цена «от» среди показанных: {price}, на {diff_pct} % ниже следующей — {next_price}",
        "Среди показанных дешевле всего стартовая цена {price}: следующая — {next_price}, разница {diff_pct} %",
        "По цене «от» {price} доступнее соседей: следующий вариант — от {next_price}, экономия относительно него {diff_pct} %",
    ),
    "BUDGET_FITS": (
        "Цена от {price} укладывается в указанный вами бюджет {budget}",
        "При бюджете {budget} проходит по стартовой цене {price}; итоговую сумму нужно уточнить",
        "Бюджет {budget} позволяет рассмотреть этот вариант со стоимостью от {price}",
    ),
    "FORMAT_SUPPORTED": (
        "Принимает заказы на указанный вами формат мероприятия — {format}",
        "В анкете среди форматов работы указан нужный вам: {format}",
        "Формат из вашего запроса — {format} — входит в перечень услуг подрядчика",
    ),
    "LANGUAGE_REQUEST_MATCH": (
        "Работает на языке {language}, который вы указали в запросе на подбор",
        "Указанный в запросе язык — {language} — есть среди рабочих языков",
        "По языку совпадает с запросом: {language} указан в анкете подрядчика",
    ),
    "LANGUAGE_UNIQUE_IN_SHOWN": (
        "Единственный из показанных работает на языке {language}; у соседей он не заявлен",
        "Язык {language} заявлен только в этой анкете среди показанных вариантов",
        "Среди показанных только этот подрядчик указал рабочий язык {language}",
    ),
    "LANGUAGE_OPTIONS": (
        "В анкете указаны рабочие языки: {languages}; язык проведения можно выбрать",
        "Для проведения доступны языки {languages} — можно выбрать язык мероприятия",
        "По языку проведения есть выбор: {languages}, все они заявлены в профиле",
    ),
    "DURATION_HEADROOM": (
        "Лимит {max_hours} ч закрывает запрошенные {requested_hours} ч с запасом по времени на площадке",
        "На запрошенные {requested_hours} ч можно пригласить с запасом: лимит работы — {max_hours} ч",
        "По длительности остаётся резерв: нужно {requested_hours} ч, а в анкете доступно до {max_hours} ч",
    ),
    "DURATION_MAX_IN_SHOWN": (
        "Самый большой лимит работы среди показанных — до {max_hours} ч на площадке",
        "Среди этих вариантов дольше всех может работать на площадке: до {max_hours} ч",
        "По длительности опережает соседей: заявлен наибольший лимит — {max_hours} ч",
    ),
    "DURATION_NOT_APPLICABLE": (
        "Работа не привязана к присутствию на площадке, поэтому лимит часов здесь не применяется",
        "Для этой услуги работа не привязана к присутствию на площадке; почасовой лимит не применяется",
        "В этом случае работа не привязана к присутствию на площадке, часы участия не ограничивают подбор",
    ),
    "DESCRIPTION_ASPECT": (
        "С запросом перекликается фрагмент описания: «{quote}»",
        "В описании есть деталь, относящаяся к вашему запросу: «{quote}»",
        "К запросу относится фрагмент из анкеты: «{quote}»",
    ),
    "DESCRIPTION_CLOSEST_IN_SHOWN": (
        "Описание ближе всего к запросу среди показанных: «{quote}»",
        "Среди этих анкет описание точнее всего соответствует запросу: «{quote}»",
        "По близости описания к запросу опережает соседей: «{quote}»",
    ),
    "AVAILABILITY_REPLACEMENT": (
        "Попал в тройку потому, что {competitor} занят {date}: без этой брони его место было бы ниже",
        "Занятость {competitor} на {date} освободила место в тройке: без этой брони кандидат остался бы за её пределами",
        "Вошёл в подборку из-за брони у {competitor} на {date}; иначе в тройку не попал бы",
    ),
    "AVAILABILITY_ONLY_FREE": (
        "На {date} это единственный подходящий вариант; занятых на эту дату в категории — {busy_count}",
        "Только этот кандидат прошёл условия подбора на {date}; из остальных заняты — {busy_count}",
        "На дату {date} остался один подходящий профиль; число занятых в категории — {busy_count}",
    ),
    "PRICE_IMPUTED": (
        "цена проставлена при подготовке датасета, уточняйте",
        "учтите: цена проставлена при подготовке датасета, уточняйте",
        "по данным анкеты, цена проставлена при подготовке датасета, уточняйте",
    ),
    "CITY_IMPUTED": (
        "город проставлен при подготовке датасета",
        "в анкете город проставлен при подготовке датасета",
        "учтите: город проставлен при подготовке датасета",
    ),
    "SYNTHETIC": (
        "синтетический профиль",
        "это синтетический профиль",
        "в подборке синтетический профиль",
    ),
}


def _primary(facts: CardFacts) -> Reason | None:
    return next((reason for reason in facts.reasons if reason.primary), next(iter(facts.reasons), None))


def _safe_quote(quote: str) -> str:
    """Choose a contiguous fragment; never rewrite words or append an ellipsis."""
    fragments = re.split(r"[.!?\n]|" + "|".join(map(re.escape, BANNED_PHRASES)), quote, flags=re.IGNORECASE)
    candidates = []
    for fragment in fragments:
        fragment = fragment.strip(" ,;:—-\t")
        if len(fragment) > 120:
            fragment = fragment[:120].rsplit(" ", 1)[0].rstrip(",;:—-")
        if len(_words(fragment)) >= 2:
            candidates.append(fragment)
    return max(candidates, key=len, default="")


def _reason_text(facts: CardFacts) -> str:
    primary = _primary(facts)
    def phrase(reason: Reason) -> str:
        evidence = dict(reason.evidence)
        if "quote" in evidence:
            evidence["quote"] = _safe_quote(evidence["quote"])
        # A quote consisting entirely of banned praise cannot be repeated.
        return PHRASES[reason.code][facts.rank - 1].format_map(evidence).removesuffix(": «»")

    text = phrase(primary)
    caveats = [phrase(r) for r in facts.reasons if r.family == ReasonFamily.DATA_QUALITY and r is not primary]
    support = next((r for r in facts.reasons if r is not primary
                    and r.family not in {primary.family, ReasonFamily.DATA_QUALITY}), None)
    details = ([phrase(support)] if support else []) + caveats
    if len(text + ". " + "; ".join(details) + ".") > 350:
        details = caveats
    body = "; ".join(details)
    return text + (". " + body[:1].upper() + body[1:] if body else "") + "."


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _numbers(text: str) -> set[int]:
    text = re.sub(r"\d{1,3}(?:[ \u2009\u202f\xa0]\d{3})+(?!\d)",
                  lambda match: re.sub(r"\s", "", match[0]), text)
    return {int(number) for number in re.findall(r"\d+", text)}


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def _grounding(facts: CardFacts, text: str) -> tuple[int, set[int]]:
    contractor, day = facts.contractor, facts.free_on_date
    values = {contractor.price_from_kzt, facts.budget_kzt, facts.budget_headroom_pct,
              facts.requested_hours, contractor.max_hours} - {None}
    numbers = _numbers(text)
    allowed = values | {day.day, day.month, day.year} | _numbers(facts.semantic_snippet or "") | _numbers(contractor.name)
    hits = len(numbers & values)
    folded = text.casefold()
    hits += bool(re.search(rf"\b{re.escape(format_date(day))}\b|\b0?{day.day} {MONTHS[day.month - 1]}\b", folded))
    hits += bool(re.search(rf"\b{re.escape(facts.format_matched)}\b", folded))
    hits += any(re.search(rf"\b{re.escape(language.removesuffix('ий'))}\w*\b", folded) for language in facts.languages_matched)
    snippet_words, text_words = _words(facts.semantic_snippet or ""), _words(text)
    trigrams = {tuple(text_words[i:i + 3]) for i in range(len(text_words) - 2)}
    hits += any(tuple(snippet_words[i:i + 3]) in trigrams for i in range(len(snippet_words) - 2))
    for reason in facts.reasons:
        for value in reason.evidence.values():
            evidence_numbers = _numbers(value)
            allowed |= evidence_numbers
            hits += len(numbers & evidence_numbers) if evidence_numbers else bool(value and _normalise(value) in _normalise(text))
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
        minimum = 60 if facts.reasons else 40
        if not minimum <= len(text) <= 350:
            problems.append(f"{prefix}: text must be {minimum}..350 characters")
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
            required = set().union(*(_numbers(value) for key, value in primary.evidence.items() if key in NUMBER_KEYS))
            missing = required - _numbers(text)
            if missing:
                problems.append(f"{prefix}: missing primary numbers {sorted(missing)}")
            if primary.code == "AVAILABILITY_REPLACEMENT" and _normalise(primary.evidence["competitor"]) not in _normalise(text):
                problems.append(f"{prefix}: missing primary competitor")
            for quote in re.findall(r"«([^»]+)»", text):
                if len(_words(quote)) >= 4 and _normalise(quote) not in _normalise(facts.contractor.description):
                    problems.append(f"{prefix}: quote is not in the description")
    for left, right in combinations((set(_words(t)) for t in texts if isinstance(t, str)), 2):
        if left | right and len(left & right) / len(left | right) >= 0.6:
            problems.append("Explanations are too similar (Jaccard >= 0.6)")
    return problems


class TemplateExplainer:
    def explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        explanations = []
        for facts in result.cards:
            if facts.reasons:
                explanations.append(Explanation(facts.contractor.id, _reason_text(facts), "template"))
                continue
            contractor, index = facts.contractor, facts.rank - 1
            price, budget = money(contractor.price_from_kzt), money(facts.budget_kzt)
            reserve, day, form = facts.budget_headroom_pct, format_date(facts.free_on_date), facts.format_matched
            fit = (
                f"цена от {price} при бюджете {budget} — запас {reserve} %" if reserve else f"цена от {price} — ровно в бюджет {budget}",
                f"стартовая цена — {price}, это на {reserve} % ниже бюджета {budget}" if reserve else f"стартовая цена — {price}, ровно в бюджет {budget}",
                f"лимит {budget} оставляет {reserve} % резерва" if reserve else f"лимит {budget} выбран полностью, запаса нет",
            )[index]
            text = (
                f"Первый в выдаче: {contractor.name}, {fit}; формат — {form}, свободен {day}",
                f"На втором месте {contractor.name}: {form}, свободен {day}; {fit}",
                f"Замыкает тройку {contractor.name}: {form} со ставкой от {price}; {fit}, свободен {day}",
            )[index]
            details = []
            for flag, wording in (
                ("price_imputed", "цена проставлена при подготовке датасета, уточняйте"),
                ("city_imputed", "город проставлен при подготовке датасета"),
                ("synthetic", "синтетический профиль"),
            ):
                if flag in facts.caveats or getattr(contractor, flag):
                    details.append(wording)
            if contractor.max_hours is None:
                details.append("работа не привязана к присутствию на площадке")
            lead = ("Важные условия: ", "Обратите внимание: ", "По данным анкеты: ")[index] if details else ""
            options = []
            if facts.semantic_snippet:
                for sentence in re.split(r"[.!?\n]", facts.semantic_snippet):
                    quote = sentence.strip()
                    if len(quote) > 90:
                        quote = quote[:90].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
                    if len(_words(quote)) >= 3 and not any(p in quote.casefold() for p in BANNED_PHRASES):
                        rarity = sum(c.semantic_snippet == facts.semantic_snippet for c in result.cards)
                        options.append((rarity, ("из описания", "в профиле", "особенность")[index] + f": «{quote}»"))
                        break
            if facts.requested_language:
                rarity = sum(c.requested_language == facts.requested_language for c in result.cards)
                options.append((rarity, ("язык — ", "заявлен ", "доступен ")[index] + facts.requested_language))
            if facts.requested_hours and contractor.max_hours is not None:
                hours, maximum = facts.requested_hours, contractor.max_hours
                rarity = sum(c.contractor.max_hours == maximum for c in result.cards)
                options.append((rarity, (
                    f"до {maximum} ч при запрошенных {hours} ч",
                    f"продолжительность: {hours} ч из доступных {maximum} ч",
                    f"на {hours} ч можно пригласить при лимите {maximum} ч",
                )[index]))
            for _, detail in sorted(options, key=lambda option: option[0]):
                if len(text + ". " + lead + "; ".join(details + [detail]) + ".") <= 350:
                    details.append(detail)
            body = lead + "; ".join(details)
            text += (". " + body[:1].upper() + body[1:] if body else "") + "."
            explanations.append(Explanation(contractor.id, text, "template"))
        return tuple(explanations)


SYSTEM_PROMPT = (
    "Ты помощник площадки подрядчиков для мероприятий. Кандидаты уже отобраны и упорядочены кодом; "
    "ты только объясняешь заказчику, почему каждая карточка здесь. "
    "Пиши по-русски, живым языком: 1–2 предложения, 60–350 символов на карточку.\n"
    "1. Используй только причины из списка этой карточки и их факты. Не добавляй другие причины, "
    "свойства, оценки или сравнения, даже если они кажутся подходящими по запросу.\n"
    "2. Первое предложение обязательно выражает главную_причину со всеми её числами; "
    "при замене занятого кандидата укажи имя конкурента и дату его брони.\n"
    "3. Второе предложение может добавить ОДНУ поддерживающую причину из другой семьи или одну оговорку. "
    "Оговорки используй только с готовыми формулировками из поля «оговорки».\n"
    "4. Варьируй структуру между карточками, сохраняя главную причину в начале. "
    "Не пиши про баллы, не повторяй дату и формат без причины.\n"
    "5. Числа копируй как в фактах: «900 000 ₸», «04.10.2026», «55 %». "
    "Без KZT, ISO-дат и английских слов, включая коды причин. Цена всегда «от», не итоговая.\n"
    "6. Без общих похвал и оценочных прилагательных. Цитату приводи дословно в «кавычках»: "
    "можно взять более короткий фрагмент, но нельзя менять слова или добавлять многоточие внутри кавычек.\n"
    "Верни только JSON {\"explanations\":[{\"id\":\"...\",\"text\":\"...\"}]} в заданном порядке карточек. "
    "Запрещённые фразы: " + "; ".join(BANNED_PHRASES)
)

EVIDENCE_KEYS = NUMBER_KEYS | {"language", "languages", "quote", "competitor", "format"}


def build_prompt_payload(result: MatchResult) -> dict:
    """Expose only selected codes and display evidence, never scores or raw flags."""
    def encoded(reason: Reason) -> dict:
        return {"код": reason.code, "факты": {k: v for k, v in reason.evidence.items() if k in EVIDENCE_KEYS}}

    req = result.request
    cards = []
    for facts in result.cards:
        primary = _primary(facts)
        cards.append({
            "id": facts.contractor.id, "имя": facts.contractor.name, "позиция": facts.rank,
            "главная_причина": encoded(primary) if primary else None,
            "поддерживающие": [encoded(r) for r in facts.reasons
                               if r is not primary and r.family != ReasonFamily.DATA_QUALITY],
            "оговорки": [{"код": r.code, "формулировка": PHRASES[r.code][0]}
                         for r in facts.reasons if r.family == ReasonFamily.DATA_QUALITY],
        })
    return {
        "запрос": {"город": req.city, "дата": format_date(req.event_date), "формат": req.event_format,
                   "категория": req.category, "бюджет": money(req.budget_kzt),
                   "длительность": f"{req.duration_hours} ч" if req.duration_hours is not None else None,
                   "язык": req.language},
        "карточки": cards,
    }


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


_log = logging.getLogger(__name__)
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "explanations_cache.json"
PROMPT_VERSION = sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


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
        return sha256(_json([build_prompt_payload(result), self.model, PROMPT_VERSION]).encode()).hexdigest()

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
