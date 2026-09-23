# codex-india: text-embedding-3-large migration

Branch: `wt-charlie`; starting commit: `87cf1e0`. No push performed.

## Files

- `matcher/embeddings.py`: default `text-embedding-3-large`, 1024 dimensions; `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` environment overrides; explicit `dimensions=` API argument. Hash includes model, dimensions, and text. Cache metadata must match both settings, and cached/API vector lengths must match. Incompatible or legacy caches are empty for scoring, raise `SemanticUnavailable` without a key, and are replaced when rebuilt. Six-decimal rounding, compact JSON, and norm-corrected cosine remain in use.
- `scripts/build_embeddings.py`: dimension-aware fake client, persisted-cache verification, and output metadata.
- `tests/test_embeddings.py`: explicit dimension-aware fixtures and API fake; coverage for defaults/overrides, incompatible/legacy cache rejection, replacement without mixed vectors, hash isolation, malformed vector lengths, persistence, and offline reuse. 11 additional cases.
- `data/embeddings.json`: regenerated with the real API key, covering all 78 contractors and demo query texts.
- `README.md`: only the two model mentions (now 1024 dims) and shipped vector count changed.
- `DESIGN.md`: updated the EmbeddingScorer paragraph.
- `RESULT.md`: this verification record.

`matcher/config.py` already loads the worktree `.env` with python-dotenv and needs no change. Created the user-authorized, gitignored `.env` symlink to `/Users/mike/Hackaton/hack-109052e5-itshich/.env`; its contents were never printed or staged.

No edits to `matcher/explain.py`, `matcher/reasons.py`, `matcher/service.py`, `matcher/ranking.py`, `matcher/model.py`, or `web/`. Pre-existing untracked `TASK.md` left untouched.

## Commands and results

All `uv` commands used `UV_CACHE_DIR=/private/tmp/codex-india-uv`: the default `/Users/mike/.cache/uv` is not writable in this sandbox.

1. `uv run pytest tests/test_embeddings.py -q -o addopts=` before implementation: **46 failed, 2 passed**. Failures demonstrated missing dimensions support, omitted API argument, and old cache format. After implementation: **48 passed in 0.23s**.
2. `uv run python scripts/build_embeddings.py`: exit 0.
   ```text
   contractors=78 descriptions=78 sentences=320 unique_texts=389 embedded=389 reused=0 vectors=389 model=text-embedding-3-large dimensions=1024 dry_run=False
   ```
3. `uv run python scripts/find_demo_queries.py`: exit 0; **Verified 6 demo queries in demo/queries.json**. All six scenario constraints hold. Adds three unique query texts for **392 total vectors**. Regenerated expectations are byte-identical to the starting commit.
4. `OPENAI_API_KEY='' uv run python scripts/run_demo.py`, twice: both exit 0; all six scenarios use `semantic_backend: embeddings`, match saved expectations, and repeat the same order. Both complete output logs are byte-identical; all 11 displayed explanations per run replay from the LLM cache.
5. `uv run python scripts/run_demo.py` with the key loaded by config: exit 0; all six scenarios pass. Card IDs and explanation cache keys are unchanged, so the existing committed `data/explanations_cache.json` remains valid and byte-identical; no new cache entries were needed.
6. `uv run pytest -q -o addopts=`: exit 0.
   ```text
   220 passed, 4 skipped in 12.64s
   ```
   Follow-up `uv run pytest tests/e2e/test_browser.py -q -o addopts= -rs`: four skips at lines 74, 100, 123, and 142, each because **macOS sandbox blocks Chromium launch: MachPort permission denied**. No test failures.
7. Offline artifact audit: all 389 catalogue texts plus three distinct demo texts are cache hits without a key. All 392 vectors have 1024 finite components rounded to six decimals; JSON is compact; file is under 6,000,000 bytes. Demo logs, saved expectations, and existing explanation cache were compared byte-for-byte.
8. `git diff --check`: exit 0, no whitespace errors.

## Normalization verification

The [official OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings) documents shortened dimensions and unit normalization. A live 1024-dimensional probe for `корпоратив Ведущий Алматы` measured L2 norm **0.999760138377** before repository rounding; the initial strict unit-norm assertion (absolute tolerance 1e-6) failed. Repeating with both SDK default base64 decoding and explicit float encoding reproduced the same norm, locating the deviation in the returned vectors rather than cache rounding. Explicit normalization yields **1.000000000000**, and production `_cosine(vector, vector)` yields **1.000000000000**. Production cosine already divides the dot product by both norms, so it corrects this deviation without relying on API normalization or changing stored precision. The cached vectors' norms range from **0.999459890616** to **1.000642084138**.

## Demo card IDs: old vs new

| Scenario | Old IDs, in order | New IDs, in order |
| --- | --- | --- |
| dense | HK-35215, HK-27222, HK-29829 | HK-35215, HK-27222, HK-29829 |
| rare | HK-39372, HK-90001 | HK-39372, HK-90001 |
| empty_no_category | [] | [] |
| empty_none_eligible | [] | [] |
| date_pair_a | HK-35215, HK-27222, HK-29829 | HK-35215, HK-27222, HK-29829 |
| date_pair_b | HK-88430, HK-77838, HK-44733 | HK-88430, HK-77838, HK-44733 |

## Cache sizes

- `data/embeddings.json`: **3,792,336 bytes** (3.79 MB / 3.62 MiB), **392 vectors × 1024 dimensions**, below the 6 MB target.
- `data/explanations_cache.json`: **4,530 bytes**, unchanged and already committed in the starting revision.

## Commit blocked by sandbox

Attempted the requested scoped `git add` (including the already-tracked, unchanged demo and explanation-cache paths). It failed before staging:

```text
fatal: Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-charlie/index.lock': Operation not permitted
```

The actual worktree Git metadata is outside this session's writable roots, and permission escalation is disabled. No commit was created; no push was attempted. The verified changes remain in the working tree. Complete the local commit from a session allowed to write that Git metadata:

```sh
git add matcher/embeddings.py scripts/build_embeddings.py tests/test_embeddings.py data/embeddings.json data/explanations_cache.json demo/queries.json README.md DESIGN.md RESULT.md
git commit -m "Switch semantic embeddings to 3-large at 1024 dimensions"
```
