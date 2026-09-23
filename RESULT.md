# Charlie result

Branch: `wt-charlie`. No push performed. `matcher/model.py`, `pyproject.toml`,
`uv.lock`, the other workers' modules, and the pre-existing `TASK.md` were not edited.

## Files

- `matcher/pipeline.py`: lazy collaborator imports, cached contractor loader,
  embeddings selection with initialization/runtime fallback, RequestError adapter,
  and timed dictionary responses. Exposes `choose_scorer`, `run`, and `explain`.
- `matcher/api_types.py`: Pydantic request/response/metadata models and `to_dto`;
  exact outcome titles, card facts, and rejection codes with Russian labels.
- `app.py`: `/`, `/api/match`, `/api/meta`, `/api/demo`.
- `web/index.html`: self-contained Russian form and results, demo buttons,
  distinct outcome banners, ordered cards, badges, facts, rejection disclosure,
  request errors, and a responsive 400px layout.
- `tests/test_api.py`: 19 API cases built from frozen domain types; shared fake
  run/explain collaborators, outcomes, 422, deterministic bytes excluding timing,
  scorer fallback, metadata, demo presence/absence, and HTML delivery.
- `tests/e2e/test_browser.py`, `tests/e2e/__init__.py`: four marked Playwright
  sync checks using the same fake pipeline and an in-process Uvicorn thread on
  a bound ephemeral socket. Covers outcomes at 400px, card order, badges,
  rejection toggling, metadata-driven form, optional values, and 422 errors.
- `scripts/run_demo.py`: lazy pipeline import, missing-file skip, both runs'
  card order, ranked card details and explanations; nonzero exit for changed order.

## Verification

TDD red steps were observed at the API seam before implementation: missing
pipeline, unhandled/missing RequestError, five scorer fallback failures, missing
metadata/demo routes, and HTTP 404 for the home page. Subsequent API checks passed.

All tool caches, browser downloads, temporary files and smoke artifacts were
placed under this worktree's ignored `.work/` directory.

Environment used for the final test command:

```sh
export UV_CACHE_DIR="$PWD/.work/uv-cache"
export TMPDIR="$PWD/.work/tmp"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.work/ms-playwright"
export PYTEST_ADDOPTS="--basetemp=$PWD/.work/pytest -rs -o addopts= -o markers=e2e"
uv run pytest -q tests/test_api.py tests/e2e
```

Final output: `19 passed, 4 skipped in 0.78s` (exit 0). All four skips explicitly
report `macOS sandbox blocks Chromium launch: MachPort permission denied`.
The CLI marker override avoids editing the shared project configuration.

Other commands/checks:

- `uv run playwright install chromium` with the local cache/browser/temp
  environment above: exit 0; Chromium, headless shell and FFmpeg downloaded.
- `uv run pytest -q tests/e2e` with `PLAYWRIGHT_BROWSERS_PATH` pointing at the
  absent `.work/browser-not-installed` directory and the same pytest overrides:
  `4 skipped in 0.60s`; each skip correctly says Chromium is not installed.
- `uv run --with ruff ruff check matcher/pipeline.py matcher/api_types.py app.py tests/test_api.py tests/e2e scripts/run_demo.py`:
  `All checks passed!` (exit 0).
- `uv run python scripts/run_demo.py`: `Демо-запросов пока нет: demo/queries.json отсутствует. Пропускаем.` (exit 0).
- Inline `uv run python -` smoke using the same Uvicorn fixture and fake pipeline:
  all three GET routes returned 200; all three POST outcomes returned 200;
  `run_demo.main` printed both orders with `Порядок совпадает: да` for every
  fixture query. Final line: `Live Uvicorn and demo-runner smoke: passed`.
- Inline `node`/jsdom smoke of the actual inline page script, with response
  fixtures generated through the verified API seam: all three outcomes,
  demo-filled request payloads, ordered names, city counts, calendar bounds,
  three distinct computed colours, disclosure toggling and 422 handling passed.
  Final line: `DOM smoke does not verify browser layout or replace Playwright.`
  jsdom was installed only in `.work/dom-smoke` with npm scripts disabled.
- `git diff --check`: exit 0, no output.

## Deviations and remaining verification

- Chromium is installed, but the host sandbox rejects its macOS MachPort
  registration (`bootstrap_check_in ... Permission denied (1100)`). The fixture
  explicitly skips that known environment failure as well as absent browser
  binaries. Other browser launch errors still fail. Real browser interaction and
  400px visual layout remain unverified here; no screenshots were taken.
- The other workers' implementations and real `demo/queries.json` are absent
  in this worktree, as expected. Real catalogue ranking/LLM integration must be
  checked after their changes are combined. API and live-server verification
  used the requested hand-built domain results.
- The unspecified metadata wire names are `calendar_start`, `calendar_end`,
  and `counts[city][category]`, including zero counts. Product behavior otherwise
  follows DESIGN.md. Card rank is the response-array position, preserving the
  exact documented card fields without an extra rank field.

To capture screenshots on a host that can launch Chromium, set
`E2E_SCREENSHOTS="$PWD/.work/screenshots"` when running the browser tests.

## Git commit blocker

Attempted:

```sh
git add -- matcher/pipeline.py matcher/api_types.py app.py web/index.html tests/test_api.py tests/e2e/test_browser.py tests/e2e/__init__.py scripts/run_demo.py RESULT.md
git diff --cached --check
git commit -m "Add matching API, Russian page, and standalone seam tests"
```

The commands were chained with `&&`; the first command failed with exit 128:

```text
fatal: Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-charlie/index.lock': Operation not permitted
```

The Git metadata lives outside this worktree's writable filesystem boundary.
Staging and committing therefore could not complete in this session; no commit
was created and no push was attempted. The implementation and this report remain
in the worktree, ready for the authorized add/commit in a writable environment.
