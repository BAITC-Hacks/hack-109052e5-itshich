"""Real Chromium checks of the page, with no dependency on other workers' code."""
import json
import os
import socket
from pathlib import Path
from threading import Event, Thread
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
    page.goto(live_server + "/legacy", wait_until="networkidle")
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
    page.goto(live_server + "/legacy", wait_until="networkidle")
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
    page.goto(live_server + "/legacy", wait_until="networkidle")
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
    page.goto(live_server + "/legacy", wait_until="networkidle")
    with page.expect_response("**/api/match") as response:
        page.get_by_role("button", name="Три ведущих", exact=True).click()
    assert response.value.status == 422
    expect(page.locator("#outcome-title")).to_have_text(detail)
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "error")
    expect(page.locator(".card")).to_have_count(0)
    expect(page.get_by_role("button", name="Подобрать", exact=True)).to_be_enabled()


def open_qalau(page, live_server):
    page.goto(live_server, wait_until="networkidle")
    expect(page.locator("#demos button")).to_have_count(3)


@pytest.mark.e2e
def test_qalau_preserves_backend_order_explanations_and_rejections(page, live_server):
    open_qalau(page, live_server)
    page.locator("#demos button").first.click()
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "matched")
    expect(page.locator("#outcome-title")).to_have_text("Подобрали")
    expect(page.locator(".vendor .vendor-name")).to_have_text(["Арман", "Дана", "Ерлан"])
    expect(page.locator(".vendor .explanation")).to_have_text([
        "Арман: цена от 600000 ₸, свободен 14.11.2026.",
        "Дана: цена от 640000 ₸, свободен 14.11.2026.",
        "Ерлан: цена от 700000 ₸, свободен 14.11.2026.",
    ])
    for label in ["Синтетический профиль", "Цена проставлена", "Город проставлен", "25 %", "До 8 часов"]:
        expect(page.locator(".vendor").first).to_contain_text(label)
    expect(page.locator("#result-footer")).to_contain_text("лексическая")
    expect(page.locator("#result-footer")).to_contain_text("шаблон")
    expect(page.locator("#rejections-list")).to_be_hidden()
    page.get_by_text("Почему остальные не попали (1)", exact=True).click()
    expect(page.locator("#rejections-list")).to_contain_text("Занятый ведущий")
    for reason in ["занят на эту дату", "цена от выше бюджета", "не берёт этот формат",
                   "не работает на этом языке", "максимум часов меньше запрошенной длительности"]:
        expect(page.locator("#rejections-list")).to_contain_text(reason)
    page.get_by_text("Почему остальные не попали (1)", exact=True).click()
    expect(page.locator("#rejections-list")).to_be_hidden()
    screenshot(page, "qalau-matched-desktop.png")


@pytest.mark.e2e
def test_qalau_three_outcomes_and_demo_payloads_at_400px(page, live_server):
    page.set_viewport_size({"width": 400, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    open_qalau(page, live_server)
    colors = set()
    titles = ["Подобрали", "В этом городе такой категории нет",
              "Кандидаты есть, но ни один не проходит по условиям"]
    for index, demo in enumerate(DEMOS):
        with page.expect_request("**/api/match") as sent:
            page.locator("#demos button").nth(index).click()
        assert sent.value.post_data_json == demo["request"]
        expect(page.locator("#banner")).to_have_attribute("data-outcome", demo["expected_outcome"])
        expect(page.locator("#outcome-title")).to_have_text(titles[index])
        expect(page.locator(".vendor")).to_have_count(3 if index == 0 else 0)
        expect(page.locator("#date")).to_have_value(demo["request"]["event_date"])
        expect(page.locator("#category")).to_have_value(demo["request"]["category"])
        if index:
            expect(page.locator("#shortfall")).to_have_text(
                "В этом городе нет профилей этой категории." if index == 1
                else "Кандидат есть, но условия не выполнены.")
        colors.add(page.locator("#banner").evaluate("el => getComputedStyle(el).backgroundColor"))
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot(page, f"qalau-{demo['expected_outcome']}-400px.png")
    assert len(colors) == 3
    assert errors == []


@pytest.mark.e2e
def test_qalau_meta_options_and_optional_fields(page, live_server):
    open_qalau(page, live_server)
    expect(page.locator("[data-service='Ведущий']")).to_contain_text("Профилей: 4")
    expect(page.locator("[data-service='Ведущий церемонии']")).to_have_count(0)
    expect(page.locator("#date")).to_have_attribute("min", "2026-09-23")
    expect(page.locator("#date")).to_have_attribute("max", "2026-12-31")
    page.locator("#city").select_option("Астана")
    expect(page.locator("[data-service='Ведущий']")).to_contain_text("Профилей: 0")
    page.locator("#city").select_option("Алматы")
    page.locator("#continue").click()
    page.locator("#date").fill("2026-11-14")
    page.locator("#budget").fill("800000")
    page.locator("#extra summary").click()
    page.locator("#language").select_option("")
    page.locator("#hours").fill("")
    with page.expect_request("**/api/match") as sent:
        page.locator("#submit").click()
    assert sent.value.post_data_json == test_api.REQUEST | {"duration_hours": None, "language": None}
    expect(page.locator("#outcome-title")).to_have_text("Подобрали")


@pytest.mark.e2e
def test_qalau_422_and_retry(page, live_server, fake_pipeline, monkeypatch):
    detail = "Календарь занятости известен только на 23.09.2026 — 31.12.2026."

    def invalid(*args):
        raise fake_pipeline.RequestError(detail)

    monkeypatch.setattr(fake_pipeline, "run", invalid)
    open_qalau(page, live_server)
    page.locator("#demos button").first.click()
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "error")
    expect(page.locator("#outcome-title")).to_have_text(detail)
    expect(page.locator(".vendor")).to_have_count(0)
    monkeypatch.setattr(fake_pipeline, "run", test_api.fake_run)
    page.get_by_role("button", name="Повторить подбор", exact=True).click()
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "matched")


