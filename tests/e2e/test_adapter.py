"""Exercise the browser API adapter without needing a Chromium process."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_api import DEMOS, REQUEST, client, fake_pipeline  # noqa: F401


def test_adapter_preserves_dto_content_and_translates_form_request(client):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the API adapter contract check")
    payload = {
        "meta": client.get("/api/meta").json(),
        "demos": DEMOS,
        "results": [client.post("/api/match", json=request).json() for request in
                    [REQUEST, REQUEST | {"category": "Нет категории"}, REQUEST | {"budget_kzt": 1}]],
        "request": REQUEST,
    }
    # A backend order deliberately opposed to ascending price catches local sorting.
    payload["results"][0]["cards"].reverse()
    script = r'''
import assert from 'node:assert/strict';
import {loadOptions, loadScenarios, selectProfiles} from './web/qalau/api.mjs';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const data = JSON.parse(input);
let resultIndex = 0;
globalThis.fetch = async (url, init) => {
  if (url === '/api/meta') return Response.json(data.meta);
  if (url === '/api/demo') return Response.json(data.demos);
  assert.equal(url, '/api/match');
  assert.equal(init.method, 'POST');
  assert.deepEqual(JSON.parse(init.body), data.request);
  return Response.json(data.results[resultIndex++]);
};
assert.deepEqual(await loadOptions(), data.meta);
const scenarios = await loadScenarios();
const query = {city:'Алматы', date:'2026-11-14', format:'корпоратив', category:'Ведущий',
               budget:800000, hours:5, language:'русский'};
assert.deepEqual(scenarios[0].query, query);
for (const state of ['success', 'absent', 'filtered']) {
  const result = await selectProfiles(query);
  const dto = data.results[resultIndex - 1];
  assert.equal(result.state, state);
  assert.deepEqual(result.items.map(p => p.id), dto.cards.map(p => p.id));
  assert.deepEqual(result.items.map(p => p.explanation), dto.cards.map(p => p.explanation));
  assert.deepEqual(result.items.map(p => p.facts), dto.cards.map(p => p.facts));
  assert.deepEqual(result.rejections, dto.rejections);
  assert.equal(result.pool, dto.pool_size);
  assert.equal(result.total, dto.eligible_count);
  assert.equal(result.outcome_title_ru, dto.outcome_title_ru);
  assert.equal(result.shortfall_note, dto.shortfall_note);
  assert.equal(result.semantic_backend, dto.semantic_backend);
  assert.equal(result.timing_ms, dto.timing_ms);
  if (state === 'success') {
    assert.deepEqual(result.reasons, {busy:1, budget:1, format:1, language:1, hours:1});
    assert.deepEqual(result.items[0].badges, ['Синтетический профиль', 'Цена проставлена', 'Город проставлен']);
    assert.deepEqual(result.items.map(p => p.name), ['Ерлан', 'Дана', 'Арман']);
    assert.equal(result.items[0].price, 700000);
    assert.equal(result.items[0].explanation_source, 'template');
  }
}
globalThis.fetch = async (url, init) => {
  assert.equal(JSON.parse(init.body).duration_hours, null);
  assert.equal(JSON.parse(init.body).language, null);
  return Response.json({detail:'Дата за пределами календаря.'}, {status:422});
};
await assert.rejects(selectProfiles({...query, hours:'', language:''}), /Дата за пределами календаря\./);
globalThis.fetch = async () => {throw new TypeError('Failed to fetch');};
await assert.rejects(selectProfiles(query), /Не удалось связаться с сервером/);
globalThis.fetch = async () => new Response('upstream error', {status:502});
await assert.rejects(selectProfiles(query), /Сервер вернул непонятный ответ/);
'''
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            cwd=Path(__file__).resolve().parents[2], input=json.dumps(payload),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
