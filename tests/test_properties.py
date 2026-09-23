"""Domain properties exercised through public matching and reason seams."""
import csv
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st

from matcher import ranking, reasons
from matcher.data import load_contractors
from matcher.embeddings import EmbeddingScorer, content_key, request_text, split_sentences
from matcher.filtering import filter_pool
from matcher.lexical import LexicalScorer
from matcher.model import (
    CALENDAR_END, CALENDAR_START, CITIES, EVENT_FORMATS, LANGUAGES, MAX_CARDS,
    Contractor, MatchRequest, MatchResult, Outcome, ReasonFamily, RejectReason,
)
from matcher.service import run
from tests.test_embeddings import MODEL, write_cache


CATEGORIES = ("Ведущий", "Ведущий церемонии", "Флорист", "Декоратор", "Ресторан")
DAYS = st.dates(min_value=CALENDAR_START, max_value=CALENDAR_END)
PROPERTY = settings(max_examples=200, deadline=None)


def subset(values):
    return st.lists(st.sampled_from(values), min_size=1, max_size=len(values), unique=True).map(tuple)

@st.composite
def contractors(draw):
    """Valid generated profiles, including multilingual and multi-category rows."""
    return Contractor(
        id=draw(st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=12)),
        name=draw(st.text(alphabet="АБВабвXYZéҚқ", min_size=1, max_size=20)),
        categories=draw(subset(CATEGORIES)), city=draw(st.sampled_from(CITIES)),
        city_imputed=draw(st.booleans()), synthetic=True,
        price_from_kzt=draw(st.integers(min_value=1, max_value=5_000_000)),
        price_imputed=draw(st.booleans()), event_formats=draw(subset(EVENT_FORMATS)),
        languages=draw(subset(LANGUAGES)),
        max_hours=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=24))),
        busy_dates=frozenset(draw(st.sets(DAYS, max_size=25))),
        description=draw(st.one_of(
            st.sampled_from(("", "Корпоративы и свадьбы. Ведущий для компаний!",
                             'Флористика, букеты.\nЦветы "Алматы"?', "English business team")),
            st.text(alphabet=st.characters(exclude_categories=("Cs",)), max_size=200),
        )),
    )

REQUESTS = st.builds(
    MatchRequest, city=st.sampled_from(CITIES), event_date=DAYS,
    event_format=st.sampled_from(EVENT_FORMATS), category=st.sampled_from(CATEGORIES),
    budget_kzt=st.integers(min_value=1, max_value=5_000_000),
    duration_hours=st.one_of(st.none(), st.integers(min_value=1, max_value=24)),
    language=st.one_of(st.none(), st.sampled_from(LANGUAGES)),
)

@st.composite
def cases(draw, min_eligible=1):
    request = draw(REQUESTS)
    catalogue = draw(st.lists(contractors(), max_size=8, unique_by=lambda c: c.id))
    for index in range(draw(st.integers(min_value=min_eligible, max_value=6))):
        candidate = draw(contractors())
        # Construct eligible anchors instead of filtering most generated examples away.
        catalogue.append(replace(
            candidate, id=f"eligible-{index}", city=request.city,
            categories=tuple(dict.fromkeys((*candidate.categories, request.category))),
            event_formats=tuple(dict.fromkeys((*candidate.event_formats, request.event_format))),
            languages=tuple(dict.fromkeys((*candidate.languages, request.language)))
            if request.language else candidate.languages,
            price_from_kzt=draw(st.integers(min_value=1, max_value=request.budget_kzt)),
            max_hours=None if candidate.max_hours is None else max(candidate.max_hours, request.duration_hours or 1),
            busy_dates=candidate.busy_dates - {request.event_date},
        ))
    return catalogue, request


@PROPERTY
@given(cases())
def test_no_card_is_busy(case):
    catalogue, request = case
    result = run(request, catalogue, LexicalScorer())
    assert result.cards
    assert all(request.event_date not in card.contractor.busy_dates for card in result.cards)


@PROPERTY
@given(cases())
def test_every_card_starting_price_fits_budget(case):
    catalogue, request = case
    result = run(request, catalogue, LexicalScorer())
    assert result.cards
    assert all(card.contractor.price_from_kzt <= request.budget_kzt for card in result.cards)


