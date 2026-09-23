"""Report contract checks: parsing, CLI isolation, and published count consistency."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/test_report.py'
XML = '''<testsuites><testsuite>
<testcase classname="tests.test_properties.TestThings" name="z" time="0.5"/>
<testcase classname="tests.test_api" name="bad" time="1"><failure message="fallback">first line\nsecond line</failure></testcase>
<testcase classname="tests.e2e.test_browser" name="browser" time="2"><skipped/></testcase>
<testcase classname="tests.test_properties.TestThings" name="a" time="0.25"/>
<testcase file="tests/test_data.py" name="broken" time="0.25"><error>setup broke\ntrace</error></testcase>
<testcase classname="custom" name="misc"/>
</testsuite></testsuites>'''


def module():
    assert SCRIPT.exists(), 'report generator is missing'
    spec = importlib.util.spec_from_file_location('report_generator', SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_parse_kinds_counts_messages_and_order(tmp_path):
    source = tmp_path / 'junit.xml'
    source.write_text(XML)
    suites = module().parse_junit(source)
    assert [(s['name'], s['kind']) for s in suites] == [
        ('custom', 'other'), ('tests/e2e/test_browser.py', 'e2e'),
        ('tests/test_api.py', 'api'), ('tests/test_data.py', 'unit'),
        ('tests/test_properties.py', 'property')]
    assert [t['name'] for t in suites[-1]['tests']] == ['TestThings::a', 'TestThings::z']
    assert suites[-1]['duration_s'] == .75
    assert suites[2]['tests'][0] == dict(name='bad', status='failed', duration_s=1., message='first line')
    assert suites[3]['tests'][0]['status'] == 'error'
    assert module().summarize(suites) == dict(total=6, passed=3, failed=1, errors=1, skipped=1, duration_s=4.)
    root = ET.fromstring(XML)
    root[0][:] = list(reversed(root[0][:]))
    source.write_text(ET.tostring(root, encoding='unicode'))
    assert module().parse_junit(source) == suites


@pytest.mark.parametrize('strict,code', [(False, 0), (True, 1)])
def test_junit_cli_without_pytest_or_git(tmp_path, strict, code):
    assert SCRIPT.exists(), 'report generator is missing'
    source = tmp_path / 'input.xml'
    source.write_text(XML)
    output = tmp_path / 'report.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--junit', str(source), '--output', str(output), *(['--strict'] if strict else [])], env={**os.environ, 'PATH': ''}, capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    report = json.loads(output.read_text())
    assert report['git_sha'] == 'unknown'
    assert report['summary']['total'] == 6
    assert '6' in result.stdout
    assert len(report['demo']) == 6
    datetime.fromisoformat(report['generated_at'])


def test_committed_report_shape():
    path = ROOT / 'data/test_report.json'
    assert path.exists(), 'published report is missing'
    report = json.loads(path.read_text())
    assert {'generated_at', 'git_sha', 'summary', 'suites'} <= report.keys()
    datetime.fromisoformat(report['generated_at'])
    counts = dict(total=0, passed=0, failed=0, skipped=0, errors=0)
    duration = 0
    for suite in report['suites']:
        assert {'name', 'kind', 'passed', 'failed', 'skipped', 'duration_s', 'tests'} <= suite.keys()
        assert suite['kind'] in {'unit', 'property', 'api', 'e2e', 'other'}
        for status in ('passed', 'failed', 'skipped'):
            assert suite[status] == sum(t['status'] == status for t in suite['tests'])
        for test in suite['tests']:
            assert {'name', 'status', 'duration_s', 'message'} <= test.keys()
            assert test['status'] in {'passed', 'failed', 'skipped', 'error'}
            counts['errors' if test['status'] == 'error' else test['status']] += 1
            counts['total'] += 1
        duration += suite['duration_s']
    assert all(report['summary'][key] == value for key, value in counts.items())
    assert report['summary']['duration_s'] == pytest.approx(duration)


@pytest.mark.e2e
def test_dashboard_mobile_filters_and_empty_state(tmp_path, monkeypatch):
    from tests.e2e.test_browser import page as browser_fixture
    from playwright.sync_api import expect
    source = tmp_path / 'report.xml'
    source.write_text(XML)
    output = tmp_path / 'report.json'
    module().write_report(source, output)
    report = json.loads(output.read_text())
    # Reuse the repository's Chromium availability/sandbox handling.
    generator = browser_fixture.__wrapped__(monkeypatch)
    browser_page = next(generator)
    try:
        def serve(route):
            path = route.request.url.split('http://report.local', 1)[1]
            if path == '/api/tests':
                route.fulfill(json=report)
            else:
                file = ROOT / 'web/qalau' / ('tests.html' if path == '/tests' else Path(path).name)
                route.fulfill(path=str(file)) if file.is_file() else route.abort()
        browser_page.route('http://report.local/**', serve)
        browser_page.set_viewport_size({'width': 400, 'height': 900})
        browser_page.goto('http://report.local/tests')
        expect(browser_page.locator('tbody tr')).to_have_count(6)
        assert browser_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser_page.get_by_role('button', name='Ошибки', exact=True).click()
        expect(browser_page.locator('tbody tr')).to_have_count(2)
        browser_page.locator('details').first.locator('summary').click()
        expect(browser_page.get_by_text('first line', exact=True)).to_be_visible()
        browser_page.get_by_role('button', name='E2E', exact=True).click()
        expect(browser_page.locator('tbody tr')).to_have_count(1)
        expect(browser_page.locator('#demos article')).to_have_count(6)
        report['suites'] = []
        browser_page.reload()
        expect(browser_page.get_by_text('отчёт ещё не сформирован: uv run python scripts/test_report.py', exact=True)).to_be_visible()
    finally:
        generator.close()
