from dataclasses import replace
from datetime import date
import json
from types import SimpleNamespace

import pytest

from matcher.model import (
    CardFacts, Contractor, MatchRequest, MatchResult, Outcome, ScoreBreakdown,
)


def card(rank=1, **changes):
    contractor = Contractor(
        id=f"c{rank}", name=("Хикару", "Микаса", "Рен")[rank - 1],
        categories=("Ведущий",), city="Алматы", city_imputed=False,
        synthetic=False, price_from_kzt=600_000, price_imputed=False,
        event_formats=("корпоратив",), languages=("русский", "английский"),
        max_hours=8, busy_dates=frozenset(),
        description=("Ведёт деловые встречи с живой импровизацией", "Проводит музыкальные игры для команд", "Модерирует дискуссии и церемонии награждения")[rank - 1],
    )
    facts = CardFacts(
        contractor=contractor, rank=rank,
        score=ScoreBreakdown(0.25, 0.9, 1.0, 0.375, 1.0, 0.64),
        budget_kzt=800_000, budget_headroom_pct=25,
        format_matched="корпоратив", requested_language="английский",
        languages_matched=("английский",), requested_hours=5,
        duration_note="fits", semantic_snippet=contractor.description,
        semantic_score=0.9, free_on_date=date(2026, 11, 14), caveats=(),
    )
    return replace(facts, **changes)


def result(*cards):
    return MatchResult(
        request=MatchRequest("Алматы", date(2026, 11, 14), "корпоратив", "Ведущий", 800_000, 5, "английский"),
        outcome=Outcome.MATCHED if cards else Outcome.NONE_ELIGIBLE,
        cards=cards, rejections=(), pool_size=len(cards), eligible_count=len(cards),
        shortfall_note=None, semantic_backend="embeddings",
    )


def test_template_explains_this_contractor_with_exact_money_date_and_details():
    from matcher.explain import TemplateExplainer

    explanation, = TemplateExplainer().explain(result(card()))

    assert explanation.contractor_id == "c1"
    assert explanation.source == "template"
    assert explanation.text.startswith("Первый в выдаче: Хикару")
    for fact in ("600\u2009000 ₸", "800\u2009000 ₸", "25 %", "корпоратив",
                 "английский", "до 8 ч при запрошенных 5 ч", "свободен 14.11.2026",
                 "«Ведёт деловые встречи с живой импровизацией»"):
        assert fact in explanation.text
    assert 40 <= len(explanation.text) <= 350


@pytest.mark.parametrize("text", [
    "Цена от 600\u2009000 ₸, бюджет 800\u2009000 ₸; свободен 14.11.2026.",
    "Корпоратив: свободен 14 ноября, английский язык, до 8 ч при запрошенных 5 ч.",
    "Ведёт деловые встречи с живой импровизацией; формат — корпоратив.",
    "Формат — корпоратив; работает на английском языке по запросу.",
])
def test_validator_accepts_grounded_numbers_russian_dates_and_snippet(text):
    from matcher.explain import validate_explanations

    assert validate_explanations(result(card()), [text]) == []


@pytest.mark.parametrize("texts", [
    [],
    ["Корпоратив."],
    ["А" * 351],
    ["Опытный ведущий создаёт настроение и помогает провести вечер без забот."],
    ["Корпоратив, свободен 14 ноября. Цена от 600000 ₸. Бюджет 800000 ₸."],
    ["Корпоратив, бюджет 800000 ₸; проведёт 99 мероприятий для гостей."],
    ["Цена от 600000 ₸; бюджет 800000 ₸ — ОТЛИЧНЫЙ ВЫБОР для корпоратива."],
    ["Корпоратив, цена от 600000 ₸; бюджет 800000 ₸." for _ in range(2)],
])
def test_validator_rejects_invalid_structure_fluff_and_ungrounded_numbers(texts):
    from matcher.explain import validate_explanations

    assert validate_explanations(result(card()), texts)


def test_validator_rejects_duplicate_texts_between_cards():
    from matcher.explain import validate_explanations

    text = "Корпоратив, цена от 600000 ₸; бюджет 800000 ₸."
    assert validate_explanations(result(card(), card(2)), [text, text])


