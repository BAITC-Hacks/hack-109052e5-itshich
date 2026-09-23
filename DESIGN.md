# DESIGN — Smart contractor matching (#79-lite)

Priorities (from the brief): quality of explanations > honest handling of rare /
booked / empty categories > speed > UI. Determinism and reproducibility are hard
requirements.

## Module map (deep modules, narrow interfaces)

```
matcher/model.py       shared types (Contractor, MatchRequest, CardFacts, MatchResult, ...)  [owner: orchestrator, frozen]
matcher/data.py        load_contractors(path) -> list[Contractor]                                [alpha]
matcher/filtering.py   filter_pool(contractors, request) -> (pool, eligible, rejections)         [alpha]
matcher/ranking.py     rank(eligible, request, scorer) -> tuple[CardFacts, ...]                  [alpha]
matcher/lexical.py     LexicalScorer (SemanticScorer, no network)                                [alpha]
matcher/service.py     run(request, contractors, scorer) -> MatchResult  (+ shortfall_note)      [alpha]
matcher/embeddings.py  EmbeddingScorer (OpenAI embeddings, file cache keyed by content hash)     [bravo]
matcher/explain.py     TemplateExplainer, LLMExplainer, validate_explanations, get_explainer()   [bravo]
matcher/pipeline.py    answer(request) -> ResponseDTO (loads data once, picks scorer/explainer)  [charlie]
app.py                 FastAPI: GET /, POST /api/match, GET /api/demo, GET /api/meta            [charlie]
web/index.html         single page, vanilla JS, shows 3 outcomes distinctly                      [charlie]
demo/queries.json      reproducible demo requests on real data                                   [alpha]
scripts/               build_embeddings.py [bravo], run_demo.py [charlie], find_demo_queries.py [alpha]
tests/                 test_data, test_filtering, test_ranking, test_properties [alpha];
                       test_explain, test_embeddings [bravo]; test_api, e2e/test_browser [charlie]
```

Nothing else: no DB, no vector store, no auth, no booking.

## Data (matcher/data.py)

- Source: `data/contractors.csv` (66 rows). Columns: id, anon_name, categories,
  city, city_imputed, synthetic, price_from_kzt, price_imputed, event_formats,
  languages, max_hours, busy_dates, description. List fields are `|`-separated.
  Booleans are `True`/`False`. `max_hours` empty => `None`. Dates ISO `YYYY-MM-DD`.
- Optional extra file `data/synthetic_extra.csv` (same schema) is loaded when
  present; every row there MUST have synthetic=True (loader raises otherwise).
- Loader is strict: unknown city / format / language / malformed date / non-int
  price => `DataError` with row id. Output sorted by id.
- All dates in busy_dates are inside [CALENDAR_START, CALENDAR_END] (assert).

## Filtering (matcher/filtering.py)

Input validation (raise `RequestError(message_ru)` — the API returns 422 with
that text; this is NOT one of the three outcomes):
- event_date outside [2026-09-23, 2026-12-31]: "Календарь занятости известен
  только на 23.09.2026 — 31.12.2026, подбор на {date} невозможен."
- city not in CITIES; event_format not in EVENT_FORMATS; language not in
  LANGUAGES; budget_kzt <= 0; duration_hours <= 0.
- Unknown category string is NOT an error: pool is empty => NO_CATEGORY_IN_CITY.

Pool = contractors where `request.category in categories and city == request.city`.
For each pool member compute ALL failing reasons (RejectReason order):
- BUSY_ON_DATE: event_date in busy_dates
- OVER_BUDGET: price_from_kzt > budget_kzt  (price is a "from" price: passing
  this check means "the starting price fits", never "the final price fits")
- FORMAT_NOT_SUPPORTED: event_format not in event_formats
- LANGUAGE_NOT_SUPPORTED: language requested and not in languages
- DURATION_EXCEEDS_MAX: duration requested and max_hours is not None and
  max_hours < duration_hours. max_hours None => never a reason (not applicable).
Eligible = pool members with zero reasons. Rejections sorted by id.

## Ranking (matcher/ranking.py)

Score each eligible contractor; components in [0,1]:
- budget_fit = clamp(1 - price_from / budget, 0, 1)
- semantic = scorer.score(...)[id]  (already rounded to 3 decimals)
- language_fit: if language requested => 1.0 (it is a hard filter) plus nothing;
  if not requested => len(languages)/3 (more languages = more flexible)
- duration_fit: if hours requested and max_hours not None =>
  clamp(1 - hours/max_hours, 0, 1) (headroom); if max_hours None => 0.5;
  if not requested => 0.5
- data_quality = 1 - 0.5*price_imputed - 0.25*city_imputed - 0.25*synthetic
- total = round(0.35*budget_fit + 0.35*semantic + 0.10*language_fit
              + 0.10*duration_fit + 0.10*data_quality, 4)
