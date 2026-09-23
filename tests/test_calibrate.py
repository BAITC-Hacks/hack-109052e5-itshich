"""Offline behavioral checks for the calibration experiment."""
import math
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import calibrate_weights as calibration


def test_bt_loss_uses_temperature_request_macro_average_and_prior_penalty():
    pairs = [calibration.Pair("q1", "a", "b", 1.0),
             calibration.Pair("q2", "a", "b", 0.5),
             calibration.Pair("q2", "a", "c", 0.5)]
    scores = {"q1": {"a": 0.4, "b": 0.0},
              "q2": {"a": 0.0, "b": 0.0, "c": 0.0}}
    assert calibration.bt_loss(pairs, scores) == pytest.approx(0.4100375958014589)
    assert calibration.bt_loss(pairs, scores, (1.25, 1, 1)) == pytest.approx(
        0.4100375958014589 + 0.05 * math.log(1.25) ** 2)
    assert math.isfinite(calibration.bt_loss(
        [calibration.Pair("q", "a", "b", 0)], {"q": {"a": 1000, "b": -1000}}))
    with pytest.raises(ValueError):
        calibration.bt_loss([], scores)


def test_grid_preserves_profiles_masks_duration_and_fits_only_train():
    assert len(calibration.grid()) == 27
    assert len(set(calibration.grid())) == 27
    assert calibration.weights("general", (1, 1, 1)) == pytest.approx(
        (0.25, 0.55, 0, 0.05, 0.15))
    assert calibration.weights("host", (1, 1, 1), False) == pytest.approx(
        (0.25 / 0.9, 0.55 / 0.9, 0, 0, 0.10 / 0.9))
    requests = [{"id": "train", "split": "train", "group": "general",
                 "request": {"duration_hours": None},
                 "features": {"a": [1, 0, 0, 0.5, 1], "b": [0, 0, 0, 0.5, 1]}},
                {"id": "test", "split": "held_out", "group": "general",
                 "request": {"duration_hours": None},
                 "features": {"a": [1, 0, 0, 0.5, 1], "b": [0, 0, 0, 0.5, 1]}}]
    pairs = [calibration.Pair("train", "a", "b", 1)]
    best, loss = calibration.fit(requests, pairs)
    assert best == (1.25, 1.0, 0.75)
    assert math.isfinite(loss)
    assert calibration.fit(requests, pairs + [calibration.Pair("test", "a", "b", 0)]) == (best, loss)


def test_resume_keeps_finished_judgments_and_recovers_only_truncated_tail(tmp_path):
    path = tmp_path / "judgments.jsonl"
    finished = {"key": "done", "raw": {"id": "response-1"}, "parsed": {"winner": "A"}}
    calibration.append_record(path, finished)
    calibration.append_record(path, {"key": "retry", "raw": None, "error": 429})
    with path.open("a") as handle:
        handle.write('{"key":"interrupted"')
    records = calibration.load_judgments(path)
    assert records["done"] == finished
    assert calibration.pending_jobs([{"key": "done"}, {"key": "retry"}, {"key": "new"}], records) == [
        {"key": "retry"}, {"key": "new"}]
    calibration.append_record(path, {"key": "new", "raw": {"id": "response-2"}})
    assert set(calibration.load_judgments(path)) == {"done", "retry", "new"}
    path.write_text('{broken}\n')
    with pytest.raises(ValueError):
        calibration.load_judgments(path)


@pytest.mark.parametrize(("forward", "reverse", "expected"), [
    ("A", "B", 1), ("B", "A", 0), ("tie", "tie", 0.5),
    ("A", "A", None), ("B", "B", None), ("A", "tie", None),
    ("insufficient", "insufficient", None), ("A", "insufficient", None), ("A", None, None),
])
def test_swap_aggregation_tracks_real_candidate_not_position(forward, reverse, expected):
    records = [{"kind": "pair", "request_id": "q", "a": "a", "b": "b",
                "parsed": {"winner": forward}},
               {"kind": "pair", "request_id": "q", "a": "b", "b": "a",
                "parsed": {"winner": reverse} if reverse else None}]
    assert calibration.aggregate(records) == (
        [] if expected is None else [calibration.Pair("q", "a", "b", expected)])
    assert calibration.aggregate(records[:1]) == []
    assert calibration.aggregate([dict(row, kind="repeat") for row in records]) == []


