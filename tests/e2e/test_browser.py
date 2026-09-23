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
    expect(page.locator("#rejections-list")).to_be_visible()
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
@pytest.mark.parametrize("path", ["/", "/docs-ui", "/tests"])
def test_shared_navigation(page, live_server, path):
    page.set_viewport_size({"width": 400, "height": 900})
    page.goto(live_server + path, wait_until="networkidle")
    nav = page.get_by_role("navigation", name="Основная навигация", exact=True)
    for label, href in [("Подбор", "/"), ("Документация", "/docs-ui"), ("Тесты", "/tests")]:
        link = nav.get_by_role("link", name=label, exact=True)
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", href)
        if href == path:
            expect(link).to_have_attribute("aria-current", "page")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.e2e
def test_qalau_navigation_persistence_and_result_focus(page, live_server):
    open_qalau(page, live_server)
    expect(page.locator('#demos button')).to_have_text(["Три ведущих", "Нет категории", "Не проходят"])
    expect(page.locator('.journey [data-step="conditions"]')).to_be_disabled()
    page.locator('#continue').click()
    page.locator('#budget').fill('765432')
    page.locator('#date').fill('2026-11-15')
    page.get_by_role('button', name='← Назад', exact=True).click()
    page.locator('.journey [data-step="conditions"]').click()
    expect(page.locator('#budget')).to_have_value('765432')
    page.reload(wait_until='networkidle')
    expect(page.locator('#conditions-screen')).to_be_visible()
    expect(page.locator('#date')).to_have_value('2026-11-15')
    expect(page.locator('#budget')).to_have_value('765432')
    page.locator('#budget').press('Enter')
    expect(page.locator('#banner')).to_have_attribute('data-outcome', 'matched')
    expect(page.locator('#results-title')).to_be_focused()
    bounds = page.locator('#results-title').bounding_box()
    assert 0 <= bounds['y'] < 400
    page.locator('#results-screen').get_by_role('button', name='Изменить условия').first.click()
    expect(page.locator('#budget')).to_have_value('765432')
    page.locator('.journey [data-step="results"]').click()
    expect(page.locator('#results-screen')).to_be_visible()
    page.get_by_role('button', name='← Назад', exact=True).click()
    expect(page.locator('#conditions-screen')).to_be_visible()


@pytest.mark.e2e
def test_qalau_previous_result_survives_loading(page, live_server, fake_pipeline, monkeypatch):
    open_qalau(page, live_server)
    page.locator('#demos button').first.click()
    expect(page.locator('.vendor')).to_have_count(3)
    released = Event()
    def delayed(*args):
        assert released.wait(timeout=10)
        return test_api.fake_run(*args)
    monkeypatch.setattr(fake_pipeline, 'run', delayed)
    try:
        page.locator('#demos button').first.click()
        expect(page.locator('.loading')).to_be_visible()
        expect(page.locator('.vendor')).to_have_count(3)
        page.wait_for_function("Number(getComputedStyle(document.querySelector('.comparison')).opacity) < 1")
        expect(page.locator('#submit')).to_contain_text('Подбираем')
    finally:
        released.set()
    expect(page.locator('#result-content')).not_to_have_attribute('aria-busy', 'true')


@pytest.mark.e2e
@pytest.mark.parametrize(('field', 'value'), [('date', '2027-01-01'), ('budget', '0'), ('hours', '25')])
def test_qalau_invalid_fields_do_not_send_requests(page, live_server, field, value):
    open_qalau(page, live_server)
    requests = []
    page.on('request', lambda request: requests.append(request.url) if '/api/match' in request.url else None)
    page.locator('#continue').click()
    if field == 'hours':
        page.locator('#extra summary').click()
    page.locator('#' + field).fill(value)
    page.locator('#' + field).press('Enter')
    expect(page.locator('#' + field)).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#' + field + '-error')).to_be_visible()
    assert requests == []


@pytest.mark.e2e
def test_qalau_network_error_keeps_edit_action(page, live_server):
    open_qalau(page, live_server)
    page.route('**/api/match', lambda route: route.abort())
    page.locator('#demos button').first.click()
    expect(page.locator('#outcome-title')).to_contain_text('Не удалось связаться с сервером')
    page.locator('#result-content').get_by_role('button', name='Изменить условия').click()
    expect(page.locator('#conditions-screen')).to_be_visible()
    expect(page.locator('#budget')).to_have_value('800000')


@pytest.mark.e2e
def test_qalau_catalog_values_survive_reload(page, live_server):
    open_qalau(page, live_server)
    page.locator('#city').select_option('Астана')
    page.locator('#format').select_option('свадьба')
    page.locator('#service-search').fill('Флорист')
    page.locator('[data-service="Флорист"]').click()
    page.locator('#continue').click()
    page.reload(wait_until='networkidle')
    expect(page.locator('#category')).to_have_value('Флорист')
    expect(page.locator('#city')).to_have_value('Астана')
    expect(page.locator('#format')).to_have_value('свадьба')
    expect(page.locator('#chosen-name')).to_have_text('Флорист')


@pytest.mark.e2e
def test_qalau_scenarios_use_titles_and_russian_fallback_labels(page, live_server):
    names = ['dense', 'rare', 'empty_no_category', 'empty_none_eligible', 'date_pair_a', 'date_pair_b']
    examples = [DEMOS[0] | {'name': name} for name in names]
    examples.append(DEMOS[0] | {'name': 'custom', 'title_ru': 'Свадьба в Алматы'})
    page.route('**/api/demo', lambda route: route.fulfill(json=examples))
    page.goto(live_server, wait_until='networkidle')
    expect(page.locator('#demos button')).to_have_text([
        'Плотная категория', 'Редкая категория', 'Категории нет в городе',
        'Никто не проходит', 'Первая дата', 'Другая дата', 'Свадьба в Алматы',
    ])
