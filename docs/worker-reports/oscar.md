# QALAU background result

Generated with the official OpenAI Images API using **gpt-image-2.5-flare**, 1536×1024, high quality, one image. No fallback was needed. Source PNG metadata records model and prompt.

## Prompt

Create a premium abstract background for QALAU, a quiet professional
interface in the OpenAI visual style. Wide landscape, deep calm charcoal and
warm graphite, with subtly luminous blue-violet and muted sage highlights.
Broad soft abstract light and flare shapes drift through the edges, with a
gentle atmospheric gradient and an open, dark center. Beautiful restrained
light diffusion, smooth tonal transitions, barely perceptible fine grain.
Very low contrast across the entire frame: white and muted light-gray interface
text must remain readable everywhere under a dark overlay. No bright white
spots, sharp edges, saturated neon, text, letters, objects, people, logos,
symbols, borders, or watermarks. An abstract field of light only.

## Files

- `web/qalau/assets/bg-hero.png`: 1,383,403 bytes
- `web/qalau/assets/bg-hero.webp`: 15,320 bytes (600,000-byte maximum)
- `web/qalau/assets/bg-hero-blur.webp`: 1,176 bytes (20,000-byte maximum; the smooth blurred image compresses smaller)
- `scripts/make_background.py`: run with `UV_CACHE_DIR=/private/tmp/qalau-uv-cache uv run --with pillow scripts/make_background.py`; add `--force` to regenerate. Verified a second run skips the API and regenerates optimized derivatives.

`style.css` and `docs.css` use the WebP over a blurred fallback, with a 72–88% dark gradient overlay, cover sizing, fixed desktop attachment and scroll attachment at widths <=760px. Background-only changes add translucent surfaces and desktop header blur. Palette variables and layout are unchanged.

## Verification

- Started `uv run uvicorn app:app --port 8019` with a writable UV cache.
- HTTP 200: `/`, `/docs-ui`, `/tests`, both CSS files and both WebP assets.
- `uv run pytest -q -o addopts= tests/test_api.py tests/e2e`: **31 passed, 10 skipped**, exit 0. Browser coverage did not execute because the sandbox blocks Chromium launching.
- Inspected the generated image: soft charcoal, violet and sage abstract light; no text or objects.
- Compared every decoded WebP pixel composited over RGB(25,27,25) at the weakest overlay alpha, .72: minimum contrast **10.89:1 for white**, **4.69:1 for #aaa**, **5.42:1 for #b2b9ae**. This checks the background, not every rendered element or card.
- Screenshot target directory: `.work/shots-bg/`. **No screenshots were produced**: Playwright Chromium launch fails with `bootstrap_check_in ... MachPortRendezvousServer: Permission denied (1100)`. Computed-style and page-error checks therefore remain unverified. Intended screenshot paths: `home-1280.png`, `home-400.png`, `docs-ui-1280.png`, `docs-ui-400.png`, `tests-1280.png`, `tests-400.png` within that directory.

## Remaining integration limitation

`web/qalau/tests.html` became available during this task. It embeds its own CSS and imports neither `style.css` nor `docs.css`; `tests.css` does not exist. The explicit ownership rules prohibit changing HTML or creating tests.css unless it exists. Therefore **/tests still has its original flat background**. Its owner must link the shared background rules or extract its inline CSS before this can be integrated within the allowed files.

No app code, HTML, matcher files, tests, or layout rules were changed. No push performed.

## Commit

`git add` refused with `Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-charlie/index.lock': Operation not permitted`. No commit was created; the shared worktree Git metadata is outside the writable sandbox. Files remain in this worktree for review and commit by the operator.
