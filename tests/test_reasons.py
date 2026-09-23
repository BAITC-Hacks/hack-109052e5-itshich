"""Reason selection is observed through assign(), preserving the ranked cards."""
from dataclasses import replace
from datetime import date
from fractions import Fraction

import pytest

from matcher import ranking
from matcher.filtering import filter_pool
from matcher.lexical import LexicalScorer
from matcher.model import FEATURES, MAX_CARDS, MatchRequest, MatchResult, Outcome, ReasonFamily


def reason_input(catalogue, request, scorer=None):
    scorer = scorer or LexicalScorer()
    pool, eligible, rejections = filter_pool(catalogue, request)
    scored = ranking.score_all(eligible, request, scorer)
    result = MatchResult(
        request, Outcome.MATCHED if eligible else Outcome.NONE_ELIGIBLE,
        scored[:MAX_CARDS], rejections, len(pool), len(eligible), None, scorer.name,
    )
    return result, scored, scorer


def demo_request(demo_queries, name):
    entry = next(entry for entry in demo_queries if entry["name"] == name)
    return MatchRequest(**(entry["request"] | {"event_date": date.fromisoformat(entry["request"]["event_date"])}))


def test_identical_cards_keep_order_and_use_factual_fallback_before_caveats(contractor, match_request):
    from matcher.reasons import assign

    catalogue = [replace(contractor, id=str(i), description="", languages=("русский",),
                         price_from_kzt=match_request.budget_kzt, synthetic=True)
                 for i in range(3)]
    result, scored, scorer = reason_input(catalogue, match_request)
    assigned = assign(result, scored, catalogue, scorer)
    assert [c.contractor.id for c in assigned.cards] == ["0", "1", "2"]
    assert all(not c.reasons for c in result.cards)
    assert assigned.diversity_limited is True
    for card in assigned.cards:
        assert [r.code for r in card.reasons] == ["BUDGET_FITS", "FORMAT_SUPPORTED", "SYNTHETIC"]
        assert [r.primary for r in card.reasons] == [True, False, False]
        assert card.reasons[0].evidence == {"price": "800\u2009000 ₸", "budget": "800\u2009000 ₸"}
        assert card.reasons[-1].family == ReasonFamily.DATA_QUALITY


def test_budget_contrast_uses_all_eligible_for_contribution_and_formatted_evidence(contractor, match_request):
    from matcher.reasons import assign

    request = replace(match_request, budget_kzt=1_000_000)
    catalogue = [replace(contractor, id=str(i), price_from_kzt=price, description="")
                 for i, price in enumerate((200_000, 400_000, 600_000, 800_000))]
    result, scored, scorer = reason_input(catalogue, request)
    assigned = assign(result, scored, catalogue, scorer)
    cheapest = assigned.cards[0].reasons[0]
    assert cheapest.code == "BUDGET_LOWER_THAN_SHOWN"
    assert cheapest.evidence == {"price": "200\u2009000 ₸", "next_price": "400\u2009000 ₸", "diff_pct": "50"}
    # Baseline includes the fourth, unshown 800,000 profile: phi = .35 * (.8 - .5).
    assert cheapest.contribution == pytest.approx(0.105)
    assert any(r.code == "BUDGET_HEADROOM" and r.contribution == pytest.approx(0.035)
               for r in assigned.cards[1].reasons)
    assert all(r.code != "BUDGET_FITS" for c in assigned.cards for r in c.reasons)


