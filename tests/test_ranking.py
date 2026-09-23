from dataclasses import replace

import pytest

from matcher.model import ScoreBreakdown
from matcher.ranking import rank


class GivenScorer:
    """An injected scorer with known scores, independent of the rank formula."""
    name = "embeddings"

    def __init__(self, scores=None):
        self.scores = scores or {}

    def score(self, request, contractors):
        return {c.id: self.scores.get(c.id, 0.6) for c in contractors}

    def snippet(self, request, contractor):
        return "Ведущая корпоративов и свадеб"


def test_rank_materializes_weighted_facts(contractor, match_request):
    candidate = replace(contractor, price_imputed=True, city_imputed=True)
    request = replace(match_request, duration_hours=4, language="казахский")
    cards = rank([candidate], request, GivenScorer())
    assert len(cards) == 1
    card = cards[0]
    assert card.contractor == candidate and card.rank == 1
    assert card.score == ScoreBreakdown(0.75, 0.6, 1.0, 0.5, 0.25, 0.6475)
    assert card.budget_kzt == 800_000 and card.budget_headroom_pct == 75
    assert card.format_matched == "корпоратив"
    assert card.requested_language == "казахский"
    assert card.languages_matched == ("казахский",)
    assert card.requested_hours == 4 and card.duration_note == "fits"
    assert card.semantic_score == 0.6
    assert card.semantic_snippet == "Ведущая корпоративов и свадеб"
    assert card.free_on_date == request.event_date
    assert card.caveats == ("price_imputed", "city_imputed")


def test_ties_break_by_id_before_truncation_and_rank_numbers(contractor, match_request):
    candidates = [replace(contractor, id=id_) for id_ in ("d", "b", "c", "a")]
    cards = rank(candidates, match_request, GivenScorer())
    assert [c.contractor.id for c in cards] == ["a", "b", "c"]
    assert [c.rank for c in cards] == [1, 2, 3]
    assert cards == rank(list(reversed(candidates)), match_request, GivenScorer())


def test_budget_headroom_orders_otherwise_equal_candidates(sample_contractors, match_request):
    cards = rank(list(reversed(sample_contractors)), match_request, GivenScorer())
    assert [c.contractor.id for c in cards] == ["c-01", "c-02", "c-03"]
    assert [c.budget_headroom_pct for c in cards] == [75, 50, 25]


@pytest.mark.parametrize("max_hours,hours,note,duration", [
    (8, None, "not_requested", 0.5), (None, None, "not_requested", 0.5),
    (None, 100, "not_applicable", 0.5), (4, 4, "fits", 0.0),
])
def test_optional_facts_and_neutral_duration(contractor, match_request, max_hours, hours, note, duration):
    candidate = replace(contractor, max_hours=max_hours, synthetic=True, price_from_kzt=800_000)
    card, = rank([candidate], replace(match_request, duration_hours=hours), GivenScorer())
    assert card.duration_note == note and card.score.duration_fit == duration
    assert card.score.language_fit == pytest.approx(2 / 3)
    assert card.languages_matched == ("русский", "казахский")
    assert card.requested_language is None
    assert card.score.budget_fit == 0.0 and card.budget_headroom_pct == 0
    assert card.score.data_quality == 0.75 and card.caveats == ("synthetic",)


def test_lexical_relevance_uses_distinct_stems_and_best_sentence(contractor, match_request):
    from matcher.lexical import LexicalScorer

    candidate = replace(contractor, description=(
        "КОМПАНИИ, компании, компании. Тимбилдинг для бизнеса! Просто текст."
    ))
    card, = rank([candidate], match_request, LexicalScorer())
    assert card.semantic_score == 0.75
    assert card.semantic_snippet == "Тимбилдинг для бизнеса"


@pytest.mark.parametrize("field,value,description,expected", [
    ("event_format", "свадьба", "Свадебный день, невеста, молодожёны, ЗАГС, WEDDING", 1.0),
    ("event_format", "конференция", "Форум, спикеры, модератор, деловые встречи", 1.0),
    ("event_format", "юбилей", "Юбилейный вечер", 0.25),
    ("event_format", "день рождения", "День рождения: именинники, BIRTHDAY, детские игры", 1.0),
    ("event_format", "той", "Беташар, кыз узату, сундет, национальные традиции", 1.0),
    ("category", "Ведущий", "Ведущая вечера", 0.25),
    ("category", "Ведущий церемонии", "Церемониймейстер", 0.25),
    ("category", "Флорист", "Букеты и композиции", 0.25),
    ("category", "Декоратор", "Сценография", 0.25),
    ("category", "Фотограф", "Фотографии гостей", 0.25),
    ("category", "Видеограф", "Видеосъёмка гостей", 0.25),
    ("category", "Подарки и сувениры", "Подарочные наборы", 0.25),
    ("category", "Инструменталист", "Саксофонист", 0.25),
    ("category", "Лайв-бэнд", "Живая музыка", 0.25),
    ("category", "Национальный ансамбль", "Ансамбли", 0.25),
    ("category", "Танцевальный коллектив", "Хореография", 0.25),
    ("category", "Шоу-программа", "Выступления", 0.25),
    ("category", "Фото и видеобудки", "Фотобудка", 0.25),
    ("category", "Банкетный зал", "Банкеты", 0.25),
    ("category", "Загородная площадка", "Террасы", 0.25),
    ("category", "Ресторан", "Ресторанная кухня", 0.5),
    ("category", "Отель", "Гостиница", 0.25),
    ("language", "английский", "ENGLISH, international, международные события", 0.75),
    ("language", "казахский", "ҚАЗАҚ тілінде, казахские традиции", 0.5),
    ("language", "русский", "Русскоязычная программа", 0.25),
])
def test_lexical_dictionary_covers_query_terms(contractor, match_request, field, value, description, expected):
    from matcher.lexical import LexicalScorer

    request = replace(match_request, category="Неизвестная", **({} if field == "category" else {field: value}))
    if field == "category":
        request = replace(request, category=value)
    candidate = replace(contractor, description=description)
    card, = rank([candidate], request, LexicalScorer())
    assert card.semantic_score == expected
    assert card.semantic_snippet == description


@pytest.mark.parametrize("description,score,snippet", [
    ("", 0.0, None), ("Нейтральное описание", 0.0, None),
    ("КОМПАНИЯ!БИЗНЕС?TEAM\nТИМБИЛДИНГ", 1.0, "КОМПАНИЯ"),
    ("Корпоратив " + "а" * 210, 0.25, "Корпоратив " + "а" * 189),
])
def test_lexical_snippet_ties_fallback_and_contract_limit(contractor, match_request, description, score, snippet):
    from matcher.lexical import LexicalScorer

    card, = rank([replace(contractor, description=description)], match_request, LexicalScorer())
    assert (card.semantic_score, card.semantic_snippet) == (score, snippet)


def test_rounded_total_ties_use_id_even_with_different_unrounded_scores(contractor, match_request):
    candidates = [replace(contractor, id="z"), replace(contractor, id="a", price_from_kzt=200_001)]
    cards = rank(candidates, match_request, GivenScorer())
    assert [card.score.total for card in cards] == [0.6892, 0.6892]
    assert [card.contractor.id for card in cards] == ["a", "z"]
