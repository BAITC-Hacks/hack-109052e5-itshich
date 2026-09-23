# codex-quebec — live pipeline tests

Implemented the live-tests contract by extending the existing live-cases work.

- Added `data/live_tests_requests.json`: the six demos, all five research cases,
  and the remaining legacy cases (18 total). Added the missing dense-budget case
  to `demo/cases.json`; regenerated `data/live_cases.json` (18/18 expectations pass).
- Added `scripts/run_live_tests.py`, reusing legacy reason extraction, expectation
  evaluation and atomic JSON publishing. Writes the requested snapshot shape,
  prints Markdown, and checks card order, primary codes, and explanation texts.
  `--check` never overwrites the saved snapshot.
- Added card reasons with Russian labels, unknown-code fallback and scarcity-code
  exclusion. `CardDTO.ReasonDTO` is scoped to the card so the existing rejection
  `ReasonDTO` and all existing rejection fields remain unchanged (add-only API).
- Added `GET /api/live-tests`, including the specified empty-file stub. Preserved
  both `/api/cases` routes.
- Replaced the live section on `/tests` with `#live-tests` after the demos. Inputs
  are on the left; saved/fresh cards, all reasons, flags, explanations and timing
  are on the right. Per-case/all reruns use `selectProfiles` from `api.mjs`, with
  disabled controls, visible progress/errors and independent three-axis diffs.
  Retained expectation details. Added an in-page anchor; header/nav untouched.
- Added live summary tiles/report data, unit/API/browser coverage and a short
  README subsection. No protected matching or main-app files were edited.

## Verification

- `UV_CACHE_DIR=/private/tmp/codex-quebec-uv uv run pytest -q -o addopts=`
  (also emitted `.work/live-tests-junit.xml`): **425 passed, 14 skipped**, no failures.
- Real `scripts/run_live_tests.py`: 18 saved cases, all returned card explanations
  from LLM/cache. Subsequent `--check`: exit 0, all three axes match.
- `scripts/run_cases.py --strict`: **18/18 passed**.
- Two `OPENAI_API_KEY='' ... scripts/run_demo.py` runs: `cmp` exit 0,
  byte-identical stdout in `.work/demo-live-a.txt` and `.work/demo-live-b.txt`.
- Live server on port **8023**: `/api/live-tests` returns 18 cases; actual
  `POST /api/match` matches the first saved case on all three axes.
- Extracted page module: `node --check` passes. `git diff --check` passes.
- `scripts/test_report.py --junit .work/live-tests-junit.xml --strict`:
  generated `data/test_report.json`, 439 total tests and live-tests summary.

## Environment limitations / handoff

- Chromium cannot launch under this macOS sandbox (`bootstrap_check_in`,
  MachPort permission denied). All 14 browser tests were skipped, including the
  three new live-tests checks. The native browser fallback also returned
  `Computer Use was not approved to use Google Chrome`.
- No screenshot or visual-verification claim is made. Details are in
  `.work/shots-live/BLOCKED.md`. Run browser tests with
  `E2E_SCREENSHOTS=.work/shots-live` in a browser-capable environment; the live
  page is `http://127.0.0.1:8023/tests#live-tests`.
- `uv`'s default cache was outside the writable sandbox; verification used the
  writable temporary cache above without changing project configuration.
- The real LLM run appended three entries to `data/explanations_cache.json`.
  They are intentionally excluded from staging because the cache is outside
  this worker's ownership. Preserve/incorporate them for offline `--check`
  reproducibility of those additional cases. `TASK.md` was pre-existing and
  was not modified or staged.
- No push was performed. Local git staging/commit outcome is recorded below.

## Git result

`git add -- <explicit owned files>` failed before staging:

```
fatal: Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-alpha/index.lock': Operation not permitted
```

The linked Git directory is read-only in this session. The chained `git commit`
was therefore not executed. Changes remain in the worktree; nothing was pushed.