@pytest.mark.parametrize("snippet, expected", [
    ("Корпоративы для команды", "Корпоративы для команды"),
    ("Корпоративы " + "для команды " * 15, "Корпоративы " + "для команды " * 9),
    ("Несуществующая цитата", None),
    ("я" * 121, None),
    (None, None),
], ids=["short", "long", "invented", "unbroken-word", "missing"])
def test_description_quotes_are_verbatim_and_end_at_a_word_boundary(
        contractor, match_request, snippet, expected):
    from matcher.reasons import assign

    description = "Корпоративы " + "для команды " * 15 + "\n" + "я" * 121
    catalogue = [replace(contractor, description=description, price_from_kzt=match_request.budget_kzt)]
    result, scored, scorer = reason_input(catalogue, match_request)
    scored = (replace(scored[0], semantic_snippet=snippet),)
    result = replace(result, cards=scored)
    assigned = assign(result, scored, catalogue, scorer)
    quoted = [r for r in assigned.cards[0].reasons if "quote" in r.evidence]
    if expected is None:
        assert quoted == []
    else:
        reason, = quoted
        assert reason.code == "DESCRIPTION_ASPECT" and reason.primary
        assert reason.evidence["quote"] == expected.rstrip()
        assert reason.evidence["quote"] in description and len(reason.evidence["quote"]) <= 120
        assert reason.evidence["semantic_score"] == str(scored[0].semantic_score)


@pytest.mark.parametrize("code, fields, peer_fields, request_fields, evidence", [
    ("LANGUAGE_REQUEST_MATCH", {}, {}, {"language": "русский"}, {"language": "русский"}),
    ("LANGUAGE_UNIQUE_IN_SHOWN", {"languages": ("русский", "английский")},
     {"languages": ("русский",)}, {}, {"language": "английский"}),
    ("LANGUAGE_OPTIONS", {"languages": ("русский", "казахский", "английский")},
     {"languages": ("русский", "казахский", "английский")}, {},
     {"languages": "английский, казахский, русский"}),
    ("DURATION_HEADROOM", {"max_hours": 12}, {"max_hours": 6}, {"duration_hours": 4},
     {"requested_hours": "4", "max_hours": "12"}),
    ("DURATION_MAX_IN_SHOWN", {"max_hours": 12}, {"max_hours": 6}, {}, {"max_hours": "12"}),
    ("DURATION_NOT_APPLICABLE", {"max_hours": None}, {}, {}, {}),
], ids=lambda value: value if isinstance(value, str) else None)
def test_language_and_duration_reasons_carry_only_their_grounded_evidence(
        contractor, match_request, code, fields, peer_fields, request_fields, evidence):
    from matcher.reasons import assign

    request = replace(match_request, **request_fields)
    catalogue = [replace(contractor, description="", **fields),
                 replace(contractor, id="peer", description="", **peer_fields)]
    result, scored, scorer = reason_input(catalogue, request)
    assigned = assign(result, scored, catalogue, scorer)
    card = next(c for c in assigned.cards if c.contractor.id == contractor.id)
    reason = next(r for r in card.reasons if r.code == code)
    assert reason.evidence == evidence
    assert card.reasons[0].code not in {"FORMAT_SUPPORTED", "DURATION_NOT_APPLICABLE"}
    assert all(r.code != "FORMAT_SUPPORTED" for r in card.reasons)
    if code == "LANGUAGE_REQUEST_MATCH":
        assert reason.contribution == 0


def test_date_pair_replacement_is_proved_by_returning_kiki_alone(real_contractors, demo_queries, offline_demo_scorer):
    from matcher.reasons import assign

    request = demo_request(demo_queries, "date_pair_b")
    result, scored, scorer = reason_input(real_contractors, request, offline_demo_scorer)
    assigned = assign(result, scored, real_contractors, scorer)
    replacements = [(card, r) for card in assigned.cards for r in card.reasons
                    if r.code == "AVAILABILITY_REPLACEMENT" and r.evidence["competitor"] == "Кики"]
    assert replacements, "Report the counterfactual maths in RESULT.md if the dataset changes"
    kiki = next(c for c in real_contractors if c.id == "HK-35215")
    assert request.event_date == date(2026, 10, 5) and request.event_date in kiki.busy_dates
    _, ignoring_date, _ = filter_pool(real_contractors, request, ignore_date=True)
    top_without_date = ranking.score_all(ignoring_date, request, scorer)[:MAX_CARDS]
    assert kiki.id in {c.contractor.id for c in top_without_date}
    _, eligible, _ = filter_pool(real_contractors, request)
    restored = ranking.score_all(eligible + [kiki], request, scorer)[:MAX_CARDS]
    for card, reason in replacements:
        assert card.contractor.id not in {c.contractor.id for c in top_without_date}
        assert card.contractor.id not in {c.contractor.id for c in restored}
        assert reason.evidence == {"competitor": "Кики", "date": "05.10.2026"}


