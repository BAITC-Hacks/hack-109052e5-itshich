"""Offline API-boundary tests for validation, retries, budgets and crash recovery."""
import asyncio
import json
from types import SimpleNamespace

import httpx
from openai import InternalServerError, RateLimitError
import pytest

from scripts import calibration_journal as journal
from scripts.calibration_protocol import build_jobs, parse_ranking, protocol


def jobs():
    return build_jobs({"protocol": protocol(), "requests": [{"id": "q", "request": {},
                       "judge_candidates": {cid: {"description": "anonymous"} for cid in "abcd"}}]})


def response(labels=("K1", "K2", "K3", "K4"), finish="stop", confidence=4):
    raw = {"id": "response-1", "model": protocol()["parameters"]["model"], "choices": [
        {"finish_reason": finish, "message": {"content": json.dumps({"ranking": [
            {"label": label, "confidence": confidence} for label in labels]}), "refusal": None}}]}
    return SimpleNamespace(model=raw["model"], model_dump=lambda: raw)


@pytest.mark.parametrize(("labels", "finish", "confidence"), [
    (("K1", "K1", "K3", "K4"), "stop", 4), (("K1",), "stop", 4),
    (("K1", "K2", "K3", "unknown"), "stop", 4),
    (("K1", "K2", "K3", "K4"), "length", 4),
    (("K1", "K2", "K3", "K4"), "stop", True),
    (("K1", "K2", "K3", "K4"), "stop", 6),
])
def test_invalid_rankings_are_rejected(labels, finish, confidence):
    with pytest.raises(ValueError):
        parse_ranking(response(labels, finish, confidence), jobs()[0]["label_to_id"])


def test_resume_recovers_raw_response_before_validation_and_truncated_tail(tmp_path):
    path, job = tmp_path / "judgments.jsonl", jobs()[0]
    metadata = {k: v for k, v in job.items() if k not in {"payload", "schema"}}
    journal.append_record(path, {**metadata, "event": "response", "raw": response().model_dump(),
                                 "expected_model": protocol()["parameters"]["model"]})
    journal.append_record(path, {"key": "retry", "raw": None, "event": "attempt"})
    with path.open("a") as handle:
        handle.write('{"key":"interrupted"')
    records = journal.load_judgments(path)
    assert records[job["key"]]["ranking"] == list(job["label_to_id"].values())
    assert journal.pending_jobs([job, {"key": "retry"}], records) == [{"key": "retry"}]
    assert path.read_bytes().endswith(b"\n")
    path.write_text('{broken}\n')
    with pytest.raises(ValueError):
        journal.load_judgments(path)


def test_resume_adds_newline_to_complete_tail_and_keeps_success(tmp_path):
    path = tmp_path / "judgments.jsonl"
    path.write_text(json.dumps({"key": "k", "ranking": ["a", "b"]}))
    assert journal.load_judgments(path)["k"]["ranking"] == ["a", "b"]
    journal.append_record(path, {"key": "k", "event": "attempt", "raw": None})
    assert journal.load_judgments(path)["k"]["ranking"] == ["a", "b"]


def test_retries_use_required_parameters_limit_concurrency_and_resume(tmp_path, monkeypatch):
    calls, delays, active, peak = [], [], 0, 0
    real_sleep = asyncio.sleep

    async def sleep(delay):
        delays.append(delay)
        await real_sleep(0)

    async def create(**kwargs):
        nonlocal active, peak
        calls.append(kwargs)
        if len(calls) == 1:
            raise RateLimitError("rate limited", response=httpx.Response(429,
                                 request=httpx.Request("POST", "https://example.test")), body=None)
        active += 1
        peak = max(peak, active)
        await real_sleep(0)
        active -= 1
        return response()

    monkeypatch.setattr(journal.asyncio, "sleep", sleep)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    path = tmp_path / "judgments.jsonl"
    asyncio.run(journal.collect(jobs(), protocol(), path, client))
    assert len(calls) == 7 and peak <= 4
    assert len(delays) == 1 and 1 <= delays[0] < 2
    assert all(call["response_format"]["json_schema"]["strict"] for call in calls)
    assert all(call["reasoning_effort"] == "medium" and call["seed"] == 42 for call in calls)
    assert all(call["max_completion_tokens"] == 1500 for call in calls)
    before = path.read_bytes()
    asyncio.run(journal.collect(jobs(), protocol(), path, client))
    assert len(calls) == 7 and path.read_bytes() == before
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(row["event"] == "response" for row in events) == 6


def test_invalid_raw_response_is_retained_and_retried(tmp_path, monkeypatch):
    responses = [response(finish="length"), response()]
    path, job = tmp_path / "judgments.jsonl", jobs()[0]

    async def create(**kwargs):
        return responses.pop(0)

    async def sleep(delay):
        pass

    monkeypatch.setattr(journal.asyncio, "sleep", sleep)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    asyncio.run(journal.collect([job], protocol(), path, client))
    assert not journal.pending_jobs([job], journal.load_judgments(path))
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(row["event"] == "response" for row in events) == 2
    assert sum(row["event"] == "invalid" for row in events) == 1


def test_retry_budget_survives_restart_and_never_exceeds_six(tmp_path):
    path, job = tmp_path / "judgments.jsonl", jobs()[0]
    for _ in range(5):
        journal.append_record(path, {"key": job["key"], "event": "attempt", "raw": None})
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        raise InternalServerError("temporary error", response=httpx.Response(503,
                                  request=httpx.Request("POST", "https://example.test")), body=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(InternalServerError):
        asyncio.run(journal.collect([job], protocol(), path, client))
    with pytest.raises(RuntimeError, match="шесть"):
        asyncio.run(journal.collect([job], protocol(), path, client))
    assert len(calls) == 1


def test_global_attempt_cap_survives_restart(tmp_path):
    path = tmp_path / "judgments.jsonl"
    for i in range(149):
        journal.append_record(path, {"key": str(i), "event": "attempt", "raw": None})
    with pytest.raises(RuntimeError, match="149"):
        asyncio.run(journal.collect(jobs(), protocol(), path, client=None))
