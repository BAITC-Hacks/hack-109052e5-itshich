"""Validated JSON boundary; domain facts and rank order pass through unchanged."""
from dataclasses import asdict
from datetime import date
from typing import Literal

from pydantic import BaseModel

from matcher.model import (
    DurationNote,
    Explanation,
    MatchRequest,
    MatchResult,
    Outcome,
    RejectReason,
)

SemanticBackend = Literal["embeddings", "nvidia", "lexical"]
ExplanationSource = Literal["llm", "template"]
OUTCOME_TITLES = {
    Outcome.MATCHED: "Подобрали",
    Outcome.NO_CATEGORY_IN_CITY: "В этом городе такой категории нет",
    Outcome.NONE_ELIGIBLE: "Кандидаты есть, но ни один не проходит по условиям",
}
REASON_LABELS = {
    RejectReason.BUSY_ON_DATE: "занят на эту дату",
    RejectReason.OVER_BUDGET: "цена от выше бюджета",
    RejectReason.FORMAT_NOT_SUPPORTED: "не берёт этот формат",
    RejectReason.LANGUAGE_NOT_SUPPORTED: "не работает на этом языке",
    RejectReason.DURATION_EXCEEDS_MAX: "максимум часов меньше запрошенной длительности",
}


class MatchRequestDTO(BaseModel):
    city: str
    event_date: date
    event_format: str
    category: str
    budget_kzt: int
    duration_hours: int | None = None
    language: str | None = None

    def to_domain(self) -> MatchRequest:
        return MatchRequest(**self.model_dump())


class ScoreDTO(BaseModel):
    budget_fit: float
    semantic: float
    language_fit: float
    duration_fit: float
    data_quality: float
    total: float


class FactsDTO(BaseModel):
    budget_headroom_pct: int
    format_matched: str
    languages_matched: tuple[str, ...]
    requested_language: str | None
    requested_hours: int | None
    max_hours: int | None
    duration_note: DurationNote
    semantic_snippet: str | None
    semantic_score: float
    free_on_date: date
    caveats: tuple[str, ...]
    score: ScoreDTO


class CardDTO(BaseModel):
    id: str
    name: str
    category: str
    city: str
    price_from_kzt: int
    price_imputed: bool
    city_imputed: bool
    synthetic: bool
    explanation: str
    explanation_source: ExplanationSource
    facts: FactsDTO


class ReasonDTO(BaseModel):
    code: RejectReason
    label: str


class RejectionDTO(BaseModel):
    id: str
    name: str
    reasons: tuple[ReasonDTO, ...]


class MatchResponseDTO(BaseModel):
    outcome: Outcome
    outcome_title_ru: str
    shortfall_note: str | None
    cards: tuple[CardDTO, ...]
    rejections: tuple[RejectionDTO, ...]
    pool_size: int
    eligible_count: int
    semantic_backend: SemanticBackend
    timing_ms: float


class MetaDTO(BaseModel):
    cities: list[str]
    categories: list[str]
    formats: list[str]
    languages: list[str]
    calendar_start: date
    calendar_end: date
    counts: dict[str, dict[str, int]]


def to_dto(result: MatchResult, explanations: tuple[Explanation, ...],
           timing_ms: float, backend: SemanticBackend) -> dict:
    explanations_by_id = {item.contractor_id: item for item in explanations}
    cards = []
    for facts in result.cards:
        contractor = facts.contractor
        explanation = explanations_by_id[contractor.id]
        cards.append(CardDTO(
            id=contractor.id, name=contractor.name, category=result.request.category,
            city=contractor.city, price_from_kzt=contractor.price_from_kzt,
            price_imputed=contractor.price_imputed, city_imputed=contractor.city_imputed,
            synthetic=contractor.synthetic, explanation=explanation.text,
            explanation_source=explanation.source,
            facts=FactsDTO(
                budget_headroom_pct=facts.budget_headroom_pct,
                format_matched=facts.format_matched, languages_matched=facts.languages_matched,
                requested_language=facts.requested_language, requested_hours=facts.requested_hours,
                max_hours=contractor.max_hours, duration_note=facts.duration_note,
                semantic_snippet=facts.semantic_snippet, semantic_score=facts.semantic_score,
                free_on_date=facts.free_on_date, caveats=facts.caveats,
                score=ScoreDTO(**asdict(facts.score)),
            ),
        ))
    return MatchResponseDTO(
        outcome=result.outcome, outcome_title_ru=OUTCOME_TITLES[result.outcome],
        shortfall_note=result.shortfall_note, cards=tuple(cards),
        rejections=tuple(RejectionDTO(
            id=item.contractor.id, name=item.contractor.name,
            reasons=tuple(ReasonDTO(code=reason, label=REASON_LABELS[reason])
                          for reason in item.reasons),
        ) for item in result.rejections),
        pool_size=result.pool_size, eligible_count=result.eligible_count,
        semantic_backend=backend, timing_ms=timing_ms,
    ).model_dump(mode="json")