def test_metrics_measure_boundary_and_use_independent_grades():
    requests = [{"id": "q", "features": dict.fromkeys(("a", "b", "c", "d"))}]
    pairs = [calibration.Pair("q", "a", "b", 1), calibration.Pair("q", "c", "d", 0)]
    scores = {"q": {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.6}}
    grades = {("q", "a"): 3, ("q", "b"): 2, ("q", "c"): 0, ("q", "d"): 1}
    metrics = calibration.evaluate(requests, pairs, scores, grades)
    assert metrics["pairwise_agreement"] == 0.5
    assert metrics["boundary_agreement"] == 0
    assert metrics["ndcg_at_3"] == pytest.approx(0.9467676761)
    assert metrics["per_request"][0]["coverage"] == pytest.approx(1 / 3)
    assert calibration.evaluate(requests, pairs, scores, {})["ndcg_at_3"] is None


def test_jobs_are_blind_complete_and_fingerprinted_by_facts():
    data = {"protocol": {"repeat_pairs": 20, "repeat_seed": 42}, "requests": [
        {"id": "q", "request": {"budget_kzt": 100}, "features": {"a": [0.9]},
         "candidates": {cid: {"price_from_kzt": i} for i, cid in enumerate("abcd")}}]}
    jobs = calibration.build_jobs(data)
    assert len(jobs) == 28  # Six pairs in two orders, four grades, twelve repeat calls.
    assert len({job["key"] for job in jobs}) == 28
    assert all(set(job["payload"]) <= {"request", "A", "B"} for job in jobs)
    data["requests"][0]["features"]["a"] = [-99]
    assert calibration.build_jobs(data) == jobs
    data["requests"][0]["candidates"]["a"]["price_from_kzt"] = 50
    assert {job["key"] for job in calibration.build_jobs(data)} != {job["key"] for job in jobs}


def test_judging_retries_throttling_limits_concurrency_and_resumes(tmp_path, monkeypatch):
    import httpx
    from openai import RateLimitError

    protocol = json.loads((Path(__file__).resolve().parents[1] / "data/calibration/requests.json").read_text())["protocol"]
    jobs = [{"key": str(i), "kind": "pair", "request_id": "q", "a": "a", "b": "b", "payload": {}}
            for i in range(7)]
    calls, active, peak, delays = [], 0, 0, []
    real_sleep = asyncio.sleep

    async def sleep(delay):
        delays.append(delay)
        await real_sleep(0)

    async def create(**kwargs):
        nonlocal active, peak
        calls.append(kwargs)
        if len(calls) == 1:
            raise RateLimitError("rate limited", response=httpx.Response(429, request=httpx.Request("POST", "https://example.test")), body=None)
        active += 1
        peak = max(peak, active)
        await real_sleep(0)
        active -= 1
        content = json.dumps({"winner": "A", "decisive_factor": "budget", "evidence_a": "Дешевле", "evidence_b": "Дороже"})
        return SimpleNamespace(model_dump=lambda: {"content": content},
                               choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))])

    monkeypatch.setattr(calibration.asyncio, "sleep", sleep)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    path = tmp_path / "judgments.jsonl"
    asyncio.run(calibration.collect(jobs, protocol, path, client))
    assert len(calls) == 8
    assert peak <= 4
    assert len(delays) == 1 and 1 <= delays[0] < 2
    assert all(call["response_format"]["json_schema"]["strict"] for call in calls)
    assert all(call["temperature"] == 0 and call["seed"] == 42 for call in calls)
    assert len(calibration.load_judgments(path)) == 7
    asyncio.run(calibration.collect(jobs, protocol, path, client))
    assert len(calls) == 8


def test_retry_budget_survives_restart_and_never_exceeds_six(tmp_path):
    import httpx
    from openai import InternalServerError

    protocol = json.loads((Path(__file__).resolve().parents[1] / "data/calibration/requests.json").read_text())["protocol"]
    job = {"key": "retry", "kind": "pair", "request_id": "q", "a": "a", "b": "b", "payload": {}}
    path = tmp_path / "judgments.jsonl"
    for _ in range(5):
        calibration.append_record(path, {"key": "retry", "event": "attempt", "raw": None})
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        raise InternalServerError("temporary error", response=httpx.Response(503, request=httpx.Request("POST", "https://example.test")), body=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(InternalServerError):
        asyncio.run(calibration.collect([job], protocol, path, client))
    with pytest.raises(RuntimeError, match="шесть"):
        asyncio.run(calibration.collect([job], protocol, path, client))
    assert len(calls) == 1