Order: `sorted(key=(-total, id))`. Take first MAX_CARDS. Build CardFacts:
budget_headroom_pct = round((budget - price)/budget*100); languages_matched =
(language,) if requested else contractor.languages; duration_note = "fits" /
"not_applicable" (max_hours None) / "not_requested"; semantic_snippet from
scorer.snippet(); caveats from flags.

## Service (matcher/service.py)

`run(request, contractors, scorer) -> MatchResult`. Outcome rules:
- pool_size == 0 => NO_CATEGORY_IN_CITY, shortfall_note: "В городе {city}
  нет подрядчиков категории «{category}». Ближайшие варианты: категории с
  похожим назначением в этом городе: ..." (list up to 3 categories present in
  the city; if category exists in another city, say so: "Категория есть в
  {other city}: N профилей.")
- pool_size > 0 and eligible == 0 => NONE_ELIGIBLE, shortfall_note summarising
  rejections by reason with counts and names, e.g. "Кандидаты в категории есть
  (5), но ни один не проходит: 3 заняты 14.11.2026 (Имя1, Имя2, Имя3), 2 дороже
  бюджета (цена от 1 200 000 ₸ при бюджете 800 000 ₸)."
- else MATCHED; if len(cards) < 3, shortfall_note explains why fewer: pool size
  and rejection counts by reason ("Показано 2 из 3: в категории 3 профиля, 1
  занят на 14.11.2026 (Имя).") If pool itself is < 3: "в городе всего N
  профилей этой категории".
Numbers formatted with a thin space thousands separator and "₸".

## Semantic scorers

Request text for both scorers: f"{event_format} {category} {city}" plus
language word if requested. Both must be deterministic and return values
rounded to 3 decimals.

LexicalScorer (alpha): keyword dictionary per format/category (e.g. свадьба:
["свадьб", "невест", "молодожён", "загс", "wedding"], корпоратив:
["корпоратив", "компани", "тимбилдинг", "team", "бизнес"], конференция:
["конференц", "форум", "спикер", "модератор", "делов"], юбилей: ["юбил"],
"день рождения": ["день рожд", "именин", "birthday", "детск"], той: ["той",
"беташар", "кыз узату", "сундет", "национальн", "казах"]; english language:
["английск", "english", "international", "международн"]; kazakh: ["казахск",
"қазақ"]). score = min(1, hits/4) over stemmed substrings, case-insensitive;
snippet = sentence (split on [.!?\n]) with the most hits, else None.

EmbeddingScorer (bravo): OpenAI `text-embedding-3-large` (env
EMBEDDING_MODEL), 1024 dimensions by default (env EMBEDDING_DIMENSIONS, passed
as `dimensions` to the API). Cache: `data/embeddings.json` =
{"model": ..., "dimensions": ..., "vectors":
{sha256(model + "\n" + str(dimensions) + "\n" + text): [floats]}}.
A model/dimensions mismatch is an empty cache; the builder rewrites it.
Vectors are rounded to 6 decimals and stored as compact JSON. The shipped
cache contains 392 vectors for 78 contractors and demo queries. Cosine divides
by both vector norms even when API vectors are not exactly unit length.
Texts embedded: full description per contractor AND each sentence of the description (for snippets). Query text
embedded at request time and cached in the same file (write-through; if the
file is read-only or missing key, keep in memory). Score = cosine mapped to
[0,1] via (cos+1)/2, rounded to 3 decimals. Snippet = sentence with highest
cosine, <= 200 chars. `scripts/build_embeddings.py` precomputes the cache for
all contractors so the repo ships with it and ranking reproduces WITHOUT a key.
If OPENAI_API_KEY is missing and a needed vector is not cached =>
raise `SemanticUnavailable`; pipeline then falls back to LexicalScorer and
reports semantic_backend="lexical".

## Explanations (matcher/explain.py)

`Explainer.explain(result: MatchResult) -> tuple[Explanation, ...]` (one per
card, same order). Two implementations:
- TemplateExplainer: deterministic Russian text from CardFacts: price vs budget
  ("цена от 600 000 ₸ укладывается в бюджет 800 000 ₸ с запасом 25 %"),
  format, language (only if requested or notable), duration ("до 8 ч при
  запрошенных 5 ч" / "работа не привязана к присутствию на площадке"), snippet
  quote, caveats ("цена проставлена при подготовке датасета", "профиль
  синтетический"), and free-on-date ("свободен 14.11.2026"). Vary sentence
  structure by rank so cards are not clones. 1-2 sentences, <= 350 chars.
- LLMExplainer: OpenAI chat (env LLM_MODEL, default "gpt-5.4-mini"),
  temperature 0, seed 42, JSON output {"explanations":[{"id","text"}]}. The
  prompt contains ONLY: the request, the CardFacts of the shown cards (as
  compact JSON incl. description snippet, NOT the full catalogue), and the
  rejection summary. System prompt rules: Russian; 1-2 sentences per card;
  cite concrete numbers (price, budget, hours, date); mention what makes THIS
  card different from the other shown cards; no generic praise; do not invent
  facts not present in the JSON; do not reorder.
- `validate_explanations(result, texts) -> list[str]` (problems; empty => ok):
  count == len(cards); each 1-2 sentences (split on [.!?]), 40..350 chars;
  contains at least two grounded facts from its CardFacts (price digits,
  budget digits, headroom %, hours, date "14.11.2026" / "14 ноября", format
  word, a language word, 3+ consecutive words from the snippet); no banned
  phrases (case-insensitive substrings): "отличный выбор", "идеально подойдёт",
  "идеально подходит", "прекрасный вариант", "лучший выбор", "не пожалеете",
  "профессионал своего дела", "высокое качество", "индивидуальный подход";
  pairwise word-set Jaccard similarity between texts < 0.6; no digits that are
  not present in that card's facts (numbers must be grounded).
- `get_explainer()` picks LLMExplainer when OPENAI_API_KEY is set, else
  TemplateExplainer. LLMExplainer itself: on API error, timeout (8 s) or
  validation failure => fall back to TemplateExplainer for the whole result and
  mark source="template". Responses cached in-process by
  sha256(request + card ids) so a repeated request returns identical text.

## Web / API (charlie)

- `POST /api/match` body: {city, event_date (ISO), event_format, category,
  budget_kzt, duration_hours?, language?}. 200 => {outcome, outcome_title_ru,
  shortfall_note, cards:[{id,name,category,city,price_from_kzt,price_imputed,
  city_imputed,synthetic,explanation,explanation_source,facts:{...}}],
  rejections:[{id,name,reasons:[...]}], pool_size, eligible_count,
  semantic_backend, timing_ms}. 422 => {detail: message_ru} for RequestError.
- `GET /api/meta` => cities, categories (sorted), formats, languages, calendar
  window, counts per category per city (drives dropdowns).
- `GET /api/demo` => demo/queries.json.
- `GET /` => web/index.html. The page: form with dropdowns, "Демо-запросы"
  buttons, result area with a big outcome banner in 3 distinct styles/colours
  (matched / no category in city / none eligible), cards showing name, category,
  city, "от N ₸", badges (synthetic / price_imputed / city_imputed), the
  explanation, and a collapsible "Почему не попали" list of rejections with
  reasons in Russian. Show semantic backend and timing in a footer line.
- Determinism guard: the page never reorders cards.

## Demo requests (demo/queries.json)

Each: {name, request, expected_outcome, note}. Must be found on REAL data by
`scripts/find_demo_queries.py` and asserted in tests:
1. dense: Ведущий / Алматы / корпоратив / autumn date with >= 5 eligible.
2. rare: Флорист or Декоратор / Алматы.
3. empty A (NO_CATEGORY_IN_CITY): e.g. Декоратор / Астана.
4. empty B (NONE_ELIGIBLE): pool exists but all rejected (budget or busy).
5. date pair: same request on two dates where card set differs because a
   top card is busy on the second date.

## Tests

- TDD (tests/): loader (66 rows, flags, None hours, dates in window); filter
  (each reason, price "from" boundary price == budget passes, max_hours None
  never rejects, date-window validation); ranking (tie => id order, budget
  headroom ordering, stable across runs); service outcomes (three outcomes and
  shortfall notes); explain (validator accepts template output for every demo
  query, rejects generic/ungrounded text, LLM fallback on bad JSON); API (3
  outcomes, 422 text, determinism: two calls identical).
- Property-based (Hypothesis, tests/test_properties.py) with a contractor
  strategy generating synthetic profiles: (1) no card is busy on the date; (2)
  every card's price_from <= budget; (3) every card supports the format; (4)
  cards sorted by (-total, id) and len <= 3; (5) run() is deterministic
  (same inputs => equal results); (6) pool_size == eligible_count +
  len(rejections); (7) outcome consistent with counts; (8) a contractor with
  max_hours None is never rejected for duration; (9) adding a busy date for
  the top card removes it from cards and never changes the relative order of
  the remaining ones; (10) loader round-trip: rows from the strategy written
  as CSV load back equal.

## Conventions

Python 3.12+, `uv run` for everything, stdlib + fastapi + openai + hypothesis.
Frozen dataclasses. No global mutable state except explicit caches. Russian
user-facing strings; English code and comments. Every module <= ~250 lines.

## Reasons: "why this card" is computed by code (matcher/reasons.py)

Source: docs/research/02-reasons-codex.md + team decisions. The LLM never
decides reasons; it receives reason codes with evidence and only phrases them.

Hooks already in place: `ranking.score_all(eligible, request, scorer)` returns
CardFacts for EVERY eligible contractor (rank 1..n, same order rule);
`ranking.weights_for(request)` returns the weights dict (keys == model.FEATURES);
`filtering.filter_pool(..., ignore_date=True)` drops the busy check.
Types: `model.Reason`, `model.ReasonFamily`, `CardFacts.reasons`,
`MatchResult.diversity_limited`.

### Contributions (exact, linear score)
For card i and feature f: `phi_if = w_f * (x_if - mean_f(eligible))` where the
mean is over ALL eligible (score_all), not just the top-3. Pairwise contrast
against every other shown card j and against the best non-shown eligible k:
`delta_ij_f = w_f * (x_if - x_jf)`. Round to 4 decimals.

### Taxonomy (codes -> evidence keys)
| family | code | when | evidence |
| --- | --- | --- | --- |
| budget | BUDGET_HEADROOM | phi_budget > 0 or headroom_pct >= 30 | price, budget, headroom_pct |
| budget | BUDGET_LOWER_THAN_SHOWN | cheapest among shown (unique) | price, next_price, diff_pct |
| budget | BUDGET_FITS | always true for eligible; used only when nothing stronger | price, budget |
| format | FORMAT_SUPPORTED | always (eligibility); never primary | format |
| language | LANGUAGE_REQUEST_MATCH | language requested | language |
| language | LANGUAGE_UNIQUE_IN_SHOWN | only shown card with lang L (L not requested) | language |
| language | LANGUAGE_OPTIONS | 3 languages, no request | languages |
| duration | DURATION_HEADROOM | hours requested, max_hours not None, phi_duration > 0 | requested_hours, max_hours |
| duration | DURATION_MAX_IN_SHOWN | largest max_hours among shown (unique) | max_hours |
| duration | DURATION_NOT_APPLICABLE | max_hours None (never primary unless nothing else) | — |
| description_semantic | DESCRIPTION_ASPECT | phi_semantic > 0 or snippet exists; must carry a verbatim quote | quote (<= 120 chars, substring of description), semantic_score |
| description_semantic | DESCRIPTION_CLOSEST_IN_SHOWN | highest semantic among shown (unique) | quote |
| availability_contrast | AVAILABILITY_REPLACEMENT | i in top3(on date) and i not in top3(ignore date); competitor j in top3(ignore date), j busy on date, and i not in top3(eligible_on_date + [j]) | competitor, date |
| availability_contrast | AVAILABILITY_ONLY_FREE | pool > 1 and eligible == 1 and >= 1 rejection is BUSY_ON_DATE | date, busy_count |
| data_quality_caveat | PRICE_IMPUTED / CITY_IMPUTED / SYNTHETIC | flags | — (caveat, never primary) |

### Selecting the primary reason per card, diverse across the triple
1. Candidates for card i: reasons with contribution >= 0.2 * max positive
   contribution of that card, plus AVAILABILITY_REPLACEMENT / *_IN_SHOWN /
   LANGUAGE_REQUEST_MATCH when they apply (contrast reasons count as strong:
   utility A = 1.0). Utility `U = 0.65*A + 0.25*D + 0.10*Q` with A = normalized
   contribution (phi / max phi of card), D = normalized min delta vs other shown
   cards for that feature, Q = 1 if the fact is unique in the shown set else 0.
2. Triple: enumerate combinations (<= 6 candidates per card => <= 216),
   maximize sum of U minus 0.5 per repeated code and 0.1 per repeated family;
   ties -> lexicographic by codes. Card order is NEVER changed. If the best
   combination still repeats a code, set MatchResult.diversity_limited=True.
3. Output per card: primary first (primary=True), then up to 2 supporting
   reasons (highest U, different family), then caveats. FORMAT_SUPPORTED is
   included as supporting only when no other supporting reason exists.
4. Evidence strings are pre-formatted with textfmt (money, dates) so the LLM
   copies them verbatim.

`reasons.assign(result: MatchResult, all_scored: tuple[CardFacts, ...], contractors, scorer) -> MatchResult`
returns a new MatchResult whose cards carry reasons. service.run calls
score_all once, slices top-3, then assign().

### Explanations from codes (matcher/explain.py)
- Prompt payload per card: {id, имя, позиция, главная причина: {code, evidence},
  поддерживающие: [...], оговорки: [...]} + запрос. No full description, no
  raw flags. Rule to the model: the first sentence states the primary reason
  with its numbers; the second may add one supporting reason or caveat.
- TemplateExplainer renders from codes with one Russian phrase pattern per
  code (3 variants keyed by rank), then joins: primary + 1 supporting + caveats.
- Validator additions: the text must contain every number in the primary
  reason's evidence (price/headroom/hours/date) and the competitor name for
  AVAILABILITY_REPLACEMENT; quotes must be substrings of the description.
