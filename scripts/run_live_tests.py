#!/usr/bin/env python3
"""Publish pipeline snapshots; --check compares order, primary codes and text."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from matcher import pipeline
from matcher.api_types import CARD_REASON_LABELS, MatchRequestDTO
from scripts.run_cases import card_reasons, evaluate, write_json


def snapshot(name, request, response, reasons=None):
    cards = []
    for card in response.get('cards', []):
        raw_reasons = card.get('reasons', (reasons or {}).get(card['id'], []))
        cards.append(dict(
            **{key: card[key] for key in ('id', 'name', 'price_from_kzt', 'explanation', 'explanation_source')},
            flags=[flag for flag in ('synthetic', 'price_imputed', 'city_imputed') if card.get(flag)],
            reasons=[dict(code=r['code'], primary=bool(r.get('primary')),
                          label=r.get('label', CARD_REASON_LABELS.get(r['code'], r['code'])))
                     for r in raw_reasons if r['code'] != 'AVAILABILITY_SCARCE']))
    return dict(name=name, request=request, outcome=response.get('outcome', 'request_error'),
                pool_size=response.get('pool_size', 0), eligible_count=response.get('eligible_count', 0),
                shortfall_note=response.get('shortfall_note') or response.get('detail'),
                timing_ms=response.get('timing_ms', 0), cards=cards)


def run_live_tests(requests_path=ROOT / 'data/live_tests_requests.json'):
    cases = json.loads(Path(requests_path).read_text(encoding='utf-8'))
    results = []
    for case in cases:
        request = MatchRequestDTO.model_validate(case['request']).to_domain()
        started = perf_counter()
        try:
            response = pipeline.answer(request)
        except pipeline.RequestError as exc:
            response = dict(status_code=422, detail=str(exc), timing_ms=round((perf_counter()-started)*1000, 2))
        reasons = card_reasons(request, response)
        result = snapshot(case['name'], case['request'], response, reasons)
        if 'expect' in case:
            result['checks'] = evaluate(case['expect'], response, reasons)
        results.append(result)
    sha = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip() or 'unknown'
    return dict(generated_at=datetime.now(timezone.utc).isoformat(), git_sha=sha,
                model=os.getenv('LLM_MODEL', 'gpt-5.4-mini'), cases=results)


def signature(case):
    """Compare reasons/text by identity so order changes are a separate axis."""
    cards = case['cards']
    return ([c['id'] for c in cards],
            {c['id']: [r['code'] for r in c['reasons'] if r['primary']] for c in cards},
            {c['id']: c['explanation'] for c in cards})


def differences(saved, fresh):
    old, new = saved['cases'], fresh['cases']
    if [(c['name'], c['request']) for c in old] != [(c['name'], c['request']) for c in new]:
        return ['Изменился список запросов']
    return [f"{after['name']}: {axis}" for before, after in zip(old, new)
            for axis, a, b in zip(('порядок id', 'главные коды', 'объяснения'), signature(before), signature(after))
            if a != b]


def markdown(report):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', '<br>')
    print('| Кейс | Вход | Исход | Карточки по порядку | Главные коды | Объяснения | мс |')
    print('| --- | --- | --- | --- | --- | --- | --- |')
    for case in report['cases']:
        cards = case['cards']
        values = [case['name'], json.dumps(case['request'], ensure_ascii=False), case['outcome'],
                  ' → '.join(c['id'] for c in cards),
                  '; '.join(', '.join(r['code'] for r in c['reasons'] if r['primary']) for c in cards),
                  '<br>'.join(c['explanation'] for c in cards) or case['shortfall_note'] or '—', case['timing_ms']]
        print('| ' + ' | '.join(cell(value) for value in values) + ' |')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=Path, default=ROOT / 'data/live_tests_requests.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/live_tests.json')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    load_dotenv(ROOT / '.env')
    saved = None
    if args.check:
        if not args.output.exists():
            print(f'Нет сохранённого файла: {args.output}', file=sys.stderr)
            return 1
        saved = json.loads(args.output.read_text(encoding='utf-8'))
    fresh = run_live_tests(args.requests)
    markdown(fresh)
    if args.check:
        changes = differences(saved, fresh)
        print('\n'.join(changes) if changes else 'Все три оси совпали.')
        return int(bool(changes))
    write_json(args.output, fresh)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
