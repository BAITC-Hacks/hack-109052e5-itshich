"""Deterministic explanations of already-selected contractor facts."""
import json
import os
import re
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from itertools import combinations

from matcher.config import create_client
from matcher.model import CardFacts, Explanation, MatchResult
from matcher.textfmt import format_date, money

BANNED_PHRASES = (
    "отличный выбор", "идеально подойдёт", "идеально подходит",
    "прекрасный вариант", "лучший выбор", "не пожалеете",
    "профессионал своего дела", "высокое качество", "индивидуальный подход",
)
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _numbers(text: str) -> set[int]:
    text = re.sub(r"\d{1,3}(?:[ \u2009\u202f\xa0]\d{3})+(?!\d)",
                  lambda match: re.sub(r"\s", "", match[0]), text)
    return {int(number) for number in re.findall(r"\d+", text)}


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
        if not 40 <= len(text) <= 350:
            problems.append(f"{prefix}: text must be 40..350 characters")
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
    for left, right in combinations((set(_words(t)) for t in texts if isinstance(t, str)), 2):
        if left | right and len(left & right) / len(left | right) >= 0.6:
            problems.append("Explanations are too similar (Jaccard >= 0.6)")
    return problems


class TemplateExplainer:
    def explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        explanations = []
        for facts in result.cards:
            contractor, index = facts.contractor, facts.rank - 1
            price, budget = money(contractor.price_from_kzt), money(facts.budget_kzt)
            reserve, day, form = facts.budget_headroom_pct, format_date(facts.free_on_date), facts.format_matched
            text = (
                f"Первый в выдаче: {contractor.name}, цена от {price} при бюджете {budget} — запас {reserve} %; формат — {form}, свободен {day}",
                f"На втором месте {contractor.name}: {form}, свободен {day}; стартовая цена — {price}, это на {reserve} % ниже бюджета {budget}",
                f"Замыкает тройку {contractor.name}: {form} со ставкой от {price}; лимит {budget} оставляет {reserve} % резерва, свободен {day}",
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
                        quote = quote[:90].rsplit(" ", 1)[0].rstrip(",;:")
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
    'Ты получаешь уже отобранные карточки подрядчиков и проверенные факты. '
    'Напиши по-русски 1–2 предложения (40–350 символов) для каждой карточки. '
    'Приводи конкретные числа: цену от, бюджет, запас, часы, дату. '
    'Объясни, что отличает именно эту карточку от других показанных. '
    'Не используй общие похвалы; не выдумывай ничего, чего нет в фактах. '
    'Каждое число должно быть из фактов этой карточки, укажи минимум два факта. '
    'Не повторяй один текст для разных карточек. Не меняй порядок. '
    'Верни только JSON {"explanations":[{"id":"...","text":"..."}]} '
    'в заданном порядке. Запрещённые фразы: ' + '; '.join(BANNED_PHRASES)
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class LLMExplainer:
    def __init__(self, client=None, *, model: str | None = None, api_key: str | None = None):
        self.client, self.api_key = client, api_key
        self.model = model or os.getenv("LLM_MODEL", "gpt-5-mini")
        self._cache: dict[str, tuple[Explanation, ...]] = {}

    def explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        key = sha256(_json([asdict(result.request), [c.contractor.id for c in result.cards]]).encode()).hexdigest()
        if key not in self._cache:
            self._cache[key] = self._explain(result) if result.cards else ()
        return self._cache[key]

    def _explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        cards = []
        for facts in result.cards:
            card = asdict(facts)
            card["contractor"].pop("description")
            card["contractor"].pop("busy_dates")
            cards.append(card)
        payload = {"request": asdict(result.request), "cards": cards,
                   "rejection_summary": dict(Counter(reason.value for rejection in result.rejections for reason in rejection.reasons))}
        try:
            if self.client is None:
                self.client = create_client(self.api_key)
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
        except Exception:
            # Failure of any card rejects the whole response, preserving one source.
            return TemplateExplainer().explain(result)


def get_explainer() -> LLMExplainer | TemplateExplainer:
    return LLMExplainer() if os.getenv("OPENAI_API_KEY") else TemplateExplainer()