@pytest.mark.parametrize("profile", ["similar", "single", "identical", "offsite", "imputed", "all_flags", "empty"])
def test_validator_accepts_every_template_with_similar_profiles_and_caveats(profile):
    from matcher.explain import TemplateExplainer, validate_explanations

    cards = [card(rank) for rank in range(1, 4)]
    if profile == "single":
        cards = cards[:1]
    if profile == "empty":
        cards = []
    if profile == "identical":
        cards = [replace(c, semantic_snippet=cards[0].semantic_snippet) for c in cards]
    if profile in {"offsite", "imputed", "all_flags"}:
        flags = () if profile == "offsite" else ("price_imputed", "city_imputed", "synthetic") if profile == "all_flags" else ("price_imputed",)
        cards = [replace(c, contractor=replace(c.contractor, max_hours=None,
                 price_imputed="price_imputed" in flags, city_imputed="city_imputed" in flags,
                 synthetic="synthetic" in flags), duration_note="not_applicable", caveats=flags) for c in cards]
    match = result(*cards)

    explanations = TemplateExplainer().explain(match)

    assert [e.contractor_id for e in explanations] == [c.contractor.id for c in cards]
    assert validate_explanations(match, [e.text for e in explanations]) == []
    assert TemplateExplainer().explain(match) == explanations
    for facts, explanation, opening in zip(cards, explanations, ("Первый в выдаче: ", "На втором месте ", "Замыкает тройку ")):
        assert explanation.text.startswith(opening)
        if facts.contractor.max_hours is None:
            assert "работа не привязана к присутствию на площадке" in explanation.text
        for flag, wording in (("price_imputed", "цена проставлена при подготовке датасета, уточняйте"),
                              ("city_imputed", "город проставлен при подготовке датасета"),
                              ("synthetic", "синтетический профиль")):
            if flag in facts.caveats:
                assert wording in explanation.text


@pytest.mark.parametrize("snippet", [
    "Индивидуальный подход и высокое качество для ваших гостей.",
    "Модерирует деловые встречи. Проводит церемонии! Работает с гостями?",
    "Модерирует деловые встречи " + "для больших компаний " * 10 + "12345 гостей",
    "Модерирует деловые встречи " + "а" * 56 + " 123456789 гостей",
])
def test_template_uses_only_safe_short_quotes(snippet):
    import re
    from matcher.explain import TemplateExplainer, validate_explanations

    match = result(card(semantic_snippet=snippet))
    explanation, = TemplateExplainer().explain(match)
    assert validate_explanations(match, [explanation.text]) == []
    for quote in re.findall("«([^»]+)»", explanation.text):
        assert len(quote) <= 90
        assert quote.rstrip("…") in snippet


LLM_TEXTS = [
    "Хикару: цена от 600\u2009000 ₸, бюджет 800\u2009000 ₸; корпоратив, свободен 14.11.2026.",
    "Микаса проводит музыкальные игры для команд: корпоратив, до 8 ч при запрошенных 5 ч; резерв 25 %.",
    "Рен модерирует дискуссии и церемонии награждения; стартовая стоимость 600000 ₸, доступен 14 ноября.",
]


class FakeChatClient:
    def __init__(self, content, error=None):
        self.content, self.error, self.calls = content, error, []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def llm_json(texts=LLM_TEXTS):
    return json.dumps({"explanations": [{"id": f"c{i}", "text": text} for i, text in enumerate(texts, 1)]})


