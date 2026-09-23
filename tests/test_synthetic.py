"""Contracts for the separately labelled, reproducible synthetic extension."""
import csv
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pytest

from matcher.data import load_contractors
from matcher.embeddings import content_key, split_sentences
from matcher.model import CALENDAR_END, CALENDAR_START
from matcher.pipeline import DATA_PATH

ROOT = Path(__file__).resolve().parents[1]
EXTRA = DATA_PATH.with_name("synthetic_extra.csv")


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.fixture(scope="module")
def extra_rows():
    return read_rows(EXTRA)


def test_generator_is_deterministic_and_matches_committed_csv(tmp_path):
    outputs = [tmp_path / name for name in ("first.csv", "second.csv")]
    for output in outputs:
        subprocess.run([sys.executable, str(ROOT / "scripts/make_synthetic.py"),
                        "--output", str(output)], cwd=tmp_path, check=True, capture_output=True)
    assert outputs[0].read_bytes() == outputs[1].read_bytes() == EXTRA.read_bytes()


def test_schema_and_flags_match_source(extra_rows):
    assert EXTRA.read_bytes().splitlines()[0] == DATA_PATH.read_bytes().splitlines()[0]
    for row in extra_rows:
        assert None not in row and all(value is not None for value in row.values())
        assert row["synthetic"] == "True"
        assert row["city_imputed"] == row["price_imputed"] == "False"
        assert int(row["price_from_kzt"]) > 0
        if set(row["categories"].split("|")) & {"Флорист", "Декоратор", "Подарки и сувениры"}:
            assert row["max_hours"] == ""
        else:
            assert int(row["max_hours"]) > 0


def test_ids_are_unique_and_disjoint_from_source(extra_rows):
    ids = [row["id"] for row in extra_rows]
    assert ids == [f"SYN-{i:05d}" for i in range(1, 13)]
    assert len(ids) == len(set(ids)) == 12
    assert not set(ids) & {row["id"] for row in read_rows(DATA_PATH)}


def test_busy_dates_are_iso_in_window_with_seasonal_load(extra_rows):
    for row in extra_rows:
        tokens = row["busy_dates"].split("|")
        days = [date.fromisoformat(token) for token in tokens]
        assert tokens == sorted({day.isoformat() for day in days})
        assert all(CALENDAR_START <= day <= CALENDAR_END for day in days)
        autumn = [day for day in days if day.month < 12]
        december = [day for day in days if day.month == 12]
        assert 0.35 <= len(autumn) / 69 <= 0.45
        assert 0.70 <= len(december) / 31 <= 0.80
        weekends = {date(2026, 12, day) for day in range(1, 32)
                    if date(2026, 12, day).weekday() >= 5}
        assert len(weekends & set(december)) / len(weekends) >= 0.9


def test_profiles_fill_gaps_without_entering_pinned_demo_pools(extra_rows):
    forbidden = {("Алматы", "Ведущий"), ("Алматы", "Флорист"), ("Астана", "Декоратор")}
    assert all((row["city"], category) not in forbidden
               for row in extra_rows for category in row["categories"].split("|"))
    assert sum("казахский" in row["languages"].split("|") for row in extra_rows) >= 3
    photographer = next(row for row in extra_rows if row["categories"] == "Фотограф")
    assert photographer["city"] == "Алматы"
    assert {"казахский", "английский"} <= set(photographer["languages"].split("|"))
    assert len({row["description"] for row in extra_rows}) == 12
    assert all(2 <= len(split_sentences(row["description"])) <= 4 for row in extra_rows)


def test_loader_merges_78_profiles_with_12_new_synthetic(extra_rows):
    contractors = load_contractors(DATA_PATH)
    assert len(contractors) == len({c.id for c in contractors}) == 78
    extra_ids = {row["id"] for row in extra_rows}
    added = [c for c in contractors if c.id in extra_ids]
    assert len(added) == 12 and all(c.synthetic for c in added)
    # The original source already has 13 synthetic profiles; retain their flags.
    original_count = sum(row["synthetic"] == "True" for row in read_rows(DATA_PATH))
    assert sum(c.synthetic for c in contractors) == original_count + 12


def test_new_descriptions_and_sentences_have_shipped_embeddings(extra_rows):
    cache = json.loads((ROOT / "data/embeddings.json").read_text(encoding="utf-8"))
    for row in extra_rows:
        for text in [row["description"], *split_sentences(row["description"])]:
            assert content_key(cache["model"], text) in cache["vectors"]


def test_embedding_builder_includes_extra_catalogue_and_reuses_vectors(csv_row, write_csv, tmp_path):
    source = write_csv([csv_row])
    extra = csv_row | {"id": "SYN-00001", "synthetic": "True", "description": "Играет на кобызе."}
    write_csv([extra], "synthetic_extra.csv")
    cache = tmp_path / "vectors.json"
    command = [sys.executable, str(ROOT / "scripts/build_embeddings.py"), "--dry-run",
               "--csv", str(source), "--output", str(cache)]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    assert "contractors=2" in first.stdout
    contents = cache.read_bytes()
    second = subprocess.run(command, check=True, capture_output=True, text=True)
    assert "embedded=0" in second.stdout
    assert cache.read_bytes() == contents
    data = json.loads(contents)
    for row in (csv_row, extra):
        for text in [row["description"], *split_sentences(row["description"])]:
            assert content_key(data["model"], text) in data["vectors"]
