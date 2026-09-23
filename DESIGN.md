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
- total = round(sum(weights_for(request)[f] * feature[f] for f in FEATURES), 4)

`weights_for()` returns a fresh dict with exactly the FEATURES keys. The
plain constant `CATEGORY_GROUPS` selects these category priors:

| Group | Categories | budget | semantic | language | duration | quality |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| general | all other / unknown categories | 0.25 | 0.55 | 0.00 | 0.05 | 0.15 |
| venue | Банкетный зал, Ресторан, Отель, Загородная площадка | 0.25 | 0.60 | 0.00 | 0.05 | 0.10 |
| host | Ведущий, Ведущий церемонии | 0.25 | 0.55 | 0.00 | 0.10 | 0.10 |
| no_presence | Флорист, Декоратор, Подарки и сувениры | 0.25 | 0.60 | 0.00 | 0.00 | 0.15 |

Language remains a hard filter and a displayed fact, with zero ranking weight.
When duration is not requested, set its weight to zero and renormalize the
remaining weights to sum to one, rounded to four decimals. Any rounding
residue goes into semantic, the largest weight (venue without duration:
0.2632 / 0.6315 / 0 / 0 / 0.1053). Reasons use this same function for exact
feature contributions; weights never depend on the eligible pool.

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
{"model": ..., "dimensions": ..., "anchors": {"low": ..., "high": ..., "pairs": ...}, "vectors":
{sha256(model + "\n" + str(dimensions) + "\n" + text): [floats]}}.
A model/dimensions mismatch is an empty cache; the builder rewrites it.
Vectors are rounded to 6 decimals and stored as compact JSON. The shipped
cache contains 801 vectors for 78 contractors, all 411 catalogue query texts,
and demo queries. Cosine divides
by both vector norms even when API vectors are not exactly unit length.
Texts embedded: full description per contractor AND each sentence of the
description (for snippets). `scripts/build_embeddings.py --anchors` also
enumerates every distinct format/category/city combination each contractor
can produce, with each of their languages and with no language. It includes
`synthetic_extra.csv` when present. These queries are cached before calibration,
so all requests with eligible catalogue candidates rank offline. Other missing
texts retain the existing API write-through behavior (memory-only if read-only).

Anchors use the Cartesian product of those distinct query texts and distinct
catalogue descriptions, including unrelated categories/cities, never just
eligible candidates or snippets. P05 and P95 use linear interpolation at
`(N - 1) * percentile`: `low = P05`, `high = max(P95, low + 0.10)`.
The shipped cache has 32,058 pairs, low 0.22155170065111932 and high
0.506944405362618. Score = `round(clip((cos - low)/(high - low), 0, 1), 3)`.
Clipping can intentionally tie descriptions above P95 (both demo florists).
The frozen anchors are rebuilt only by the explicit catalogue build step;
scoring, booking changes and vector write-through never recalibrate them.
Missing/invalid anchors raise `SemanticUnavailable` with the `--anchors`
rebuild command, even when vectors or an API key exist. Snippet selection is
unchanged: sentence with highest raw cosine, <= 200 chars.
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

This is the current B5/B6 contract, superseding the initial explanation design
above. The LLM only phrases selected reasons; code chooses contractors, order,
primary reasons and supporting facts. No changes to ranking or model types.

`reasons.assign(result, all_scored, contractors, scorer) -> MatchResult` returns
new cards with reasons. `service.run` calls `ranking.score_all` for every eligible
contractor, takes the first MAX_CARDS, then assigns reasons without reordering.

### Contributions

For card i and feature f: `phi_if = w_f * (x_if - mean_f(eligible))`. The mean
includes all eligible contractors, not just shown cards. Pairwise contrasts
`delta_ij_f = w_f * (x_if - x_jf)` include other shown cards and the best unshown
eligible contractor. Values are rounded to four decimals; weights come from
`ranking.weights_for(request)`.

### Aspect evidence

Use `matcher.aspects.load_aspects()` and its documented offline JSON schema.
Only positive aspects for which `Aspect.relevant_to(request.event_format)` is
true qualify. Choose the lexicographically first tag among equally relevant
aspects. Evidence is exactly `{"aspect": Aspect.label, "tag": Aspect.tag}`;
labels come from `matcher.aspects.TAGS`.

The offline tag's proof quote never enters reason evidence, prompt payloads or
card text. Missing files, absent tags, negative/neutral tags and irrelevant tags
yield no DESCRIPTION_ASPECT reason. The uniquely highest semantic score may
still yield DESCRIPTION_CLOSEST_IN_SHOWN with empty evidence, supporting only.

### Taxonomy (codes -> evidence keys)