@pytest.mark.e2e
def test_qalau_example_url_submits_backend_demo(page, live_server):
    page.goto(live_server + "/?example=3", wait_until="networkidle")
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "none_eligible")
    expect(page.locator("#budget")).to_have_value("1")


@pytest.mark.e2e
def test_qalau_loading_until_backend_response(page, live_server, fake_pipeline, monkeypatch):
    released = Event()

    def delayed(*args):
        assert released.wait(timeout=10), "Test did not release the pending match"
        return test_api.fake_run(*args)

    monkeypatch.setattr(fake_pipeline, "run", delayed)
    open_qalau(page, live_server)
    try:
        page.locator("#demos button").first.click()
        expect(page.locator("#result-content")).to_have_attribute("aria-busy", "true")
        expect(page.locator(".loading")).to_be_visible()
        expect(page.locator("#submit")).to_be_disabled()
        expect(page.locator(".vendor")).to_have_count(0)
    finally:
        released.set()
    expect(page.locator("#banner")).to_have_attribute("data-outcome", "matched")
    expect(page.locator("#submit")).to_be_enabled()


@pytest.mark.e2e
@pytest.mark.parametrize('swapped', [False, True])
def test_live_tests_rerun_compares_saved_and_fresh(page, live_server, fake_pipeline, monkeypatch, swapped):
    from matcher.api_types import MatchRequestDTO
    saved = fake_pipeline.answer(MatchRequestDTO(**test_api.REQUEST).to_domain())
    page.route('**/api/live-tests', lambda route: route.fulfill(json={
        'generated_at': '2026-09-23T00:00:00Z', 'git_sha': 'fake', 'model': 'fake',
        'cases': [{'name': 'Проверка сравнения', 'request': test_api.REQUEST, **saved}]}))
    if swapped:
        original = fake_pipeline.answer
        def reverse(request):
            result = original(request)
            result['cards'].reverse()
            return result
        monkeypatch.setattr(fake_pipeline, 'answer', reverse)
    page.goto(live_server + '/tests#live-tests', wait_until='networkidle')
    expect(page.get_by_role('heading', name='Лайв-тесты пайплайна')).to_be_visible()
    page.get_by_role('button', name='Прогнать заново', exact=True).click()
    expect(page.locator('.diff-order')).to_have_text('Порядок id: ' + ('изменилось' if swapped else 'совпало'))
    expect(page.locator('.diff-primary')).to_have_text('Главные коды: совпало')
    expect(page.locator('.diff-explanation')).to_have_text('Объяснения: совпало')
    expect(page.locator('.fresh-result .card-name')).to_have_count(3)
    expect(page.get_by_role('button', name='Прогнать заново', exact=True)).to_be_enabled()
    screenshot(page, 'live-tests-swapped.png' if swapped else 'live-tests-matched.png')


@pytest.mark.e2e
def test_live_tests_api_error_and_run_all(page, live_server):
    saved = dict(outcome='none_eligible', pool_size=1, eligible_count=0,
                 cards=[], shortfall_note='Нет свободных', timing_ms=1)
    page.route('**/api/live-tests', lambda route: route.fulfill(json={'cases': [
        {'name': name, 'request': test_api.REQUEST, **saved} for name in ['Первый', 'Второй']]}))
    page.route('**/api/match', lambda route: route.fulfill(status=422, json={'detail': 'Ошибка календаря'}))
    page.goto(live_server + '/tests#live-tests', wait_until='networkidle')
    page.get_by_role('button', name='Прогнать все', exact=True).click()
    expect(page.locator('.run-status')).to_have_text(['Ошибка календаря', 'Ошибка календаря'])
    expect(page.get_by_role('button', name='Прогнать все', exact=True)).to_be_enabled()
    expect(page.locator('.saved-result .shortfall')).to_have_count(2)
