"""Live snapshot contract and regression comparisons, without network calls."""
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from matcher import pipeline
from matcher.api_types import MatchRequestDTO, to_dto
from matcher.model import Reason, ReasonFamily
from tests.test_api import REQUEST, fake_run, fake_explain


def response():
    result = fake_run(MatchRequestDTO(**REQUEST).to_domain(), [], SimpleNamespace(name='lexical'))
    result = replace(result, cards=tuple(replace(card, reasons=(
        Reason('BUDGET_HEADROOM', ReasonFamily.BUDGET, {}, primary=True),
        Reason('AVAILABILITY_SCARCE', ReasonFamily.AVAILABILITY, {}),
        Reason('FUTURE_CODE', ReasonFamily.FORMAT, {}),
    )) for card in result.cards))
    return to_dto(result, fake_explain(result), 12.5, 'lexical')


def test_card_reasons_are_public_labeled_and_have_one_primary():
    for card in response()['cards']:
        assert [r['code'] for r in card['reasons']] == ['BUDGET_HEADROOM', 'FUTURE_CODE']
        assert sum(r['primary'] for r in card['reasons']) == 1
        assert card['reasons'][0]['label'] == 'Запас бюджета'
        assert card['reasons'][1]['label'] == 'FUTURE_CODE'


@pytest.mark.parametrize('exists', [False, True])
def test_live_tests_route(tmp_path, monkeypatch, exists):
    import app
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    saved = {'generated_at': 'now', 'git_sha': 'abc', 'model': 'fake', 'cases': []}
    if exists:
        (tmp_path / 'data').mkdir()
        (tmp_path / 'data/live_tests.json').write_text(json.dumps(saved))
    with TestClient(app.app) as client:
        result = client.get('/api/live-tests')
    assert result.status_code == 200
    assert result.json() == (saved if exists else {'cases': [], 'note': 'лайв-тесты ещё не прогнаны'})


def test_runner_shape_and_three_axis_check(tmp_path, monkeypatch, capsys):
    from scripts import run_live_tests as runner
    fresh = response()
    monkeypatch.setattr(pipeline, 'answer', lambda request: deepcopy(fresh))
    source, output = tmp_path / 'requests.json', tmp_path / 'live.json'
    source.write_text(json.dumps([{'name': 'Тест', 'request': REQUEST}]))
    args = ['--requests', str(source), '--output', str(output)]
    assert runner.main(args) == 0
    saved_bytes = output.read_bytes()
    saved = json.loads(saved_bytes)
    assert {'generated_at', 'git_sha', 'model', 'cases'} <= saved.keys()
    case = saved['cases'][0]
    assert {'name', 'request', 'outcome', 'pool_size', 'eligible_count', 'shortfall_note', 'timing_ms', 'cards'} <= case.keys()
    assert case['request'] == REQUEST and case['timing_ms'] == 12.5
    assert case['cards'][0]['flags'] == ['synthetic', 'price_imputed', 'city_imputed']
    assert case['cards'][0]['reasons'][0]['primary'] is True
    assert '| Тест |' in capsys.readouterr().out
    assert runner.main(args + ['--check']) == 0
    for axis in ['order', 'primary', 'explanation']:
        fresh = response()
        if axis == 'order':
            fresh['cards'].reverse()
        elif axis == 'primary':
            fresh['cards'][0]['reasons'][0]['code'] = 'BUDGET_FITS'
        else:
            fresh['cards'][0]['explanation'] = 'Новый текст'
        assert runner.main(args + ['--check']) == 1
        assert output.read_bytes() == saved_bytes


def test_runner_keeps_request_errors(tmp_path, monkeypatch):
    from scripts import run_live_tests as runner
    def invalid(request):
        raise pipeline.RequestError('Дата вне календаря')
    monkeypatch.setattr(pipeline, 'answer', invalid)
    source = tmp_path / 'requests.json'
    source.write_text(json.dumps([{'name': 'Дата', 'request': REQUEST}]))
    result = runner.run_live_tests(source)
    assert result['cases'][0]['outcome'] == 'request_error'
    assert result['cases'][0]['shortfall_note'] == 'Дата вне календаря'
    assert result['cases'][0]['cards'] == []


def test_test_report_includes_live_summary(tmp_path, monkeypatch):
    from scripts import test_report
    monkeypatch.setattr(test_report, 'ROOT', tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/live_tests.json').write_text(json.dumps({
        'cases': [{}, {}], 'generated_at': 'today', 'git_sha': 'abc'}))
    source = tmp_path / 'junit.xml'
    source.write_text('<testsuite><testcase name="ok" classname="tests.test_live"/></testsuite>')
    output = tmp_path / 'report.json'
    test_report.write_report(source, output)
    assert json.loads(output.read_text())['live_tests'] == {
        'cases_count': 2, 'generated_at': 'today', 'git_sha': 'abc'}


def test_saved_requests_cover_research_and_preserve_legacy_cases():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    requests = json.loads((root / 'data/live_tests_requests.json').read_text())
    demos = json.loads((root / 'demo/queries.json').read_text())
    legacy = json.loads((root / 'demo/cases.json').read_text())
    assert [MatchRequestDTO(**c['request']) for c in requests[:6]] == [
        MatchRequestDTO(**c['request']) for c in demos]
    assert [c['request'] for c in requests] == [c['request'] for c in legacy]
    assert len(requests) == 18
    assert requests[6]['request']['language'] == 'английский'
    assert requests[7]['request']['category'] == 'Банкетный зал'
    assert requests[8]['request']['budget_kzt'] == 350000
    assert requests[9]['request']['language'] == 'казахский'
    assert requests[10]['request']['budget_kzt'] == 1900000
    saved = json.loads((root / 'data/live_tests.json').read_text())
    assert [c['request'] for c in saved['cases']] == [c['request'] for c in requests]
    for case in saved['cases']:
        for card in case['cards']:
            assert sum(r['primary'] for r in card['reasons']) == 1
            assert all(r['code'] != 'AVAILABILITY_SCARCE' for r in card['reasons'])
