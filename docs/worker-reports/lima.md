# codex-lima — QALAU API integration

Branch: `wt-charlie`, starting commit: `db22fff`. No push performed.

Implemented:
- QALAU at `/`; original UI at `/legacy`; developer guide at `/docs-ui`; FastAPI `/docs` preserved.
- Static QALAU assets served after API routes. Optional `/tests` HTML and `/api/tests` report routes, including missing-file responses.
- `api.mjs` loads metadata and demo requests, submits `/api/match`, preserves backend order, facts, explanations, sources, and rejection labels. Local catalogue and matching modules deleted.
- Russian UI retains service search, grouping, conditions, edit/retry/reset interactions; shows calendar limits, per-city counts, loading, all three outcomes, shortfall notes, provenance badges, rejection disclosure, and timing/source footer.
- Backend demos populate the form and submit; `?example=1` through `?example=6` and scenario names are supported.
- Documentation describes the actual server pipeline and API contract. Removed local dataset-module instructions.
- Legacy browser tests moved to `/legacy`. New QALAU browser coverage includes all outcomes, backend card text/order, facts/badges, rejection disclosure, demo payloads, metadata, 422/retry, loading, URL examples, and 400px overflow checks.
- Operator smoke script verifies all six demos against their live API responses and saves screenshots under `.work/shots-qalau/`.

Verification:
- Full suite: `UV_CACHE_DIR=/private/tmp/codex-lima-uv E2E_SCREENSHOTS=.work/shots-qalau uv run pytest -q -o addopts=` → **381 passed, 10 skipped** in 18.81s. Log: `.work/qalau-pytest.log`.
- All ten skips are Chromium browser checks: macOS sandbox denies `bootstrap_check_in ... MachPortRendezvousServer` (`Permission denied (1100)`). Chromium is installed. No browser assertions were executed, so desktop/mobile rendering remains unverified.
- API tests and Node-executed adapter contract pass, including backend order opposed to ascending price, DTO explanations/facts, reason counts, flags, nullable fields, 422 detail, and network/invalid-JSON errors.
- `node --check` for both application modules, Python compilation, and `git diff --check` passed.
- Started `uv run uvicorn app:app --port 8012` with a writable UV cache. Direct live HTTP checks passed for all six demos: dense, rare, empty_no_category, empty_none_eligible, date_pair_a, date_pair_b. Expected outcomes matched; card explanations were nonempty; semantic backend was embeddings. The two dates returned different ordered cards as expected.
- `/`, `/docs-ui`, `/legacy`, and their referenced local links/assets returned HTTP 200.
- `uv run python scripts/browser_check.py http://127.0.0.1:8012` was attempted and blocked before page navigation by the same Chromium launch restriction. Log: `.work/qalau-browser-check.log`.
- The computer-use tool also reported no available browser.
- Live checks generated explanation-cache entries; those verification-only additions were removed. No matching/ranking/explanation implementation was changed. `tests.html` and the pre-existing untracked `TASK.md` were left untouched.

Screenshots:
- **Not generated**, because Chromium cannot launch in this sandbox.
- Expected smoke output paths for the three outcomes: `.work/shots-qalau/1-matched.png`, `.work/shots-qalau/3-no_category_in_city.png`, `.work/shots-qalau/4-none_eligible.png`.
- Expected 400px test paths: `.work/shots-qalau/qalau-matched-400px.png`, `.work/shots-qalau/qalau-no_category_in_city-400px.png`, `.work/shots-qalau/qalau-none_eligible-400px.png`.
- Rerun the full suite and smoke command in a session that permits Chromium launch to finish visual verification.

Commit status: **blocked by filesystem sandbox**. Explicit `git add` of only the task files failed with:

```text
fatal: Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-charlie/index.lock': Operation not permitted
```

No files were staged by this attempt; `git commit` was not run after staging failed. Changes remain in the worktree. No push performed.
