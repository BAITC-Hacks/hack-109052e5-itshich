"""Shared domain types. This module is the contract between data loading,
filtering, ranking, explanation and the web layer. Keep it dependency-free.

Design rule: code decides WHO is shown and in WHAT ORDER; the LLM only turns
already-computed facts (CardFacts) into 1-2 sentences. Ties are broken by id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal, Protocol

# Calendar window for which busy_dates are known (100 days inclusive).
CALENDAR_START = date(2026, 9, 23)
CALENDAR_END = date(2026, 12, 31)

CITIES: tuple[str, ...] = ("Алматы", "Астана", "Зарубежье")
EVENT_FORMATS: tuple[str, ...] = (
    "свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения",
)
LANGUAGES: tuple[str, ...] = ("русский", "казахский", "английский")

MAX_CARDS = 3


@dataclass(frozen=True)
class Contractor:
    id: str
    name: str
    categories: tuple[str, ...]
    city: str
    city_imputed: bool
    synthetic: bool
    price_from_kzt: int
    price_imputed: bool
    event_formats: tuple[str, ...]
    languages: tuple[str, ...]
    max_hours: int | None  # None => work is not tied to on-site presence
    busy_dates: frozenset[date]
    description: str


@dataclass(frozen=True)
class MatchRequest:
    city: str
    event_date: date
    event_format: str
    category: str
    budget_kzt: int
    duration_hours: int | None = None
    language: str | None = None


class RejectReason(str, Enum):
    """Why a contractor of the right category+city was excluded.
    Order matters: reasons are reported in this order."""
    BUSY_ON_DATE = "busy_on_date"
    OVER_BUDGET = "over_budget"  # price_from_kzt > budget_kzt ("from" price already exceeds budget)
    FORMAT_NOT_SUPPORTED = "format_not_supported"
    LANGUAGE_NOT_SUPPORTED = "language_not_supported"  # only when language requested
    DURATION_EXCEEDS_MAX = "duration_exceeds_max"  # only when duration requested AND max_hours is not None


@dataclass(frozen=True)
class Rejection:
    contractor: Contractor
    reasons: tuple[RejectReason, ...]  # ALL failing reasons, in RejectReason order


class Outcome(str, Enum):
    MATCHED = "matched"  # 1..3 cards
    NO_CATEGORY_IN_CITY = "no_category_in_city"  # category pool in this city is empty
    NONE_ELIGIBLE = "none_eligible"  # pool non-empty, every candidate rejected


@dataclass(frozen=True)
class ScoreBreakdown:
    """All components in [0, 1]; total = weighted sum rounded to 4 decimals.
    Weights (documented in README): budget 0.35, semantic 0.35,
    language 0.10, duration 0.10, data_quality 0.10."""
    budget_fit: float
    semantic: float
    language_fit: float
    duration_fit: float
    data_quality: float
    total: float


DurationNote = Literal["fits", "not_applicable", "not_requested"]

# Score feature keys, in weight order. ranking.weights_for(request) returns a
# dict with exactly these keys; ScoreBreakdown holds the feature values x_f.
FEATURES: tuple[str, ...] = ("budget_fit", "semantic", "language_fit", "duration_fit", "data_quality")


class ReasonFamily(str, Enum):
    BUDGET = "budget"
    FORMAT = "format"
    LANGUAGE = "language"
    DURATION = "duration"
    DESCRIPTION = "description_semantic"
    AVAILABILITY = "availability_contrast"
    DATA_QUALITY = "data_quality_caveat"


@dataclass(frozen=True)
class Reason:
    """One code-derived, verifiable reason why a card is shown (or a caveat).
    code examples: BUDGET_HEADROOM, BUDGET_LOWER_THAN_SHOWN, LANGUAGE_REQUEST_MATCH,
    LANGUAGE_UNIQUE_IN_SHOWN, DURATION_HEADROOM, DURATION_NOT_APPLICABLE,
    DESCRIPTION_ASPECT, AVAILABILITY_REPLACEMENT, PRICE_IMPUTED, SYNTHETIC ...
    evidence: every number/name/quote the text may use for this reason,
    e.g. {"price": "900 000 ₸", "budget": "2 000 000 ₸", "headroom_pct": "55",
    "competitor": "Кики", "date": "05.10.2026", "quote": "..."}.
    contribution: w_f·(x_if − mean_f(eligible)) for score-based reasons, else 0.
    primary: True for the single reason chosen as the card's headline."""
    code: str
    family: ReasonFamily
    evidence: dict[str, str]
    contribution: float = 0.0
    primary: bool = False


@dataclass(frozen=True)
class CardFacts:
    """Everything the explainer is allowed to say about a card. No other
    source of truth for explanations exists."""
    contractor: Contractor
    rank: int  # 1-based position in the result
    score: ScoreBreakdown
    budget_kzt: int
    budget_headroom_pct: int  # round((budget - price_from) / budget * 100)
    format_matched: str  # the requested event format (guaranteed supported)
    requested_language: str | None
    languages_matched: tuple[str, ...]  # contractor languages ∩ {requested} (or all languages if none requested)
    requested_hours: int | None
    duration_note: DurationNote
    semantic_snippet: str | None  # sentence from description most relevant to the request
    semantic_score: float  # rounded to 3 decimals
    free_on_date: date  # the requested date (guaranteed not in busy_dates)
    caveats: tuple[str, ...]  # subset of: "price_imputed", "city_imputed", "synthetic"
    reasons: tuple[Reason, ...] = ()  # filled by matcher.reasons; primary first


@dataclass(frozen=True)
class MatchResult:
    request: MatchRequest
    outcome: Outcome
    cards: tuple[CardFacts, ...]  # len <= MAX_CARDS, sorted by (-score.total, id)
    rejections: tuple[Rejection, ...]  # sorted by id
    pool_size: int  # contractors with this category in this city
    eligible_count: int  # pool_size - len(rejections)
    shortfall_note: str | None  # Russian, code-generated: why fewer than 3 (or none)
    semantic_backend: Literal["embeddings", "lexical"]
    diversity_limited: bool = False  # cards' primary reasons could not all be made distinct


@dataclass(frozen=True)
class Explanation:
    contractor_id: str
    text: str  # 1-2 sentences, Russian
    source: Literal["llm", "template"]


class SemanticScorer(Protocol):
    """Pluggable relevance of description to request. Must be deterministic
    for identical inputs. Implementations: LexicalScorer (no network),
    EmbeddingScorer (OpenAI embeddings behind a content-hash cache)."""

    name: Literal["embeddings", "lexical"]

    def score(self, request: MatchRequest, contractors: list[Contractor]) -> dict[str, float]:
        """contractor.id -> similarity in [0, 1], rounded to 3 decimals."""
        ...

    def snippet(self, request: MatchRequest, contractor: Contractor) -> str | None:
        """Most relevant sentence of the description (<= 200 chars) or None."""
        ...
