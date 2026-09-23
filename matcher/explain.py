"""Deterministic explanations of already-selected contractor facts."""
import json
import logging
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
    "Ты помощник площадки event-подрядчиков. Кандидаты уже отобраны и упорядочены кодом; "
    "ты только объясняешь заказчику, почему каждая карточка здесь и чем она отличается от соседних. "
    "Для каждой карточки напиши по-русски 1–2 предложения (60–300 символов), живым языком, как консультант заказчику.\n"
    "Правила:\n"
    "1. Только факты карточки и её блок «чем отличается». Ничего не добавляй и не обобщай.\n"
    "2. Обязательно: цена от и запас по бюджету (если запас 0 %, скажи «ровно в бюджет»). Плюс минимум один факт: часы, язык, цитата, дата, оговорка.\n"
    "3. Начинай с самого сильного отличия этой карточки, а не с цены. Не пиши «отличается тем, что» и «лучший суммарный балл»; "
    "вместо балла говори, за счёт чего он: запас по бюджету, близость описания к запросу, лимит часов.\n"
    "4. Разная структура у разных карточек: одну начни с цитаты, другую с часов или языка, третью с цены. Дату и формат не повторяй в каждой карточке одинаково.\n"
    "5. Числа как в фактах: «900 000 ₸», «04.10.2026», «55 %». Без KZT, ISO-дат, английских слов (imputed, synthetic). "
    "Оговорки только готовыми формулировками из поля «оговорки».\n"
    "6. Без оценочных прилагательных и общих похвал. Цитату приводи дословно в «кавычках», можно сократить, но не менять слова.\n"
    "Пример хорошего текста: «Единственный из тройки ведёт на английском, что важно для международного корпоратива; при цене от 900 000 ₸ остаётся 55 % бюджета, а лимит 10 ч закрывает запрошенные 4 ч с запасом.»\n"
    "Верни только JSON {\"explanations\":[{\"id\":\"...\",\"text\":\"...\"}]} в заданном порядке карточек. "
    "Запрещённые фразы: " + "; ".join(BANNED_PHRASES)
)

REASON_RU = {
    "busy_on_date": "заняты на эту дату",
    "over_budget": "цена «от» выше бюджета",
    "format_not_supported": "не берут этот формат",
    "language_not_supported": "не работают на нужном языке",
    "duration_exceeds_max": "максимум часов меньше запрошенной длительности",
}
CAVEAT_RU = {
    "price_imputed": "цена проставлена при подготовке датасета, уточняйте",
    "city_imputed": "город проставлен при подготовке датасета",
    "synthetic": "синтетический профиль",
}


def _distinctions(result: MatchResult) -> dict[str, list[str]]:
    """Code-derived, verifiable differences between the shown cards."""
    cards = result.cards
    out: dict[str, list[str]] = {c.contractor.id: [] for c in cards}
    if not cards:
        return out
    if len(cards) == 1:
        note = "единственный подходящий вариант"
        if result.pool_size > 1:
            note += f" из {result.pool_size} в категории"
        out[cards[0].contractor.id].append(note)
        return out
    prices = [c.contractor.price_from_kzt for c in cards]
    cheapest, dearest = min(prices), max(prices)
    for c in cards:
        cid, con = c.contractor.id, c.contractor
        if con.price_from_kzt == cheapest and prices.count(cheapest) == 1:
            out[cid].append("самая низкая цена «от» среди показанных")
        if con.price_from_kzt == dearest and prices.count(dearest) == 1 and cheapest != dearest:
            out[cid].append("самая высокая цена «от» среди показанных")
        for lang in con.languages:
            if sum(lang in o.contractor.languages for o in cards) == 1:
                out[cid].append(f"единственный из показанных работает на языке: {lang}")
        if con.max_hours is None and sum(o.contractor.max_hours is None for o in cards) == 1:
            out[cid].append("единственный, чья работа не привязана к присутствию на площадке")
        hours = [o.contractor.max_hours for o in cards if o.contractor.max_hours is not None]
        if con.max_hours is not None and hours and con.max_hours == max(hours) and hours.count(con.max_hours) == 1:
            out[cid].append(f"самый большой лимит часов среди показанных: {con.max_hours} ч")
        sem = [o.semantic_score for o in cards]
        if c.semantic_score == max(sem) and sem.count(c.semantic_score) == 1:
            out[cid].append("описание ближе всего к запросу")
        if c.rank == 1:
            out[cid].append("выше всех по сумме баллов (запас по бюджету + близость описания + часы)")
        if not out[cid]:
            out[cid].append("средний по цене вариант среди показанных")
    return out


def build_prompt_payload(result: MatchResult) -> dict:
    req = result.request
    distinct = _distinctions(result)
    cards = []
    for f in result.cards:
        con = f.contractor
        facts = {
            "цена от": money(con.price_from_kzt),
            "бюджет": money(f.budget_kzt),
            "запас по бюджету": f"{f.budget_headroom_pct} %",
            "формат": f.format_matched,
            "свободен": format_date(f.free_on_date),
            "языки": ", ".join(con.languages),
        }
        if f.requested_hours is not None:
            facts["запрошено часов"] = f"{f.requested_hours} ч"
        if con.max_hours is None:
            facts["часы"] = "работа не привязана к присутствию на площадке"
        else:
            facts["максимум часов"] = f"{con.max_hours} ч"
        if f.semantic_snippet:
            facts["цитата из описания"] = f.semantic_snippet
        caveats = [CAVEAT_RU[c] for c in f.caveats if c in CAVEAT_RU]
        if caveats:
            facts["оговорки"] = "; ".join(caveats)
        cards.append({"id": con.id, "позиция": f.rank, "имя": con.name, "категория": req.category,
                      "город": con.city, "факты": facts, "чем отличается": distinct[con.id]})
    counts = Counter(r.value for rej in result.rejections for r in rej.reasons)
    summary = [f"{n} {REASON_RU[k]}" for k, n in counts.items()]
    request = {"город": req.city, "дата": format_date(req.event_date), "формат": req.event_format,
               "категория": req.category, "бюджет": money(req.budget_kzt)}
    if req.duration_hours:
        request["длительность"] = f"{req.duration_hours} ч"
    if req.language:
        request["язык"] = req.language
    return {"запрос": request, "карточки": cards,
            "отсеяно из категории": {"всего в категории": result.pool_size, "показано": len(result.cards),
                                       "причины": summary}}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


_log = logging.getLogger(__name__)


class LLMExplainer:
    last_fallback_reason: str | None = None
    def __init__(self, client=None, *, model: str | None = None, api_key: str | None = None):
        self.client, self.api_key = client, api_key
        self.model = model or os.getenv("LLM_MODEL", "gpt-5.4-mini")
        self._cache: dict[str, tuple[Explanation, ...]] = {}

    def explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        key = sha256(_json([asdict(result.request), [c.contractor.id for c in result.cards]]).encode()).hexdigest()
        if key not in self._cache:
            self._cache[key] = self._explain(result) if result.cards else ()
        return self._cache[key]

    def _explain(self, result: MatchResult) -> tuple[Explanation, ...]:
        payload = build_prompt_payload(result)
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
        except Exception as exc:
            # Failure of any card rejects the whole response, preserving one source.
            self.last_fallback_reason = f"{type(exc).__name__}: {exc}"[:300]
            _log.warning("LLM explanation fell back to template: %s", self.last_fallback_reason)
            return TemplateExplainer().explain(result)


def get_explainer() -> LLMExplainer | TemplateExplainer:
    return LLMExplainer() if os.getenv("OPENAI_API_KEY") else TemplateExplainer()