def test_only_free_card_reports_busy_count_and_all_caveats(contractor, match_request):
    from matcher.reasons import assign

    free = replace(contractor, price_from_kzt=match_request.budget_kzt, description="",
                   max_hours=None, synthetic=True, price_imputed=True, city_imputed=True)
    catalogue = [free, replace(contractor, id="busy", busy_dates=frozenset({match_request.event_date})),
                 replace(contractor, id="busy-expensive", price_from_kzt=2_000_000,
                         busy_dates=frozenset({match_request.event_date}))]
    result, scored, scorer = reason_input(catalogue, match_request)
    assigned = assign(result, scored, catalogue, scorer)
    card, = assigned.cards
    assert card.reasons[0].code == "AVAILABILITY_ONLY_FREE" and card.reasons[0].primary
    assert card.reasons[0].evidence == {"date": "14.11.2026", "busy_count": "2"}
    assert [r.code for r in card.reasons[-3:]] == ["CITY_IMPUTED", "PRICE_IMPUTED", "SYNTHETIC"]
    assert not any(r.primary for r in card.reasons[1:])
    assert assigned.diversity_limited is False


@pytest.mark.parametrize("name", ["dense", "rare", "date_pair_b"])
def test_real_contributions_decompose_the_score_relative_to_all_eligible(
        real_contractors, demo_queries, offline_demo_scorer, name):
    from matcher.reasons import assign

    request = demo_request(demo_queries, name)
    result, scored, scorer = reason_input(real_contractors, request, offline_demo_scorer)
    assigned = assign(result, scored, real_contractors, scorer)
    feature_for = {ReasonFamily.BUDGET: "budget_fit", ReasonFamily.LANGUAGE: "language_fit",
                   ReasonFamily.DURATION: "duration_fit", ReasonFamily.DESCRIPTION: "semantic"}
    weights = ranking.weights_for(request)
    # Independent exact-rational oracle; reason evidence exposes selected phi values,
    # while the score identity covers all five features, including data quality.
    for card in assigned.cards:
        phi = {}
        for feature in FEATURES:
            mean = sum(Fraction(str(getattr(c.score, feature))) for c in scored) / len(scored)
            phi[feature] = Fraction(str(weights[feature])) * (Fraction(str(getattr(card.score, feature))) - mean)
        score_advantage = card.score.total - sum(c.score.total for c in scored) / len(scored)
        assert float(sum(phi.values())) == pytest.approx(score_advantage, abs=1e-3)
        for reason in card.reasons:
            if feature := feature_for.get(reason.family):
                assert reason.contribution == pytest.approx(float(phi[feature]), abs=0.000051)
            else:
                assert reason.contribution == 0


def test_dense_diversity_and_rare_contrasts_are_honest(real_contractors, demo_queries, offline_demo_scorer):
    from matcher.reasons import assign

    result, scored, scorer = reason_input(real_contractors, demo_request(demo_queries, "dense"), offline_demo_scorer)
    dense = assign(result, scored, real_contractors, scorer)
    primary_codes = {c.reasons[0].code for c in dense.cards}
    assert len(dense.cards) == 3
    if len(primary_codes) == 3:
        assert dense.diversity_limited is False
    else:
        assert dense.diversity_limited is True
    result, scored, _ = reason_input(real_contractors, demo_request(demo_queries, "rare"), scorer)
    rare = assign(result, scored, real_contractors, scorer)
    assert len(rare.cards) == 2
    assert any(r.code in {"BUDGET_LOWER_THAN_SHOWN", "DESCRIPTION_CLOSEST_IN_SHOWN"}
               for c in rare.cards for r in c.reasons)
    synthetic, = [c for c in rare.cards if c.contractor.synthetic]
    assert any(r.code == "SYNTHETIC" and not r.primary for r in synthetic.reasons)


