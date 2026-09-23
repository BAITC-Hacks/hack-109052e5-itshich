# QALAU UX pass — codex-papa / wt-delta

Implemented shared sticky QALAU headers on `/`, `/docs-ui`, and `/tests`, with the same three navigation links, `aria-current="page"`, visible active underline, and compact wrapping at 400px. Docs and tests now load the shared stylesheet.

Main-flow changes:
- Visited steps are clickable; steps 2 and 3 have back buttons.
- Category, city, format, date, budget, language, and hours persist in sessionStorage; reloading returns to the editable step (results reload to conditions).
- Enter in form fields submits. Existing inline date/budget/hours validation and date min/max remain in place.
- Loading updates the submit button and result status; previous results remain visible and dimmed. Results scroll into view and receive heading focus.
- Error results preserve server text and offer both retry and edit conditions.
- Explanation precedes pricing/facts. Cards are focusable, wrap long names, and stack at narrow widths.
- Up to three rejections expand by default; larger sets remain collapsed with the count.
- Scenario buttons use API titles or the requested Russian name mappings; main/docs user-facing demo wording is removed.

Ownership respected: CSS changes only append `/* UX pass */` blocks; existing background/overlay/backdrop rules are untouched. `tests.html` changes only add the explicitly requested stylesheet and replace/move its navigation header. Its two remaining `Демо-запросы` body strings (section heading and empty-report message) remain for the other worker. No matcher, app.py, README, or unrelated TASK.md edits.

## Verification

Commands used `UV_CACHE_DIR=/tmp/qalau-uv-cache` because the default uv cache is outside writable sandbox roots.

- `uv run pytest -q -o addopts= tests/test_api.py tests/e2e`: **31 passed, 21 skipped** (3.39s). All browser cases skip because Chromium launch is denied by the macOS sandbox.
- Full `uv run pytest`: **385 passed, 22 skipped** (21.06s); `.work/shots-ux/full-tests.log`.
- `git diff --check`, Node syntax checks for app.mjs/api.mjs, and Python compilation of browser_check.py/test_browser.py passed.
- Live uvicorn server on port 8021 started successfully. Direct HTTP smoke exercised all six API scenarios: dense matched/3, rare matched/2, empty_no_category no_category_in_city/0, empty_none_eligible none_eligible/0, date_pair_a matched/3, date_pair_b matched/3; all HTTP 200 and expected outcomes.
- Requested `uv run python scripts/browser_check.py http://127.0.0.1:8021` was attempted, but failed before page creation: `bootstrap_check_in ... MachPortRendezvousServer ... Permission denied (1100)`. This is not a successful browser smoke. Native Chrome access was also unavailable. Browser assertions, screenshot appearance, and the requested browser red/green cycle are unverified.

## Screenshots and logs

No screenshots could be captured because Chromium could not launch. The updated browser_check.py defaults to `.work/shots-ux/` and will produce `services-{desktop,400px}.png`, `conditions-{desktop,400px}.png`, `results-{desktop,400px}.png`, `selection-{desktop,400px}.png`, `docs-{desktop,400px}.png`, `tests-{desktop,400px}.png`, plus the six scenario captures when run in an environment that permits Chromium.

Available logs: `.work/shots-ux/requested-tests.log`, `.work/shots-ux/full-tests.log`, `.work/shots-ux/browser-smoke.log`.

No push performed.

## Commit blocker

`git add` failed with exit 128: `Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-delta/index.lock': Operation not permitted`. The worktree's Git metadata is outside the writable sandbox roots. Nothing was staged by this attempt; no commit or push was made. Changes remain in the working tree.
