"""Live browser check of the demo page (real pipeline, real LLM if key present).

Usage: uv run python scripts/browser_check.py [base_url]
Clicks every demo button, records outcome banner, cards, footer, timing, and
saves screenshots to .work/shots/. Exit code 1 if any demo shows an error.
"""
import sys, time, json
from pathlib import Path
from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
shots = Path(".work/shots"); shots.mkdir(parents=True, exist_ok=True)
report, failed = [], False
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1100, "height": 1400}, locale="ru-RU")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base, wait_until="networkidle")
    buttons = page.locator("#demos button")
    n = buttons.count()
    print(f"demo buttons: {n}")
    for i in range(n):
        name = buttons.nth(i).inner_text()
        t = time.time()
        with page.expect_response(lambda r: "/api/match" in r.url) as resp:
            buttons.nth(i).click()
        r = resp.value
        page.wait_for_load_state("networkidle")
        elapsed = round(time.time() - t, 2)
        banner = page.locator("#banner")
        outcome = banner.get_attribute("data-outcome")
        title = page.locator("#outcome-title").inner_text()
        cards = page.locator(".card h3").all_inner_texts()
        expl = page.locator(".card .explanation, .card .explain, .card p.explanation").all_inner_texts()
        footer = page.locator("#result-footer").inner_text()
        shortfall = page.locator("#shortfall").inner_text() if page.locator("#shortfall").count() else ""
        page.screenshot(path=str(shots / f"{i+1}-{outcome}.png"), full_page=True)
        ok = r.status == 200 and outcome in ("matched", "no_category_in_city", "none_eligible")
        failed |= not ok
        report.append({"demo": name, "status": r.status, "outcome": outcome, "title": title, "cards": cards,
                       "explanations": expl, "shortfall": shortfall, "footer": footer, "seconds": elapsed})
    browser.close()
for e in report:
    print(f"\n[{e['demo']}] {e['status']} {e['outcome']} — {e['title']} ({e['seconds']} s)")
    if e["shortfall"]: print("  ", e["shortfall"])
    for c, x in zip(e["cards"], e["explanations"] or [""] * len(e["cards"])):
        print(f"   - {c}: {x}")
    print("  ", e["footer"])
if errors: print("PAGE ERRORS:", errors); failed = True
sys.exit(1 if failed else 0)
