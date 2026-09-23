"""Small composition boundary; collaborators are imported only when needed."""
from functools import lru_cache
from pathlib import Path
from time import perf_counter

from matcher.api_types import to_dto
from matcher.model import (
    Contractor,
    Explanation,
    MatchRequest,
    MatchResult,
    SemanticScorer,
)

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "contractors.csv"


class RequestError(ValueError):
    """Stable adapter error, also usable before the filtering module is installed."""


def request_error_type() -> type[Exception]:
    try:
        from matcher.filtering import RequestError as FilteringRequestError
        return FilteringRequestError
    except ImportError:
        return RequestError


@lru_cache(maxsize=1)
def get_contractors() -> list[Contractor]:
    from matcher.data import load_contractors
    return load_contractors(DATA_PATH)


def choose_scorer() -> SemanticScorer:
    try:
        from matcher.embeddings import EmbeddingScorer
        return EmbeddingScorer()
    except semantic_error_types():
        return lexical_scorer()


def semantic_error_types() -> tuple[type[Exception], ...]:
    try:
        from matcher.embeddings import SemanticUnavailable
        return ImportError, SemanticUnavailable
    except ImportError:
        return (ImportError,)


def lexical_scorer() -> SemanticScorer:
    from matcher.lexical import LexicalScorer
    return LexicalScorer()


def run(request: MatchRequest, contractors: list[Contractor], scorer: SemanticScorer) -> MatchResult:
    from matcher.service import run as match
    return match(request, contractors, scorer)


def explain(result: MatchResult) -> tuple[Explanation, ...]:
    from matcher.explain import get_explainer
    return get_explainer().explain(result)


def answer(request: MatchRequest) -> dict:
    started = perf_counter()
    scorer = choose_scorer()
    contractors = get_contractors()
    try:
        try:
            result = run(request, contractors, scorer)
        except semantic_error_types():
            if scorer.name != "embeddings":
                raise
            scorer = lexical_scorer()
            result = run(request, contractors, scorer)
    except (RequestError, request_error_type()) as exc:
        raise RequestError(str(exc)) from exc
    explanations = explain(result)
    return to_dto(result, explanations, round((perf_counter() - started) * 1000, 2), scorer.name)