@PROPERTY
@given(cases())
def test_every_card_supports_format_and_requested_language(case):
    catalogue, request = case
    result = run(request, catalogue, LexicalScorer())
    assert result.cards
    for card in result.cards:
        assert request.event_format in card.contractor.event_formats
        if request.language:
            assert request.language in card.contractor.languages


@PROPERTY
@given(cases())
def test_cards_are_sorted_and_capped(case):
    catalogue, request = case
    cards = run(request, catalogue, LexicalScorer()).cards
    assert 1 <= len(cards) <= MAX_CARDS
    keys = [(-card.score.total, card.contractor.id) for card in cards]
    assert all(left <= right for left, right in zip(keys, keys[1:]))
    assert [card.rank for card in cards] == list(range(1, len(cards) + 1))
    assert all(0 <= card.score.total <= 1 for card in cards)


@PROPERTY
@given(cases(min_eligible=0))
def test_run_is_deterministic_and_independent_of_catalogue_order(case):
    catalogue, request = case
    original = catalogue.copy()
    result = run(request, catalogue, LexicalScorer())
    assert result == run(request, catalogue, LexicalScorer())
    assert result == run(request, list(reversed(catalogue)), LexicalScorer())
    assert catalogue == original


@PROPERTY
@given(cases(min_eligible=0))
def test_pool_is_partitioned_into_eligible_and_rejected_profiles(case):
    catalogue, request = case
    result = run(request, catalogue, LexicalScorer())
    assert result.pool_size == result.eligible_count + len(result.rejections)
    assert result.pool_size == sum(c.city == request.city and request.category in c.categories for c in catalogue)
    rejected_ids = {rejection.contractor.id for rejection in result.rejections}
    assert not rejected_ids.intersection(card.contractor.id for card in result.cards)


@PROPERTY
@given(cases(min_eligible=0))
def test_outcome_agrees_with_counts(case):
    catalogue, request = case
    result = run(request, catalogue, LexicalScorer())
    if result.pool_size == 0:
        assert result.outcome == Outcome.NO_CATEGORY_IN_CITY and not result.cards
    elif result.eligible_count == 0:
        assert result.outcome == Outcome.NONE_ELIGIBLE and not result.cards
    else:
        assert result.outcome == Outcome.MATCHED
        assert len(result.cards) == min(MAX_CARDS, result.eligible_count)
    # A full triple needs no note: the rejections block already says who was left out.
    expects_note = len(result.cards) < MAX_CARDS
    assert (result.shortfall_note is not None) == expects_note


@PROPERTY
@given(contractors(), REQUESTS, st.integers(min_value=1, max_value=10_000))
def test_unspecified_max_hours_never_rejects_for_duration(candidate, request, hours):
    candidate = replace(candidate, max_hours=None, city=request.city, categories=(request.category,))
    with_hours = filter_pool([candidate], replace(request, duration_hours=hours))
    without_hours = filter_pool([candidate], replace(request, duration_hours=None))
    assert with_hours == without_hours
    pool, _, rejections = with_hours
    assert pool == [candidate]
    assert all(RejectReason.DURATION_EXCEEDS_MAX not in rejection.reasons for rejection in rejections)


@PROPERTY
@given(cases(min_eligible=4), st.booleans())
def test_booking_top_card_removes_it_without_reordering_survivors(case, embeddings):
    catalogue, request = case
    scorer = LexicalScorer()
    if embeddings:
        texts = [request_text(request), *(text for c in catalogue for text in [c.description, *split_sentences(c.description)])]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            write_cache(path, {text: [int(content_key(MODEL, text)[:6], 16) / 0xFFFFFF, 1.0] for text in texts},
                        anchors=(0.6, 1.0, len(texts) * len(catalogue)))
            scorer = EmbeddingScorer(cache_path=path, model=MODEL, dimensions=2, api_key="")
    before = run(request, catalogue, scorer)
    all_before = ranking.score_all(filter_pool(catalogue, request)[1], request, scorer)
    top_id = before.cards[0].contractor.id
    booked = [replace(c, busy_dates=c.busy_dates | {request.event_date}) if c.id == top_id else c for c in catalogue]
    after = run(request, booked, scorer)
    assert top_id not in {card.contractor.id for card in after.cards}
    assert [card.contractor.id for card in after.cards[:2]] == [card.contractor.id for card in before.cards[1:]]
    assert [card.score for card in after.cards[:2]] == [card.score for card in before.cards[1:]]
    assert after.eligible_count == before.eligible_count - 1
    all_after = ranking.score_all(filter_pool(booked, request)[1], request, scorer)
    assert [(c.contractor.id, c.score) for c in all_after] == [(c.contractor.id, c.score) for c in all_before if c.contractor.id != top_id]
    rejection, = [r for r in after.rejections if r.contractor.id == top_id]
    assert rejection.reasons == (RejectReason.BUSY_ON_DATE,)


