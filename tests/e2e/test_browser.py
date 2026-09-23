"""Real Chromium checks of the page, with no dependency on other workers' code."""
import json
import os
import socket
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import pytest
import uvicorn
from playwright.sync_api import Error, expect, sync_playwright

from tests import test_api

DEMOS = test_api.DEMOS
fake_pipeline = test_api.fake_pipeline


@pytest.fixture
def live_server(fake_pipeline, monkeypatch, tmp_path):
    import app
    demos = tmp_path / "queries.json"
    demos.write_text(json.dumps(DEMOS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(app, "DEMO_PATH", demos)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app.app, log_level="error", lifespan="off", ws="none"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = monotonic() + 5
        try:
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(.01)
            assert server.started, "Uvicorn failed to start"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        assert not thread.is_alive(), "Uvicorn did not shut down"


@pytest.fixture
def page(monkeypatch):
    local_browsers = Path(__file__).resolve().parents[2] / ".work" / "ms-playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and local_browsers.is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(local_browsers.resolve()))
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium is not installed; run uv run playwright install chromium")
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium is not installed; run uv run playwright install chromium")
            if "bootstrap_check_in" in str(exc) and "Permission denied" in str(exc):
                pytest.skip("macOS sandbox blocks Chromium launch: MachPort permission denied")
            raise
        try:
            yield browser.new_page(viewport={"width": 1280, "height": 1000}, locale="ru-RU")
        finally:
            browser.close()


def screenshot(page, name):
    directory = os.environ.get("E2E_SCREENSHOTS")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path / name), full_page=True)


@pytest.mark.e2e
def test_ranked_cards_and_rejections_toggle(page, live_server):
    page.goto(live_server, wait_until="networkidle")
    expect(page.get_by_role("heading", name="Подбор подрядчиков", exact=True)).to_be_visible()
    page.get_by_role("button", name="Три ведущих", exact=True).click()
    expect(page.locator("#outcome-title")).to_have_text("Подобрали")
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "matched")
    expect(page.locator(".card h3")).to_have_text(["Арман", "Дана", "Ерлан"])
    expect(page.locator(".rank")).to_have_text(["1", "2", "3"])
    expect(page.locator(".card").first).to_contain_text("синтетический профиль")
    expect(page.locator(".card").first).to_contain_text("цена проставлена")
    expect(page.locator(".card").first).to_contain_text("город проставлен")
    expect(page.locator(".card").first).to_contain_text("запас бюджета 25 %")
    expect(page.locator("#result-footer")).to_contain_text("семантика: lexical")
    expect(page.locator("#result-footer")).to_contain_text("источник объяснений: template")
    summary = page.get_by_text("Почему остальные не попали (1)", exact=True)
    expect(page.locator("#rejections-list")).to_be_hidden()
    summary.click()
    expect(page.locator("#rejections-list")).to_be_visible()
    expect(page.locator("#rejections-list")).to_contain_text("Занятый ведущий")
    expect(page.locator("#rejections-list")).to_contain_text("занят на эту дату")
    screenshot(page, "matched-desktop.png")
    summary.click()
    expect(page.locator("#rejections-list")).to_be_hidden()


@pytest.mark.e2e
def test_all_three_outcomes_are_distinct_at_400px(page, live_server):
    errors = []
    page.on("pageerror", lambda err: errors.append(err))
    page.set_viewport_size({"width": 400, "height": 900})
    page.goto(live_server, wait_until="networkidle")
    colors = set()
    titles = ["Подобрали", "В этом городе такой категории нет",
              "Кандидаты есть, но ни один не проходит по условиям"]
    for demo, title in zip(DEMOS, titles):
        page.get_by_role("button", name=demo["name"], exact=True).click()
        expect(page.locator("#outcome-title")).to_have_text(title)
        expect(page.locator("#banner")).to_have_attribute("data-outcome", demo["expected_outcome"])
        expect(page.locator(".card")).to_have_count(3 if demo["expected_outcome"] == "matched" else 0)
        if demo["expected_outcome"] != "matched":
            expect(page.locator("#shortfall")).to_be_visible()
        colors.add(page.locator("#banner").evaluate("el => getComputedStyle(el).backgroundColor"))
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot(page, f"{demo['expected_outcome']}-400px.png")
    assert len(colors) == 3
    assert errors == []


@pytest.mark.e2e
def test_form_uses_catalog_counts_dates_and_optional_fields(page, live_server):
    page.goto(live_server, wait_until="networkidle")
    expect(page.locator("#category option")).to_have_text(["Ведущий (4)", "Флорист (0)"])
    expect(page.locator("#event_date")).to_have_attribute("min", "2026-09-23")
    expect(page.locator("#event_date")).to_have_attribute("max", "2026-12-31")
    page.locator("#city").select_option("Астана")
    expect(page.locator("#category option")).to_have_text(["Ведущий (0)", "Флорист (1)"])
    page.locator("#city").select_option("Алматы")
    page.locator("#event_date").fill("2026-11-14")
    with page.expect_request("**/api/match") as sent:
        page.get_by_role("button", name="Подобрать", exact=True).click()
    assert sent.value.post_data_json == {
        "city": "Алматы", "event_date": "2026-11-14", "event_format": "корпоратив",
        "category": "Ведущий", "budget_kzt": 800000, "duration_hours": None, "language": None,
    }
    expect(page.locator("#outcome-title")).to_have_text("Подобрали")


@pytest.mark.e2e
def test_422_detail_is_visible_and_form_stays_usable(page, live_server, fake_pipeline, monkeypatch):
    detail = "Календарь занятости известен только на 23.09.2026 — 31.12.2026."

    def invalid(*args):
        raise fake_pipeline.RequestError(detail)

    monkeypatch.setattr(fake_pipeline, "run", invalid)
    page.goto(live_server, wait_until="networkidle")
    with page.expect_response("**/api/match") as response:
        page.get_by_role("button", name="Три ведущих", exact=True).click()
    assert response.value.status == 422
    expect(page.locator("#outcome-title")).to_have_text(detail)
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "error")
    expect(page.locator(".card")).to_have_count(0)
    expect(page.get_by_role("button", name="Подобрать", exact=True)).to_be_enabled()
