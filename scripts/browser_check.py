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
    parser.add_argument("--shots", type=Path, default=Path(".work/shots-ux"))
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
            labels = ["Плотная категория", "Редкая категория", "Категории нет в городе",
                      "Никто не проходит", "Первая дата", "Другая дата"]
            expect(buttons).to_have_text([demo.get("title_ru") or demo.get("title") or label
                                         for demo, label in zip(demos, labels)])
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
            for width, size in [(1280, "desktop"), (400, "400px")]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.goto(args.base_url, wait_until="networkidle")
                page.locator('[data-action="reset"]').click()
                for step in ["services", "conditions", "results"]:
                    if step == "conditions":
                        page.locator("#continue").click()
                    elif step == "results":
                        page.locator("#submit").click()
                        expect(page.locator("#result-loading")).to_be_hidden(timeout=120000)
                        expect(page.locator("#results-title")).to_be_focused()
                        assert 0 <= page.locator("#results-title").bounding_box()["y"] < 400
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(args.shots / f"{step}-{size}.png"), full_page=True)
                for path, name in [("/", "selection"), ("/docs-ui", "docs"), ("/tests", "tests")]:
                    page.goto(args.base_url.rstrip("/") + path, wait_until="networkidle")
                    nav = page.get_by_role("navigation", name="Основная навигация", exact=True)
                    for href, label in [("/", "Подбор"), ("/docs-ui", "Документация"), ("/tests", "Тесты")]:
                        link = nav.get_by_role("link", name=label, exact=True)
                        expect(link).to_be_visible()
                        expect(link).to_have_attribute("href", href)
                    expect(nav.locator('[aria-current="page"]')).to_have_attribute("href", path)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(args.shots / f"{name}-{size}.png"), full_page=True)
            assert not errors, errors
        finally:
            browser.close()


if __name__ == "__main__":
    main()