| family | code | when | evidence |
| --- | --- | --- | --- |
| budget | BUDGET_HEADROOM | phi_budget > 0 or headroom_pct >= 30 | price, budget, headroom_pct |
| budget | BUDGET_LOWER_THAN_SHOWN | uniquely cheapest among shown | price, next_price, diff_pct |
| budget | BUDGET_FITS | fallback, or tight budget with no other budget reason | price, budget |
| format | FORMAT_SUPPORTED | eligibility; supporting only | format |
| language | LANGUAGE_REQUEST_MATCH | language requested | language |
| language | LANGUAGE_UNIQUE_IN_SHOWN | only shown card with unrequested language L | language |
| language | LANGUAGE_OPTIONS | three languages, none requested | languages |
| duration | DURATION_HEADROOM | hours requested, known max_hours, phi_duration > 0 | requested_hours, max_hours |
| duration | DURATION_MAX_IN_SHOWN | uniquely largest max_hours among shown | max_hours |
| duration | DURATION_NOT_APPLICABLE | max_hours is None; supporting only | — |
| description_semantic | DESCRIPTION_ASPECT | relevant positive offline tag | aspect, tag |
| description_semantic | DESCRIPTION_CLOSEST_IN_SHOWN | uniquely highest semantic among shown | aspect, tag if available; otherwise empty, supporting only |
| availability_contrast | AVAILABILITY_REPLACEMENT | confirmed counterfactual displacement by busy competitor | competitor, date; scarcity when active |
| availability_contrast | AVAILABILITY_ONLY_FREE | pool > 1, eligible == 1, at least one busy rejection | date; scarcity when active |
| availability_contrast | AVAILABILITY_SCARCE | December or at least half the category pool busy; supporting only | scarcity |
| data_quality_caveat | PRICE_IMPUTED / CITY_IMPUTED / SYNTHETIC | flags; never primary | — |

AVAILABILITY_REPLACEMENT requires i in top3(on date), i not in top3(ignore date),
and a busy j in top3(ignore date) that passes all other filters. Restoring j alone
to the eligible set must remove i from top3. Merely being outside the date-free
top three does not prove replacement.

### Priority rules and diversity

1. Base utility is `0.65*A + 0.25*D + 0.10*Q`: normalized positive contribution,
   normalized minimum contrast against other shown cards, and unique evidence.
   Contrast codes and LANGUAGE_REQUEST_MATCH use A = 1. Candidates normally
   need at least 20% of the card's maximum positive contribution; strong contrast
   and boosted pain-point reasons also qualify.
2. Before truncating candidates or searching combinations, add deterministic
   utility boosts: BUDGET_* +0.3 if exact budget headroom is below 15%;
   AVAILABILITY_* +0.3 in December or when >= 50% of the pool has BUSY_ON_DATE;
   LANGUAGE_REQUEST_MATCH +0.2 for a requested language; DURATION_HEADROOM +0.2
   for requested hours. The budget comparison uses prices, avoiding rounding
   errors at the 15% boundary. A rendered tight-budget reason says «впритык».
3. Scarcity evidence is `{"scarcity": "в эту дату свободны N из M"}`. M is the
   city/category pool size, N is M minus busy rejections, including contractors
   rejected for other conditions. N is not eligible_count. Enrich existing
   availability reasons or offer AVAILABILITY_SCARCE to every card.
4. Enumerate up to six candidates per card. Maximize total boosted utility minus
   0.5 per repeated code and 0.1 per repeated family. Ties use code/id tuples.
   Set diversity_limited if the selected primary codes still repeat.
5. Emit the primary, up to two supporting reasons from different families, then
   caveats. Give scarcity a supporting slot when the primary is another family.
   FORMAT_SUPPORTED fills an otherwise empty supporting slot. Evidence numbers
   use `textfmt` display formats. Card scores, ids and order are preserved.

### Explanations from codes (matcher/explain.py)

- Prompt: request, ordered cards with code + «смысл» + whitelisted facts,
  aspect labels/tags, names, caveats, and a single-line rejection count by reason.
  Rejection counts may overlap. No description, snippet, proof quote, raw score
  or raw flags. Rejection counts provide context, not extra allowed card numbers.
- Each card has 1–2 sentences, 60–260 characters. Primary comes first; at most
  one other reason and caveats follow. Templates use the same codes, retain
  whole facts and include the card's name for differentiation. Callers without
  reason codes receive structural templates, also without description quotes.
- Replacement templates are short: «В тройке, потому что более привлекательный
  вариант на эту дату занят ({competitor}).», «Более привлекательный вариант
  ({competitor}) на эту дату занят, поэтому здесь {name}.», or «Поднялся в тройку:
  {competitor} на эту дату занят.». A date is not required; the competitor is.
- Validation checks length, sentence count, at least two grounded facts and
  banned praise. Allowed numbers: price, budget, headroom_pct, next_price,
  diff_pct, max_hours, requested_hours, date parts and scarcity N/M. Digits in
  descriptions, names, proof quotes or arbitrary evidence do not extend this
  set. Decimal values are checked whole. Require every primary number except
  the optional replacement date, and the primary replacement's competitor name.
- A «…» fragment of four or more words copied from the description is rejected,
  including normalized case/whitespace and a trailing ellipsis. Primary codes
  must differ unless diversity_limited is true.
- Directed swap-test: for every i != j with different primary codes, collect the
  numbers, names and aspect labels mentioned in text_i. At least one token must
  be absent from j's allowed facts. Merely changing wording is insufficient.
  Equal primary codes are exempt from this pairwise test; diversity validation
  still applies. Relevant positive tags are included even if not selected as
  j's headline. Any validation failure triggers templates for the whole result.
- LLM settings remain temperature=0, seed=42, timeout=8, json_object. Memory and
  file caches hash request, complete ordered cards, selected primary codes and
  evidence, model and PROMPT_VERSION. Unordered busy-date sets are serialized
  in sorted order, so file-cache replay works across processes. Only successful
  LLM texts persist; fallbacks remain cached in memory.
