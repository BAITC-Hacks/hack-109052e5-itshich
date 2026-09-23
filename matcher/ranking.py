"""Deterministic scoring and grounded card facts for eligible contractors."""
from __future__ import annotations

from dataclasses import replace

from matcher.model import FEATURES, MAX_CARDS, CardFacts, Contractor, MatchRequest, ScoreBreakdown, SemanticScorer


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def weights_for(request: MatchRequest) -> dict[str, float]:
    """Feature weights used by total(). Keys == FEATURES. Owner: weights research
    (per-category profiles land here); matcher.reasons reads contributions from it."""
    return {"budget_fit": 0.35, "semantic": 0.35, "language_fit": 0.10,
            "duration_fit": 0.10, "data_quality": 0.10}


def rank(
    eligible: list[Contractor], request: MatchRequest, scorer: SemanticScorer,
) -> tuple[CardFacts, ...]:
    """Top MAX_CARDS of score_all()."""
    return score_all(eligible, request, scorer)[:MAX_CARDS]


def score_all(
    eligible: list[Contractor], request: MatchRequest, scorer: SemanticScorer,
) -> tuple[CardFacts, ...]:
    """Every eligible contractor as CardFacts, sorted by (-total, id), rank 1..n."""
    weights = weights_for(request)
    semantics = scorer.score(request, eligible)
    cards = []
    for contractor in eligible:
        budget = _clamp(1 - contractor.price_from_kzt / request.budget_kzt)
        semantic = semantics[contractor.id]
        language = 1.0 if request.language is not None else len(contractor.languages) / 3
        if request.duration_hours is None:
            duration, duration_note = 0.5, "not_requested"
        elif contractor.max_hours is None:
            duration, duration_note = 0.5, "not_applicable"
        else:
            duration = _clamp(1 - request.duration_hours / contractor.max_hours)
            duration_note = "fits"
        quality = (1 - 0.5 * contractor.price_imputed - 0.25 * contractor.city_imputed
                   - 0.25 * contractor.synthetic)
        values = dict(zip(FEATURES, (budget, semantic, language, duration, quality)))
        score = ScoreBreakdown(
            budget, semantic, language, duration, quality,
            round(sum(weights[f] * values[f] for f in FEATURES), 4),
        )
        cards.append(CardFacts(
            contractor=contractor, rank=len(cards) + 1, score=score,
            budget_kzt=request.budget_kzt,
            budget_headroom_pct=round((request.budget_kzt - contractor.price_from_kzt)
                                      / request.budget_kzt * 100),
            format_matched=request.event_format, requested_language=request.language,
            languages_matched=(request.language,) if request.language is not None else contractor.languages,
            requested_hours=request.duration_hours, duration_note=duration_note,
            semantic_snippet=scorer.snippet(request, contractor), semantic_score=semantic,
            free_on_date=request.event_date,
            caveats=tuple(flag for flag in ("price_imputed", "city_imputed", "synthetic")
                          if getattr(contractor, flag)),
        ))
    cards.sort(key=lambda card: (-card.score.total, card.contractor.id))
    return tuple(replace(card, rank=index) for index, card in enumerate(cards, 1))
