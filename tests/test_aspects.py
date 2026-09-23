"""Offline aspect cache and the tagging script's evidence boundary."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from matcher.aspects import ASPECTS_PATH, POLARITIES, TAGS, load_aspects
from matcher.data import load_contractors
from matcher.pipeline import DATA_PATH


def script():
    path = Path(__file__).resolve().parents[1] / "scripts/tag_aspects.py"
    assert path.exists(), "The offline tagging script must exist"
    spec = importlib.util.spec_from_file_location("tag_aspects", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        assert kwargs["model"] == "gpt-5.4-mini"
        assert kwargs["temperature"] == 0
        assert kwargs["seed"] == 42
        schema = kwargs["response_format"]
        assert schema["type"] == "json_schema"
        assert schema["json_schema"]["strict"] is True
        assert set(schema["json_schema"]["schema"]["properties"]["aspects"]
                   ["items"]["properties"]["tag"]["enum"]) == set(TAGS)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(
                refusal=None, content=json.dumps({"aspects": self.items})))])


def test_committed_aspects_have_known_tags_and_verbatim_evidence():
    assert ASPECTS_PATH.exists()
    raw = json.loads(ASPECTS_PATH.read_text(encoding="utf-8"))
    assert set(raw) == {"model", "version", "aspects"}
    assert raw["model"] == "gpt-5.4-mini"
    assert raw["version"] == 1
    contractors = {c.id: c for c in load_contractors(DATA_PATH)}
    load_aspects.cache_clear()
    aspects = load_aspects()
    assert set(aspects) == set(raw["aspects"])
    for cid, items in aspects.items():
        assert cid in contractors
        assert 1 <= len(items) <= 4
        assert len(items) == len(raw["aspects"][cid])
        assert [a.tag for a in items] == sorted({a.tag for a in items})
        for aspect in items:
            assert aspect.tag in TAGS
            assert aspect.polarity in POLARITIES
            assert 0 < len(aspect.quote) <= 160
            assert aspect.quote in contractors[cid].description


def test_at_least_fifty_of_all_seventy_eight_contractors_are_tagged():
    assert len(load_contractors(DATA_PATH)) == 78
    assert len(load_aspects()) >= 50


@pytest.mark.parametrize("existing", [False, True])
def test_dry_run_drops_invalid_evidence_and_never_writes(tmp_path, capsys, existing):
    module = script()
    output = tmp_path / "aspects.json"
    original = '{"model":"gpt-5.4-mini","version":1,"aspects":{}}'
    if existing:
        output.write_text(original)
    fake = FakeClient([
        {"tag": "invented_tag", "polarity": "positive", "quote": "опыт"},
        {"tag": "custom_script", "polarity": "positive", "quote": "NOT IN ANY DESCRIPTION"},
    ])
    assert module.main(["--dry-run", "--output", str(output)], client=fake) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["processed"] == 78
    assert stats["contractors_tagged"] == 0
    assert stats["dropped_quotes"] == 78
    assert stats["dropped_tags"] == 78
    assert sorted(p.name for p in tmp_path.iterdir()) == (["aspects.json"] if existing else [])
    if existing:
        assert output.read_text() == original


def test_whitespace_evidence_is_restored_and_tags_are_sorted():
    module = script()
    items = [
        {"tag": "improvisation", "polarity": "positive", "quote": "живое общение"},
        {"tag": "custom_script", "polarity": "neutral", "quote": "свой сценарий"},
        {"tag": "business_forum", "polarity": "positive", "quote": ""},
        {"tag": "team_building", "polarity": "invalid", "quote": "свой"},
        {"tag": "premium_clients", "polarity": "positive", "quote": "x" * 161},
    ]
    kept, dropped_quotes, dropped_tags = module.validate_aspects(
        "свой\tсценарий; живое\n  общение " + "x" * 161, items)
    assert kept == [
        {"tag": "custom_script", "polarity": "neutral", "quote": "свой\tсценарий"},
        {"tag": "improvisation", "polarity": "positive", "quote": "живое\n  общение"},
    ]
    assert dropped_quotes == 2
    assert dropped_tags == 1


def test_existing_contractors_are_skipped_unless_forced(tmp_path, monkeypatch, capsys):
    module = script()
    output = tmp_path / "aspects.json"
    contractors = [SimpleNamespace(id="one", description="свой сценарий")]
    monkeypatch.setattr(module, "load_contractors", lambda path: contractors)
    fake = FakeClient([{"tag": "custom_script", "polarity": "positive", "quote": "свой сценарий"}])
    module.main(["--output", str(output)], client=fake)
    assert json.loads(capsys.readouterr().out)["processed"] == 1
    original = output.read_bytes()
    # No API method exists: even attempting a second request must fail.
    module.main(["--output", str(output)], client=object())
    assert json.loads(capsys.readouterr().out)["processed"] == 0
    assert output.read_bytes() == original
    fake.items = []
    module.main(["--force", "--output", str(output)], client=fake)
    assert json.loads(capsys.readouterr().out)["processed"] == 1
    assert json.loads(output.read_text())["aspects"] == {}
