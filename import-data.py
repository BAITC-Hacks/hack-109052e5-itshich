"""Prepare a local, Git-ignored catalog from a separately provided CSV."""
import csv
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('Usage: python import-data.py PATH_TO_CSV')
source = Path(sys.argv[1])
with source.open(encoding='utf-8-sig', newline='') as stream:
    rows = list(csv.DictReader(stream))
required = {'id', 'anon_name', 'categories', 'city', 'price_from_kzt', 'event_formats', 'languages', 'max_hours', 'busy_dates', 'description', 'synthetic', 'city_imputed', 'price_imputed'}
if not rows or not required.issubset(rows[0]):
    raise SystemExit('CSV does not contain the required catalog columns.')
target = Path(__file__).parent / 'source-data.mjs'
target.write_text('export const sourceRows = ' + json.dumps(rows, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')
print(f'Local catalog prepared: {len(rows)} profiles. source-data.mjs is excluded from Git.')
