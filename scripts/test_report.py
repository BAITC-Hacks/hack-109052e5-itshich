#!/usr/bin/env python3
"""Publish pytest JUnit results as the QALAU test dashboard's JSON feed."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def parse_junit(path):
    suites = {}
    for case in ET.parse(path).getroot().iter('testcase'):
        classname = case.get('classname', '')
        parts = classname.split('.')
        module_end = next((i for i, part in enumerate(parts) if part.startswith('test_')), None)
        name = case.get('name', '')
        if module_end is not None:
            inferred = '/'.join(parts[:module_end + 1]) + '.py'
            name = '::'.join(parts[module_end + 1:] + [name])
        else:
            inferred = classname or 'unknown'
        path_name = case.get('file') or inferred
        path_name = path_name.replace('\\', '/')
        kind = ('e2e' if 'tests/e2e/' in path_name else
                'property' if 'test_properties' in path_name else
                'api' if 'test_api' in path_name else
                'unit' if path_name.startswith('tests/') else 'other')
        suite = suites.setdefault(path_name, dict(name=path_name, kind=kind, passed=0,
            failed=0, skipped=0, errors=0, duration_s=0., tests=[]))
        status, message = 'passed', ''
        for tag, value in [('error', 'error'), ('failure', 'failed'), ('skipped', 'skipped')]:
            detail = case.find(tag)
            if detail is not None:
                status = value
                if tag != 'skipped':
                    lines = (detail.text or detail.get('message') or '').strip().splitlines()
                    message = lines[0] if lines else ''
                break
        duration = float(case.get('time', '0'))
        suite['errors' if status == 'error' else status] += 1
        suite['tests'].append(dict(name=name, status=status, duration_s=duration, message=message))
    result = [suites[key] for key in sorted(suites)]
    for suite in result:
        suite['tests'].sort(key=lambda test: (test['name'], test['status'], test['message'], test['duration_s']))
        suite['duration_s'] = round(sum(t['duration_s'] for t in suite['tests']), 6)
    return result


def summarize(suites):
    return dict(total=sum(len(s['tests']) for s in suites),
                **{key: sum(s[key] for s in suites) for key in ('passed', 'failed', 'skipped', 'errors')},
                duration_s=round(sum(s['duration_s'] for s in suites), 6))


def write_report(source, output):
    suites = parse_junit(source)
    try:
        sha = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = 'unknown'
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), git_sha=sha,
                  summary=summarize(suites), suites=suites)
    demo_path = ROOT / 'demo/queries.json'
    if demo_path.exists():
        keys = ('name', 'request', 'expected_outcome', 'expected_card_ids')
        report['demo'] = [{key: entry[key] for key in keys} for entry in json.loads(demo_path.read_text(encoding='utf-8'))]
    output.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically so HTTP readers never see a partially written report.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    temporary.replace(output)
    summary = report['summary']
    print(f"{summary['total']} tests: {summary['passed']} passed, {summary['failed']} failed, "
          f"{summary['errors']} errors, {summary['skipped']} skipped ({summary['duration_s']:.2f}s) → {output}")
    return bool(summary['failed'] or summary['errors'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--junit', type=Path, help='Read existing XML without running pytest')
    parser.add_argument('--e2e-only', action='store_true', help='Run only tests/e2e')
    parser.add_argument('--strict', action='store_true', help='Exit nonzero for test failures/errors')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/test_report.json')
    args = parser.parse_args(argv)
    try:
        if args.junit:
            failed = write_report(args.junit, args.output)
        else:
            with tempfile.TemporaryDirectory(prefix='qalau-tests-') as directory:
                source = Path(directory) / 'junit.xml'
                command = ['uv', 'run', 'pytest', '-q', '-o', 'addopts=', f'--junitxml={source}']
                if args.e2e_only:
                    command.append('tests/e2e')
                run = subprocess.run(command, cwd=ROOT)
                failed = write_report(source, args.output) or run.returncode != 0
    except (OSError, ET.ParseError, ValueError) as exc:
        parser.exit(2, f'Cannot generate report: {exc}\n')
    return int(args.strict and failed)


if __name__ == '__main__':
    raise SystemExit(main())
