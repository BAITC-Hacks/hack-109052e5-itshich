"""Live QALAU check: six backend demos, card content, outcomes and screenshots.

Usage: uv run python scripts/browser_check.py [base_url]
"""
import argparse
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--shots", type=Path, default=Path(".work/shots-qalau"))
    args = parser.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 1400}, locale="ru-RU")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.base_url, wait_until="networkidle")
            demos = page.request.get(args.base_url.rstrip("/") + "/api/demo").json()
            buttons = page.locator("#demos button")
            expect(buttons).to_have_count(6)
            assert len(demos) == 6, "Expected six backend demos"
            outcomes = set()
            for index, demo in enumerate(demos):
                started = time.monotonic()
                with page.expect_response(lambda response: "/api/match" in response.url, timeout=120000) as pending:
                    buttons.nth(index).click()
                response = pending.value
                assert response.status == 200, response.text()
                result = response.json()
                assert result["outcome"] == demo["expected_outcome"], result
                expect(page.locator("#banner")).to_have_attribute("data-outcome", result["outcome"])
                expect(page.locator("#outcome-title")).to_have_text(result["outcome_title_ru"])
                cards = page.locator(".vendor")
                expect(cards).to_have_count(len(result["cards"]))
                for rank, card in enumerate(result["cards"]):
                    expect(cards.nth(rank)).to_have_attribute("data-profile-id", card["id"])
                    expect(cards.nth(rank).locator(".explanation")).to_have_text(card["explanation"])
                if result["shortfall_note"]:
                    expect(page.locator("#shortfall")).to_have_text(result["shortfall_note"])
                outcome = result["outcome"]
                outcomes.add(outcome)
                page.screenshot(path=str(args.shots / f"{index + 1}-{outcome}.png"), full_page=True)
                print(f"[{demo['name']}] {response.status} {outcome} — {result['outcome_title_ru']} "
                      f"({time.monotonic() - started:.2f} s)")
                print("  ", page.locator("#result-footer").inner_text())
                for card in result["cards"]:
                    print(f"  - {card['name']}: {card['explanation']}")
            assert outcomes == {"matched", "no_category_in_city", "none_eligible"}
            assert not errors, errors
        finally:
            browser.close()


if __name__ == "__main__":
    main()
