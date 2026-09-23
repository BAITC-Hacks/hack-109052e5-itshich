# codex-november — live test cases

Implemented 17 editable cases in `demo/cases.json`, the in-process runner with `--strict`, a saved full-response report, GET `/api/cases`, POST `/api/cases/run`, and the live section above the pytest tables on `/tests`. The section includes request chips, ordered cards, prices, primary codes, explanations and their sources, outcome/shortfall, timing, checks, rerun loading state, and all/failed filters. Failed checks show expected and actual values and expand automatically. Fonts are local; responsive styles include a single-column layout at 400px.

Real pipeline results: **17 passed / 17 total**, embeddings backend, LLM explanations for all returned cards (cache enabled). The ten research cases match the documented outcomes and eligible-derived card counts. Both date-pair cases exclude the busy top card; cases 6 and 7 satisfy the requested primary reason checks. No requested behavior discrepancy was found.

Additional cases: out-of-calendar date → 422; December banquet hall → `none_eligible`; overseas photographer → `matched`, 1 card; Astana hotel/conference → `matched`, 1 card; 100,000 ₸ Astana host → `none_eligible`, «дороже бюджета»; Kazakh instrumentalist / 3 hours → `matched`, 1 card; unknown category → `no_category_in_city`. December's expectation retains the requested outcome list (`matched` or `none_eligible`) and the observed zero-card bounds.

The current API DTO omits internal reason facts. Without editing `matcher/**`, the report preserves each full DTO unchanged and adds `card_reasons`, replaying deterministic matching using the same semantic backend. The runner verifies identical card order before attaching these facts and does not make another explanation call. If future DTOs expose `facts.reasons`, those are used directly.

Verification:

- `UV_CACHE_DIR=/tmp/november-uv uv run python scripts/run_cases.py --strict`: 17/17 passed.
- Live HTTP POST `/api/cases/run`: 17/17 passed; subsequent GET matched the persisted report exactly.
- `UV_CACHE_DIR=/tmp/november-uv uv run pytest -q -o addopts= -rs`: **416 passed, 11 skipped**, 19.74 s. All 11 skips are existing Chromium browser checks blocked by macOS sandbox MachPort permission denial.
- New tests: **31 passed**, including every expectation type, ordered IDs, missing DTO reasons, schema validation, CLI exit status, report consistency, and API routes.
- `node --check` on the page's inline JavaScript and `git diff --check`: passed.
- Attempted desktop/400px Playwright visual check; Chromium startup failed with `bootstrap_check_in ... Permission denied (1100)`. Visual rendering and interactive browser verification remain unverified in this sandbox. Live HTTP behavior was verified separately.

The default uv cache is outside the sandbox's writable roots, so verification used `/tmp/november-uv`. Generated explanation-cache changes were restored; only owned deliverables and this result note are included. Existing untracked `TASK.md` was left untouched. No push was attempted.

Commit blocked: `git add` exited 128 because the sandbox denied creation of `/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-alpha/index.lock` (`Operation not permitted`). No files were staged and no commit was created; all deliverables remain in the working tree. No push was attempted.