@pytest.mark.parametrize("busy_price", [700_000, 900_000])
def test_busy_competitor_must_pass_other_filters_and_displace_a_card(contractor, match_request, busy_price):
    from matcher.reasons import assign

    catalogue = [replace(contractor, id=str(i), description="", price_from_kzt=price)
                 for i, price in enumerate((300_000, 400_000, 500_000))]
    catalogue.append(replace(contractor, id="busy", description="", price_from_kzt=busy_price,
                             busy_dates=frozenset({match_request.event_date})))
    result, scored, scorer = reason_input(catalogue, match_request)
    assigned = assign(result, scored, catalogue, scorer)
    assert all(r.family != ReasonFamily.AVAILABILITY for c in assigned.cards for r in c.reasons)


def test_absence_from_date_free_top_three_does_not_prove_single_competitor_replacement(contractor, match_request):
    from matcher.reasons import assign

    catalogue = [replace(contractor, id=str(i), description="", price_from_kzt=price,
                         busy_dates=frozenset({match_request.event_date}) if i < 2 else frozenset())
                 for i, price in enumerate((100_000, 200_000, 300_000, 400_000, 500_000, 600_000))]
    result, scored, scorer = reason_input(catalogue, match_request)
    assigned = assign(result, scored, catalogue, scorer)
    assert [c.contractor.id for c in assigned.cards] == ["2", "3", "4"]
    assert not any(r.code == "AVAILABILITY_REPLACEMENT" for r in assigned.cards[1].reasons)
    assert any(r.code == "AVAILABILITY_REPLACEMENT" for r in assigned.cards[2].reasons)


def test_zero_contribution_fact_qualifies_before_a_negative_ranking_factor(contractor, match_request):
    from matcher.reasons import assign

    catalogue = [replace(contractor, id="0", price_from_kzt=300_000,
                         languages=("русский", "казахский", "английский")),
                 replace(contractor, id="1", price_from_kzt=500_000, languages=("русский",))]
    result, scored, scorer = reason_input(catalogue, replace(match_request, budget_kzt=1_000_000))
    assigned = assign(result, scored, catalogue, scorer)
    # The second card has no positive phi. Semantic phi == 0 reaches its zero
    # threshold; the true 50% budget headroom has negative phi and does not.
    assert assigned.cards[1].reasons[0].code == "DESCRIPTION_ASPECT"
    assert assigned.cards[1].reasons[0].contribution == 0


def test_triple_search_trades_individual_utility_for_diversity_and_breaks_ties_by_code(contractor, match_request):
    from matcher.reasons import assign

    catalogue = [replace(contractor, id=str(i), description=f"Корпоративы ведущий {word}",
                         languages=("русский", "казахский", "английский"))
                 for i, word in enumerate(("один", "два", "три"))]
    result, scored, scorer = reason_input(catalogue, match_request)
    assigned = assign(result, scored, catalogue, scorer)
    # All phi and delta are zero. Each distinct quote has U=.10; common budget
    # and language facts have U=0. Three quotes score .30 - 3*.50 - 3*.10 = -1.50;
    # three different families score .10, with code strings deciding their order.
    assert [c.reasons[0].code for c in assigned.cards] == [
        "BUDGET_HEADROOM", "DESCRIPTION_ASPECT", "LANGUAGE_OPTIONS"]
    assert assigned.diversity_limited is False