@PROPERTY
@given(st.lists(contractors(), max_size=8, unique_by=lambda c: c.id))
def test_loader_round_trip(catalogue):
    scratch = Path(__file__).resolve().parents[1] / ".work"
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=scratch) as directory:
        path = Path(directory) / "contractors.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=(
                "id", "anon_name", "categories", "city", "city_imputed", "synthetic", "price_from_kzt",
                "price_imputed", "event_formats", "languages", "max_hours", "busy_dates", "description",
            ))
            writer.writeheader()
            for c in catalogue:
                writer.writerow({
                    "id": c.id, "anon_name": c.name, "categories": "|".join(c.categories), "city": c.city,
                    "city_imputed": str(c.city_imputed), "synthetic": str(c.synthetic),
                    "price_from_kzt": str(c.price_from_kzt), "price_imputed": str(c.price_imputed),
                    "event_formats": "|".join(c.event_formats), "languages": "|".join(c.languages),
                    "max_hours": "" if c.max_hours is None else str(c.max_hours),
                    "busy_dates": "|".join(day.isoformat() for day in sorted(c.busy_dates)),
                    "description": c.description,
                })
        assert load_contractors(path) == sorted(catalogue, key=lambda c: c.id)


@PROPERTY
@given(cases())
def test_assign_preserves_ranked_cards_and_emits_deterministic_grounded_reasons(case):
    catalogue, request = case
    scorer = LexicalScorer()
    pool, eligible, rejected = filter_pool(catalogue, request)
    scored = ranking.score_all(eligible, request, scorer)
    original = MatchResult(request, Outcome.MATCHED, scored[:MAX_CARDS], rejected,
                           len(pool), len(eligible), None, scorer.name)
    assigned = reasons.assign(original, scored, catalogue, scorer)
    assert assigned == reasons.assign(original, scored, catalogue, scorer)
    assert assigned == reasons.assign(original, tuple(reversed(scored)), list(reversed(catalogue)), scorer)
    assert assigned == reasons.assign(assigned, scored, catalogue, scorer)
    assert tuple(replace(c, reasons=()) for c in assigned.cards) == original.cards
    allowed_primary = {
        "BUDGET_HEADROOM", "BUDGET_LOWER_THAN_SHOWN", "BUDGET_FITS", "LANGUAGE_REQUEST_MATCH",
        "LANGUAGE_UNIQUE_IN_SHOWN", "LANGUAGE_OPTIONS", "DURATION_HEADROOM", "DURATION_MAX_IN_SHOWN",
        "DURATION_NOT_APPLICABLE", "DESCRIPTION_ASPECT", "DESCRIPTION_CLOSEST_IN_SHOWN",
        "AVAILABILITY_REPLACEMENT", "AVAILABILITY_ONLY_FREE",
    }
    for card in assigned.cards:
        assert card.reasons and card.reasons[0].primary
        assert sum(r.primary for r in card.reasons) == 1
        assert card.reasons[0].code in allowed_primary
        substantive = [r for r in card.reasons if r.family != ReasonFamily.DATA_QUALITY]
        assert len(substantive) <= 3
        assert len({r.family for r in substantive}) == len(substantive)
        for reason in card.reasons:
            assert all(isinstance(value, str) for value in reason.evidence.values())
            if "quote" in reason.evidence:
                quote = reason.evidence["quote"]
                assert quote and len(quote) <= 120 and quote in card.contractor.description
        assert {r.code for r in card.reasons if r.family == ReasonFamily.DATA_QUALITY} == {
            flag.upper() for flag in card.caveats}
    assert assigned.diversity_limited == (len({c.reasons[0].code for c in assigned.cards}) < len(assigned.cards))