def test_llm_returns_valid_ordered_explanations_and_uses_compact_grounded_prompt():
    from matcher.explain import LLMExplainer

    match = result(card(), card(2), card(3))
    client = FakeChatClient(llm_json())
    explanations = LLMExplainer(client=client).explain(match)

    assert [(e.contractor_id, e.text, e.source) for e in explanations] == [
        (f"c{i}", text, "llm") for i, text in enumerate(LLM_TEXTS, 1)]
    call, = client.calls
    assert (call["temperature"], call["seed"], call["timeout"]) == (0, 42, 8)
    assert call["response_format"] == {"type": "json_object"}
    payload = json.loads(call["messages"][1]["content"])
    assert set(payload) == {"запрос", "карточки", "отсеяно из категории"}
    assert [c["id"] for c in payload["карточки"]] == ["c1", "c2", "c3"]
    # Only pre-formatted facts reach the model: no raw description, no raw flags.
    for c in payload["карточки"]:
        assert "description" not in json.dumps(c, ensure_ascii=False)
        assert "цена от" in c["факты"] and "запас по бюджету" in c["факты"]
        assert isinstance(c["чем отличается"], list) and c["чем отличается"]


@pytest.mark.parametrize("failure", ["json", "shape", "banned", "digits", "timeout", "api", "count", "order", "type"])
def test_llm_falls_back_for_the_entire_result_when_any_response_is_invalid(failure):
    from matcher.explain import LLMExplainer, TemplateExplainer

    content, error = llm_json(), None
    if failure in {"json", "shape"}:
        content = "not json" if failure == "json" else '{"explanations":{}}'
    if failure in {"banned", "digits", "type"}:
        last = {"banned": LLM_TEXTS[2] + " Отличный выбор!", "digits": LLM_TEXTS[2] + " Опыт 99 лет.", "type": None}[failure]
        content = llm_json([*LLM_TEXTS[:2], last])
    if failure in {"timeout", "api"}:
        error = TimeoutError("8 seconds") if failure == "timeout" else RuntimeError("API unavailable")
    if failure == "count":
        content = llm_json(LLM_TEXTS[:2])
    if failure == "order":
        content = json.dumps({"explanations": list(reversed(json.loads(content)["explanations"]))})
    match = result(card(), card(2), card(3))

    explanations = LLMExplainer(client=FakeChatClient(content, error)).explain(match)

    assert explanations == TemplateExplainer().explain(match)
    assert all(e.source == "template" for e in explanations)


@pytest.mark.parametrize("content", [llm_json(), "bad json"])
def test_llm_caches_success_and_fallback_by_request_and_ordered_card_ids(content):
    from matcher.explain import LLMExplainer

    client = FakeChatClient(content)
    explainer = LLMExplainer(client=client, model="configured-chat-model")
    match = result(card(), card(2), card(3))
    first = explainer.explain(match)
    client.content = "changed response"

    assert explainer.explain(match) == first
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "configured-chat-model"
    explainer.explain(replace(match, request=replace(match.request, budget_kzt=900_000)))
    assert len(client.calls) == 2
    explainer.explain(result(card(), card(2)))
    assert len(client.calls) == 3


def test_template_distinguishes_imputed_offsite_cards_even_without_optional_facts():
    from matcher.explain import TemplateExplainer, validate_explanations

    cards = []
    for rank, name in enumerate(("Наруто Удзумаки", "Тандзиро Камадо", "Гон Фрикс"), 1):
        c = card(rank)
        cards.append(replace(c, contractor=replace(c.contractor, name=name, max_hours=None,
                     price_imputed=True, city_imputed=True, synthetic=True),
                     requested_language=None, requested_hours=None, format_matched="день рождения",
                     semantic_snippet=None, caveats=("price_imputed", "city_imputed", "synthetic")))
    match = result(*cards)
    texts = [e.text for e in TemplateExplainer().explain(match)]
    assert validate_explanations(match, texts) == []


def test_distinctions_are_code_derived_and_verifiable():
    from matcher.explain import _distinctions

    match = result(card(), card(2), card(3))
    distinct = _distinctions(match)
    assert set(distinct) == {c.contractor.id for c in match.cards}
    assert all(notes for notes in distinct.values())
    prices = {c.contractor.id: c.contractor.price_from_kzt for c in match.cards}
    cheapest = min(prices.values())
    if list(prices.values()).count(cheapest) == 1:
        cheapest_id = next(i for i, p in prices.items() if p == cheapest)
        assert any("самая низкая цена" in n for n in distinct[cheapest_id])
    single = result(card())
    assert any("единственный" in n for n in _distinctions(single)[single.cards[0].contractor.id])
