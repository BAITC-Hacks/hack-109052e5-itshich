# Bravo result

Branch: `wt-bravo`.

## Files

- `matcher/embeddings.py`: injectable `EmbeddingScorer`, `SemanticUnavailable`, content-hash cache, write-through persistence, cosine scoring and sentence selection. Cache hits are offline; write failures retain vectors in memory. Missing keys/vectors, malformed vectors and API failures produce `SemanticUnavailable`.
- `matcher/explain.py`: deterministic `TemplateExplainer`, grounded validator, injectable `LLMExplainer`, and `get_explainer()`. API/JSON/validation errors fall back for the entire result. Successful responses and fallbacks are cached by SHA-256 of the serialized request and ordered card IDs for the explainer's lifetime.
- `matcher/textfmt.py`: `money(int)` uses U+2009 thousands separators and `₸`; `format_date(date)` returns `dd.mm.yyyy`.
- `matcher/config.py`: the explicitly permitted small configuration module; loads the worktree's `.env` with python-dotenv. Creates the official OpenAI client only when needed and a key exists, with an 8-second timeout and retries disabled.
- `scripts/build_embeddings.py`: local CSV reader, full descriptions plus sentences, only missing hashes embedded, `--csv`/`--input`, `--output`, and offline `--dry-run` with an injected eight-dimensional zero-vector client.
- `tests/test_explain.py`, `tests/test_embeddings.py`: manually constructed frozen model objects; only OpenAI clients are faked. Filesystem behavior uses real temporary files.

`matcher/model.py` and alpha's modules were not modified or created. No frozen-contract changes are proposed. `TASK.md` was already untracked and is not part of this change.

## Verification

Development followed red → green at the agreed public seams: missing modules, validator failures, similar template texts, invalid LLM responses, response caching, embedding persistence, snippets, read-only storage, unavailable vectors, malformed vectors and the CLI dry-run. No live OpenAI requests were made.

Final test command (local uv/pytest scratch directories; the pytest override displays the count despite the project's existing `-q`):

```sh
UV_CACHE_DIR=.work/uv-cache PYTEST_ADDOPTS='--basetemp=.work/pytest' \
  uv run pytest -q tests/test_explain.py tests/test_embeddings.py -o addopts=''
```

```text
........................................................................ [ 98%]
.                                                                        [100%]
73 passed in 0.17s
```

Coverage includes exact money/date output, 1–2 sentences and 40–350 characters, digit grounding, snippet and language grounding, banned phrases, pairwise similarity, similar/identical cards, single/empty results, all imputation flags and `max_hours=None`. LLM tests cover ordered JSON, compact prompt contents, temperature 0 / seed 42 / timeout 8 / JSON mode, whole-result fallback, and deterministic cache reuse. Embedding tests cover known cosine values, 3-decimal rounding, cache hits with zero client calls, cache persistence/reload, sentence ties and truncation, absent keys, corrupt/model-mismatched caches, API timeouts and invalid vectors.

An additional audit constructed 32 three-card combinations varying duration applicability, requested language, shared/missing snippets, imputed flags and event format. All 32 returned `validate_explanations(...) == []` after fixing the shared-caveat similarity case; the regression is retained in the tests.

Full-dataset offline build, run twice:

```sh
OPENAI_API_KEY='' UV_CACHE_DIR=.work/uv-cache \
  uv run python scripts/build_embeddings.py --dry-run --output .work/embeddings-dry-run.json
```

First run:

```text
contractors=66 descriptions=66 sentences=284 unique_texts=341 embedded=341 reused=0 vectors=341 model=text-embedding-3-small dry_run=True
```

Second run:

```text
contractors=66 descriptions=66 sentences=284 unique_texts=341 embedded=0 reused=341 vectors=341 model=text-embedding-3-small dry_run=True
cache_bytes_unchanged=True
cache_sha256=4bf7b8237e7c135049b48965c94fe9dc768f77ebbe84617ab7615cfc9fe1d734
all_vectors_are_8_zeroes=True
```

The byte/hash/vector checks were performed with a local Python verification command. The CLI integration test also independently runs a small CSV twice and checks exact hashes, vector values, counts and unchanged bytes.

## Example three-card TemplateExplainer result

Hand-built fixture: corporate event in Алматы on 14 November 2026; budget 800 000 ₸; requested English and 5 hours. Every card starts at 600 000 ₸, supports up to 8 hours and has a distinct description snippet. Validation returned `[]`.

1. **227 characters:** Первый в выдаче: Хикару, цена от 600 000 ₸ при бюджете 800 000 ₸ — запас 25 %; формат — корпоратив, свободен 14.11.2026. Из описания: «Ведёт деловые встречи с живой импровизацией»; язык — английский; до 8 ч при запрошенных 5 ч.
2. **232 characters:** На втором месте Микаса: корпоратив, свободен 14.11.2026; стартовая цена — 600 000 ₸, это на 25 % ниже бюджета 800 000 ₸. В профиле: «Проводит музыкальные игры для команд»; заявлен английский; продолжительность: 5 ч из доступных 8 ч.
3. **239 characters:** Замыкает тройку Рен: корпоратив со ставкой от 600 000 ₸; лимит 800 000 ₸ оставляет 25 % резерва, свободен 14.11.2026. Особенность: «Модерирует дискуссии и церемонии награждения»; доступен английский; на 5 ч можно пригласить при лимите 8 ч.

## Design interpretations and limits

- The validator protects `dd.mm.yyyy` dates before splitting sentences on `[.!?]`; otherwise the required date would itself count as three sentences. Russian month-name dates are also accepted. Monetary digit groups are normalized to complete amounts before grounding.
- Templates always keep price, budget, headroom, format, availability and applicable caveats. Optional details are ordered by rarity among shown cards and included when they fit within 350 characters; quotes are at most 90 characters and exclude banned phrases. The requested wording `синтетический профиль` takes precedence over the alternative wording in DESIGN.md.
- Zero vectors have neutral cosine 0, hence score 0.5, so the required dry-run remains usable without division by zero. Empty candidate lists and empty descriptions need no embeddings.
- The real `data/embeddings.json` was not generated: the task explicitly prohibits live network calls and limits file ownership. Offline verification artifacts remain under ignored `.work/`; zero vectors are not shipped as production embeddings.
- As specified, a description-only cache cannot score an unseen query without a key. Query vectors are cached at request time; missing ones raise `SemanticUnavailable` for the pipeline's lexical fallback.
- Retain an `LLMExplainer` instance for in-process response-cache reuse. Cache scope is per instance, so injected clients and different model configurations do not share responses.
