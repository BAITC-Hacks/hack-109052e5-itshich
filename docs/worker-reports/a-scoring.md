# a-scoring — A1, A2, A6

Implemented catalogue cosine anchors, category weight profiles, and regenerated
all six demos. Full-suite completion is blocked by three obsolete expectations
in `tests/test_reasons.py`, outside the assigned ownership. That file and
`matcher/reasons.py` / `matcher/explain.py` remain unchanged.

Files: `matcher/embeddings.py`, `matcher/ranking.py`,
`scripts/build_embeddings.py`, `data/embeddings.json`,
`data/explanations_cache.json`, `demo/queries.json`, `tests/test_embeddings.py`,
`tests/test_ranking.py`, `tests/test_service.py`, `tests/test_properties.py`,
`DESIGN.md` (ranking/embeddings sections), and this report.
Builder CLI tests moved into `test_service.py` to keep test modules near 250 lines.
No dependencies or model fields were added; `snippet()` is byte-for-byte unchanged.

The cache contains 411 distinct catalogue queries, 78 descriptions and 32,058
query/description pairs, including the synthetic supplement. Inclusive linear
percentiles give low **0.22155170065111932**, high **0.506944405362618**.
There are 801 vectors: 409 added; all 392 existing vectors are unchanged.
The venue profile without requested hours absorbs its 0.0001 rounding residue
in semantic (0.6315), so rounded weights still sum to one.

Semantic spread (`max - min` over eligible candidates, same request and vectors):

| Demo | Eligible | Before `(cos+1)/2` | After anchors |
| --- | ---: | ---: | ---: |
| dense | 5 | 0.094 | 0.663 |
| rare | 2 | 0.030 | 0.000 |
| empty_no_category | 0 | n/a | n/a |
| empty_none_eligible | 0 | n/a | n/a |
| date_pair_a | 5 | 0.094 | 0.663 |
| date_pair_b | 4 | 0.068 | 0.482 |

Both florists legitimately clip to 1.0 above P95; the prescribed calibration
does not guarantee a wider spread in every category. Baseline measurements use
the shipped `text-embedding-3-large` / 1024 cache, not the earlier small-model study.

## Commands and final results

All `uv` commands used `UV_CACHE_DIR=$PWD/.work/uv-cache`. Final validation used
Python 3.12.14, selected with `UV_PYTHON=3.12 uv sync` (34 packages installed).

- TDD: ranking tests first produced 7 expected failures; anchor/CLI tests produced
  17 expected failures, plus the missing percentile-calibration method failure.
  The conflicting `--compact --anchors` regression also failed before its fix.
- `uv run python scripts/build_embeddings.py --anchors`: 409 vectors embedded;
  the first API attempt timed out, the second succeeded. Final offline rerun
  with `OPENAI_API_KEY=''`: `embedded=0 reused=800 vectors=801`, `queries=411`,
  `pairs=32058`; exit 0.
- `OPENAI_API_KEY='' uv run python scripts/find_demo_queries.py`: exit 0,
  `Verified 6 demo queries in demo/queries.json`. Dense/A order:
  `HK-75012, HK-35215, HK-27222`; rare: `HK-39372, HK-90001`;
  B: `HK-77838, HK-44733, HK-88430`; both empty outcomes remain empty.
- `uv run python scripts/run_demo.py`: exit 0; warmed three new explanation
  entries through the existing LLM mechanism. **Network was required** for
  embeddings and explanation warming; existing explanation entries are unchanged.
- `OPENAI_API_KEY='' uv run python scripts/run_demo.py` run twice: both exit 0;
  `cmp .work/a-scoring-demo-1.log .work/a-scoring-demo-2.log`: exit 0, no output.
  All six scenarios retain their constraints; order and full output are identical.
  All nonempty results replay LLM texts offline; `validate_explanations` returns `[]`.
- `uv run pytest -q -p no:cacheprovider tests/test_embeddings.py tests/test_ranking.py tests/test_properties.py tests/test_service.py tests/test_explain.py --tb=short`:
  exit 0, 206 tests passed, including all-survivor booking score invariance.
- `UV_CACHE_DIR=$PWD/.work/uv-cache uv run pytest -q -p no:cacheprovider .`:
  exit 1; 302 collected, 295 passed, 4 browser tests skipped, 3 failures below.
- `git diff --check`: exit 0, no output.
- `git add` with the twelve explicit paths listed above: exit 128,
  `Unable to create .../.git/worktrees/feat-a-scoring/index.lock: Operation not permitted`.
  Git metadata is outside the sandbox's writable roots. No commit could be
  created, no files were staged, and no push was attempted.

## Required follow-up outside ownership

1. `test_budget_contrast_uses_all_eligible_for_contribution_and_formatted_evidence`
   (`tests/test_reasons.py:60`): replace the old 0.35-budget-weight expectations
   0.105 / 0.035 with 0.0833 / 0.0278 (host without hours: budget weight 0.2778).
2. `test_date_pair_replacement_is_proved_by_returning_kiki_alone` (`:132`):
   the current witness is **Джинбей**, not Кики. Without date filtering the top
   three are HK-77838 (0.7365), HK-75012 (0.6394), HK-44733 (0.6362).
   Restoring HK-75012 alone on 05.10 removes HK-88430 (0.5172) from the shown
   triple, proving the replacement. Кики is no longer in the date-free top three.
3. `test_dense_diversity_and_rare_contrasts_are_honest` (`:205`): allow truthful
   budget headroom instead of requiring a strictly closest semantic score or
   the specific cheapest-card code. The florists tie at semantic 1.0; reasons
   report 20% budget headroom and the uniquely available Kazakh language.

README weights/demo tables/vector count and the `ScoreBreakdown` docstring
also need their owners to refresh them. Permission to update the reasons tests
was requested; no response has been received. `TASKS.md` is absent here and in
the main checkout; its copy in the `research-weights` worktree was read.
