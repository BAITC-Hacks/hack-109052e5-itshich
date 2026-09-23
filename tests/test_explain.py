from dataclasses import replace
from datetime import date
import json
from types import SimpleNamespace

import pytest

from matcher.model import (
    CardFacts, Contractor, MatchRequest, MatchResult, Outcome, Reason, ReasonFamily, ScoreBreakdown,
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


def reason(code, family, *, primary=False, **evidence):
    return Reason(code, family, evidence, primary=primary)


def reason_cards():
    first, second, third = (card(rank) for rank in range(1, 4))
    return (
        replace(first, reasons=(
            reason("AVAILABILITY_REPLACEMENT", ReasonFamily.AVAILABILITY, primary=True,
                   competitor="Кики", date="14.11.2026"),
            reason("BUDGET_HEADROOM", ReasonFamily.BUDGET,
                   price="600\u2009000 ₸", budget="800\u2009000 ₸", headroom_pct="25"),
            reason("DURATION_HEADROOM", ReasonFamily.DURATION, requested_hours="5", max_hours="8"),
        )),
        replace(second, contractor=replace(second.contractor, price_from_kzt=500_000),
                budget_headroom_pct=38, reasons=(
            reason("BUDGET_LOWER_THAN_SHOWN", ReasonFamily.BUDGET, primary=True,
                   price="500\u2009000 ₸", next_price="600\u2009000 ₸", diff_pct="17"),
            reason("BUDGET_HEADROOM", ReasonFamily.BUDGET,
                   price="500\u2009000 ₸", budget="800\u2009000 ₸", headroom_pct="38"),
            reason("LANGUAGE_REQUEST_MATCH", ReasonFamily.LANGUAGE, language="английский"),
            reason("DURATION_HEADROOM", ReasonFamily.DURATION, requested_hours="5", max_hours="8"),
        )),
        replace(third, contractor=replace(third.contractor, price_imputed=True,
                languages=("русский", "английский", "казахский")), caveats=("price_imputed",), reasons=(
            reason("LANGUAGE_UNIQUE_IN_SHOWN", ReasonFamily.LANGUAGE, primary=True, language="казахский"),
            reason("DESCRIPTION_ASPECT", ReasonFamily.DESCRIPTION, quote=third.contractor.description,
                   semantic_score="0.9"),
            reason("PRICE_IMPUTED", ReasonFamily.DATA_QUALITY),
        )),
    )


def test_template_starts_with_primary_reason_and_adds_only_one_other_family():
    from matcher.explain import TemplateExplainer

    match = result(*reason_cards())
    explanations = TemplateExplainer().explain(match)
    first, second, third = (e.text for e in explanations)

    assert first.startswith("Попал в тройку потому, что Кики занят 14.11.2026")
    assert "без этой брони" in first and "25 %" in first
    assert "500\u2009000 ₸" in second and "600\u2009000 ₸" in second and "17 %" in second
    assert "английский" in second and "38 %" not in second
    assert "казахский" in third and f"«{match.cards[2].contractor.description}»" in third
    assert "цена проставлена при подготовке датасета, уточняйте" in third
    assert all("8 ч" not in e.text and "800\u2009000 ₸" not in e.text for e in explanations[1:])
    assert all(60 <= len(e.text) <= 350 and e.source == "template" for e in explanations)
    assert [e.contractor_id for e in explanations] == ["c1", "c2", "c3"]
    assert TemplateExplainer().explain(match) == explanations


@pytest.mark.parametrize("missing", ["500000", "720000", "31"])
def test_validator_requires_every_primary_number_even_when_other_facts_are_grounded(missing):
    from matcher.explain import validate_explanations

    facts = card(reasons=(reason("BUDGET_LOWER_THAN_SHOWN", ReasonFamily.BUDGET, primary=True,
                 price="500\u2009000 ₸", next_price="720 000 ₸", diff_pct="31"),))
    text = ("Стартовая цена 500000 ₸ на 31 % ниже следующей — 720000 ₸; "
            "формат — корпоратив, свободен 14.11.2026.")
    assert validate_explanations(result(facts), [text]) == []
    problems = validate_explanations(result(facts), [text.replace(missing, "уточняется")])
    assert any("primary" in problem and missing in problem for problem in problems)


def test_validator_requires_named_busy_competitor_and_all_date_numbers():
    from matcher.explain import validate_explanations

    facts = reason_cards()[0]
    valid = ("Попал в тройку, потому что Кики занят 14.11.2026; "
             "без этой брони его место было бы ниже, цена от 600000 ₸.")
    assert validate_explanations(result(facts), [valid]) == []
    for bad in (valid.replace("Кики", "другой кандидат"), valid.replace("14.11.2026", "14.11")):
        assert validate_explanations(result(facts), [bad])


def test_validator_grounds_all_reason_evidence_and_the_template_triple():
    from matcher.explain import TemplateExplainer, validate_explanations

    match = result(*reason_cards())
    texts = [e.text for e in TemplateExplainer().explain(match)]
    assert validate_explanations(match, texts) == []


@pytest.mark.parametrize("quote,valid", [
    ("ВЕДЁТ  деловые\nвстречи с живой импровизацией", True),
    ("деловые встречи с живой импровизацией", True),
    ("Проводит яркие вечеринки для гостей", False),
    ("Ведёт деловые встречи с живой импровизацией…", False),
    ("цена от", True),
])
def test_validator_checks_long_quotes_against_full_description(quote, valid):
    from matcher.explain import validate_explanations

    facts = card(reasons=(reason("BUDGET_HEADROOM", ReasonFamily.BUDGET, primary=True,
                 price="600 000 ₸", budget="800 000 ₸", headroom_pct="25"),))
    text = f"Цена от 600000 ₸ при бюджете 800000 ₸ оставляет запас 25 %; в анкете: «{quote}»."
    problems = validate_explanations(result(facts), [text])
    assert (not problems) == valid
    if not valid:
        assert any("quote" in problem for problem in problems)
        # Legacy callers retain the original grounding rules.
        assert validate_explanations(result(replace(facts, reasons=())), [text]) == []


@pytest.mark.parametrize("code,family,evidence", [
    ("BUDGET_HEADROOM", ReasonFamily.BUDGET, {"price": "600 000 ₸", "budget": "800 000 ₸", "headroom_pct": "25"}),
    ("BUDGET_LOWER_THAN_SHOWN", ReasonFamily.BUDGET, {"price": "600 000 ₸", "next_price": "750 000 ₸", "diff_pct": "20"}),
    ("BUDGET_FITS", ReasonFamily.BUDGET, {"price": "600 000 ₸", "budget": "800 000 ₸"}),
    ("FORMAT_SUPPORTED", ReasonFamily.FORMAT, {"format": "корпоратив"}),
    ("LANGUAGE_REQUEST_MATCH", ReasonFamily.LANGUAGE, {"language": "английский"}),
    ("LANGUAGE_UNIQUE_IN_SHOWN", ReasonFamily.LANGUAGE, {"language": "английский"}),
    ("LANGUAGE_OPTIONS", ReasonFamily.LANGUAGE, {"languages": "русский, казахский, английский"}),
    ("DURATION_HEADROOM", ReasonFamily.DURATION, {"requested_hours": "5", "max_hours": "8"}),
    ("DURATION_MAX_IN_SHOWN", ReasonFamily.DURATION, {"max_hours": "8"}),
    ("DURATION_NOT_APPLICABLE", ReasonFamily.DURATION, {}),
    ("DESCRIPTION_ASPECT", ReasonFamily.DESCRIPTION, {"quote": "Ведёт деловые встречи с живой импровизацией", "semantic_score": "0.913"}),
    ("DESCRIPTION_CLOSEST_IN_SHOWN", ReasonFamily.DESCRIPTION, {"quote": "Ведёт деловые встречи с живой импровизацией"}),
    ("AVAILABILITY_REPLACEMENT", ReasonFamily.AVAILABILITY, {"competitor": "Кики", "date": "14.11.2026"}),
    ("AVAILABILITY_ONLY_FREE", ReasonFamily.AVAILABILITY, {"date": "14.11.2026", "busy_count": "7"}),
])
def test_template_renders_each_reason_at_all_ranks_with_caveats(code, family, evidence):
    from matcher.explain import TemplateExplainer, validate_explanations

    texts = []
    for rank in range(1, 4):
        facts = card(rank)
        support = (reason("DURATION_HEADROOM", ReasonFamily.DURATION, requested_hours="5", max_hours="8")
                   if family == ReasonFamily.BUDGET else
                   reason("BUDGET_FITS", ReasonFamily.BUDGET, price="600 000 ₸", budget="800 000 ₸"))
        facts = replace(facts, contractor=replace(facts.contractor, description=card().contractor.description,
                        max_hours=None if code == "DURATION_NOT_APPLICABLE" else 8), reasons=(
            reason(code, family, primary=True, **evidence), support,
            reason("PRICE_IMPUTED", ReasonFamily.DATA_QUALITY),
            reason("CITY_IMPUTED", ReasonFamily.DATA_QUALITY),
            reason("SYNTHETIC", ReasonFamily.DATA_QUALITY),
        ))
        match = result(facts)
        text, = [e.text for e in TemplateExplainer().explain(match)]
        assert validate_explanations(match, [text]) == []
        assert 60 <= len(text) <= 350
        assert "цена проставлена при подготовке датасета, уточняйте" in text
        assert "город проставлен при подготовке датасета" in text
        assert "синтетический профиль" in text
        for key, value in evidence.items():
            if key != "semantic_score":
                assert value in text
        assert "0.913" not in text
        if code == "DURATION_NOT_APPLICABLE":
            assert "работа не привязана к присутствию на площадке" in text.casefold()
        texts.append(text)
    assert len(set(texts)) == 3


def test_template_validator_accepts_description_duration_and_offsite_triple():
    from matcher.explain import TemplateExplainer, validate_explanations

    first, second, third = (card(rank) for rank in range(1, 4))
    match = result(
        replace(first, reasons=(reason("DESCRIPTION_ASPECT", ReasonFamily.DESCRIPTION, primary=True,
                quote=first.contractor.description, semantic_score="0.9"),
                reason("LANGUAGE_REQUEST_MATCH", ReasonFamily.LANGUAGE, language="английский"))),
        replace(second, reasons=(reason("DURATION_HEADROOM", ReasonFamily.DURATION, primary=True,
                requested_hours="5", max_hours="8"),
                reason("FORMAT_SUPPORTED", ReasonFamily.FORMAT, format="корпоратив"))),
        replace(third, contractor=replace(third.contractor, max_hours=None), reasons=(
                reason("BUDGET_FITS", ReasonFamily.BUDGET, primary=True, price="600 000 ₸", budget="800 000 ₸"),
                reason("DURATION_NOT_APPLICABLE", ReasonFamily.DURATION),
                reason("SYNTHETIC", ReasonFamily.DATA_QUALITY))),
    )
    texts = [e.text for e in TemplateExplainer().explain(match)]
    assert validate_explanations(match, texts) == []
    assert "работа не привязана к присутствию на площадке" in texts[2].casefold()


@pytest.mark.parametrize("quote", [
    "Проводит деловые встречи с живой импровизацией. Индивидуальный подход и высокое качество.",
    "Индивидуальный подход, проводит деловые встречи с живой импровизацией",
    "Проводит деловые встречи! Модерирует дискуссии? Помогает с награждением.",
    "Индивидуальный подход",
])
def test_reason_template_keeps_quotes_verbatim_without_banned_phrases_or_extra_sentences(quote):
    import re
    from matcher.explain import BANNED_PHRASES, TemplateExplainer, validate_explanations

    for rank in range(1, 4):
        facts = card(rank)
        facts = replace(facts, contractor=replace(facts.contractor, description=quote), semantic_snippet=quote,
                        reasons=(reason("DESCRIPTION_ASPECT", ReasonFamily.DESCRIPTION, primary=True, quote=quote),
                                 reason("BUDGET_FITS", ReasonFamily.BUDGET, price="600 000 ₸", budget="800 000 ₸")))
        match = result(facts)
        text, = [e.text for e in TemplateExplainer().explain(match)]
        assert validate_explanations(match, [text]) == []
        assert not any(phrase in text.casefold() for phrase in BANNED_PHRASES)
        for fragment in re.findall("«([^»]+)»", text):
            assert fragment in quote


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
    "Попал в тройку, потому что Кики занят 14.11.2026: без этой брони остался бы за её пределами. Цена от 600\u2009000 ₸ оставляет запас 25 % при бюджете 800\u2009000 ₸.",
    "Самая низкая цена «от» среди показанных: 500\u2009000 ₸, на 17 % ниже следующей — 600\u2009000 ₸. Работает на запрошенном языке: английский.",
    "Среди показанных только этот подрядчик работает на языке казахский. В описании: «Модерирует дискуссии и церемонии награждения».",
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
    from matcher.explain import LLMExplainer, build_prompt_payload

    match = result(*reason_cards())
    client = FakeChatClient(llm_json())
    explanations = LLMExplainer(client=client).explain(match)

    assert [(e.contractor_id, e.text, e.source) for e in explanations] == [
        (f"c{i}", text, "llm") for i, text in enumerate(LLM_TEXTS, 1)]
    call, = client.calls
    assert (call["temperature"], call["seed"], call["timeout"]) == (0, 42, 8)
    assert call["response_format"] == {"type": "json_object"}
    payload = json.loads(call["messages"][1]["content"])
    assert payload == build_prompt_payload(match)
    assert set(payload) == {"запрос", "карточки"}
    assert payload["запрос"] == {
        "город": "Алматы", "дата": "14.11.2026", "формат": "корпоратив", "категория": "Ведущий",
        "бюджет": "800\u2009000 ₸", "длительность": "5 ч", "язык": "английский",
    }
    assert [c["id"] for c in payload["карточки"]] == ["c1", "c2", "c3"]
    for c, facts in zip(payload["карточки"], match.cards):
        assert set(c) == {"id", "имя", "позиция", "главная_причина", "поддерживающие", "оговорки"}
        assert {k: v for k, v in c["главная_причина"].items() if k != "смысл"} == {"код": facts.reasons[0].code, "факты": facts.reasons[0].evidence}
        assert c["главная_причина"]["смысл"]
    assert payload["карточки"][2]["оговорки"] == [{
        "код": "PRICE_IMPUTED", "формулировка": "цена проставлена при подготовке датасета, уточняйте",
    }]
    assert payload["карточки"][2]["поддерживающие"] == [{
        "код": "DESCRIPTION_ASPECT", "факты": {"quote": match.cards[2].contractor.description},
    }]
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("description", "score", "contribution", "price_imputed", "city_imputed", "synthetic", "0.9"):
        assert forbidden not in serialized


@pytest.mark.parametrize("failure", ["json", "shape", "banned", "digits", "timeout", "api", "count", "order", "type",
                                     "primary_number", "competitor", "quote"])
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
    if failure == "primary_number":
        content = llm_json([LLM_TEXTS[0], LLM_TEXTS[1].replace("600\u2009000 ₸", "другого кандидата"), LLM_TEXTS[2]])
    if failure == "competitor":
        content = llm_json([LLM_TEXTS[0].replace("Кики", "конкурент"), *LLM_TEXTS[1:]])
    if failure == "quote":
        content = llm_json([*LLM_TEXTS[:2], LLM_TEXTS[2].replace(
            "Модерирует дискуссии и церемонии награждения", "Проводит яркие вечеринки для гостей")])
    match = result(*reason_cards())

    explanations = LLMExplainer(client=FakeChatClient(content, error)).explain(match)

    assert explanations == TemplateExplainer().explain(match)
    assert all(e.source == "template" for e in explanations)


@pytest.mark.parametrize("content", [llm_json(), "bad json"])
def test_llm_caches_success_and_fallback_by_request_and_ordered_card_ids(content):
    from matcher.explain import LLMExplainer

    client = FakeChatClient(content)
    explainer = LLMExplainer(client=client, model="configured-chat-model")
    match = result(*reason_cards())
    first = explainer.explain(match)
    client.content = "changed response"

    assert explainer.explain(match) == first
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "configured-chat-model"
    explainer.explain(replace(match, request=replace(match.request, budget_kzt=900_000)))
    assert len(client.calls) == 2
    explainer.explain(result(*reason_cards()[:2]))
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


def test_llm_file_cache_replays_without_a_client(tmp_path):
    from matcher.explain import LLMExplainer

    match = result(*reason_cards())
    path = tmp_path / "cache.json"
    first = LLMExplainer(client=FakeChatClient(llm_json()), cache_path=path).explain(match)
    assert all(e.source == "llm" for e in first) and path.exists()
    replay = LLMExplainer(client=None, api_key="", cache_path=path).explain(match)
    assert replay == first
    # A different model or prompt version must not reuse the cached text.
    other = LLMExplainer(client=None, api_key="", model="other-model", cache_path=path).explain(match)
    assert all(e.source == "template" for e in other)


@pytest.mark.parametrize("restart", [False, True])
def test_llm_cache_refreshes_when_primary_code_changes(tmp_path, restart):
    from matcher.explain import LLMExplainer

    facts = card(reasons=(reason("DESCRIPTION_ASPECT", ReasonFamily.DESCRIPTION, primary=True,
                 quote=card().contractor.description),))
    original = "С запросом перекликается описание: «Ведёт деловые встречи с живой импровизацией»."
    changed = "Ближе всего к запросу описание: «Ведёт деловые встречи с живой импровизацией»."
    client = FakeChatClient(llm_json([original]))
    path = tmp_path / "reason-cache.json"
    explainer = LLMExplainer(client=client, cache_path=path)
    first, = explainer.explain(result(facts))
    assert first.text == original and first.source == "llm"

    client.content = llm_json([changed])
    if restart:
        explainer = LLMExplainer(client=client, cache_path=path)
    facts = replace(facts, reasons=(replace(facts.reasons[0], code="DESCRIPTION_CLOSEST_IN_SHOWN"),))
    refreshed, = explainer.explain(result(facts))

    assert refreshed.text == changed and refreshed.source == "llm"
    assert len(client.calls) == 2
    assert LLMExplainer(api_key="", cache_path=path).explain(result(facts)) == (refreshed,)


def test_llm_uses_legacy_template_when_no_reason_codes_are_available():
    from matcher.explain import LLMExplainer, TemplateExplainer

    match = result(card(), card(2), card(3))
    client = FakeChatClient("no code-derived facts to phrase")
    assert LLMExplainer(client=client).explain(match) == TemplateExplainer().explain(match)
    assert client.calls == []
