"""Offline checks for listwise aggregation and train-only calibration."""
from itertools import product
import json
import math
from types import SimpleNamespace

import pytest

from matcher.model import FEATURES
from matcher.ranking import weights_for
from scripts.calibration_data import anonymize, archive_v1, request_object
from scripts.calibration_fit import bt_loss, fit, grid, weights
from scripts.calibration_metrics import Pair, aggregate, consistency, evaluate, majority_orders
from scripts.calibration_protocol import build_jobs, protocol


def votes(orders):
    return [{"request_id": "q", "assignment": i, "direction": direction, "ranking": order}
            for (i, direction), order in zip(product(range(3), ("forward", "reverse")), orders, strict=True)]


@pytest.mark.parametrize(("count", "label", "weight"), [
    (0, 0, 1), (1, 0, 1), (2, 0.17, 0.5), (3, None, 0),
    (4, 0.83, 0.5), (5, 1, 1), (6, 1, 1),
])
def test_majority_hard_soft_and_excluded_labels(count, label, weight):
    rows = votes([list("ab")] * count + [list("ba")] * (6 - count))
    pairs, details = aggregate(rows)
    assert details[0]["vote_fraction"] == count / 6
    assert details[0]["y"] == label
    assert details[0]["weight"] == weight
    assert pairs == ([] if label is None else [Pair("q", "a", "b", label, weight, count)])


def test_listwise_votes_expand_all_pairs_and_require_six_complete_rankings():
    rows = votes([list("cabd")] * 6)
    pairs, _ = aggregate(rows)
    assert len(pairs) == 6
    assert next(p.y for p in pairs if (p.a, p.b) == ("a", "c")) == 0
    with pytest.raises(ValueError):
        aggregate(rows[:5])
    with pytest.raises(ValueError):
        aggregate(rows + rows[:1])
    rows[-1]["ranking"] = list("abce")
    with pytest.raises(ValueError):
        aggregate(rows)


def test_swap_and_label_shuffle_consistency_are_separate():
    rows = votes([list("abc"), list("bac")] * 3)
    result = consistency(rows)
    assert result["swap"]["macro_agreement"] == pytest.approx(2 / 3)
    assert result["swap"]["matches"] == 6
    assert result["swap"]["comparisons"] == 9
    assert result["label_shuffle"]["macro_agreement"] == 1
    rows = votes([list("abc")] * 2 + [list("bac")] * 2 + [list("abc")] * 2)
    result = consistency(rows)
    assert result["swap"]["macro_agreement"] == 1
    assert result["label_shuffle"]["macro_agreement"] == pytest.approx(7 / 9)


def test_consensus_resolves_majority_cycles_deterministically():
    rows = votes([list("abc"), list("bca"), list("cab")] * 2)
    assert majority_orders(rows)["q"] == {
        "order": list("abc"), "vote_agreements": 10, "optimal_orders": 3, "majority_cycles": 1}
    assert majority_orders(list(reversed(rows))) == majority_orders(rows)


def test_bt_weights_soft_labels_request_macro_temperature_and_penalty():
    pairs = [Pair("q1", "a", "b", 1), Pair("q1", "a", "c", 0.17, 0.5), Pair("q2", "a", "b", 0)]
    scores = {"q1": {"a": 0.4, "b": 0.0, "c": 0.4}, "q2": {"a": 0, "b": 0}}
    expected = ((math.log1p(math.exp(-2)) + 0.5 * math.log(2)) / 1.5 + math.log(2)) / 2
    assert bt_loss(pairs, scores) == pytest.approx(expected)
    assert bt_loss(pairs, scores, (1.5, 1, 1)) == pytest.approx(expected + 0.05 * math.log(1.5) ** 2)
    assert math.isfinite(bt_loss([Pair("q", "a", "b", 0)], {"q": {"a": 1000, "b": -1000}}))
    with pytest.raises(ValueError):
        bt_loss([], scores)


def test_grid_has_125_configs_and_fit_ignores_held_out_labels():
    assert len(grid()) == len(set(grid())) == 125
    assert {theta[0] for theta in grid()} == {0.5, 0.75, 1, 1.25, 1.5}
    queries = [{"id": split, "split": split, "group": "general", "request": {"duration_hours": None},
                "features": {"a": [1, 0, 0, 0.5, 1], "b": [0, 0, 0, 0.5, 1]}}
               for split in ("train", "held_out")]
    pairs = [Pair("train", "a", "b", 1)]
    fitted = fit(queries, pairs)
    assert fitted == fit(queries, pairs + [Pair("held_out", "a", "b", 0)])
    assert fitted[0][0] != (1, 1, 1)


def test_priors_match_production_with_and_without_requested_hours():
    for group, category in (("general", "Фотограф"), ("venue", "Банкетный зал"),
                            ("host", "Ведущий"), ("no_presence", "Флорист")):
        for hours in (None, 4):
            request = request_object({"city": "Алматы", "category": category, "event_format": "свадьба",
                                      "event_date": "2026-10-01", "budget_kzt": 1000, "duration_hours": hours})
            assert weights(group, (1, 1, 1), hours is not None) == tuple(weights_for(request)[f] for f in FEATURES)
            assert sum(weights(group, (1.5, 0.5, 0.75), hours is not None)) == pytest.approx(1)
            assert weights(group, (1.5, 0.5, 0.75), hours is not None)[2] == 0


def test_metrics_compare_majority_direction_boundary_and_kendall():
    queries = [{"id": "q"}]
    pairs = [Pair("q", "a", "b", 0.83, 0.5), Pair("q", "c", "d", 0)]
    scores = {"q": {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.6}}
    result = evaluate(queries, pairs, scores, {"q": {"order": list("dcba")}})
    assert result["pairwise_agreement"] == 0.5
    assert result["weighted_pairwise_agreement"] == pytest.approx(1 / 3)
    assert result["boundary_agreement"] == 0
    assert result["kendall_tau"] == -1
    assert result["per_request"][0]["coverage"] == pytest.approx(1 / 3)


def test_jobs_are_blind_and_label_assignments_are_independent_of_presentation():
    data = {"protocol": protocol(), "requests": [{"id": "q", "request": {"budget_kzt": 100},
        "features": {"private-a": [0.9]}, "judge_candidates": {
            f"private-{cid}": {"description": "anonymous", "price_from_kzt": i}
            for i, cid in enumerate("abcd")}}]}
    jobs = build_jobs(data)
    assert len(jobs) == len({job["key"] for job in jobs}) == 6
    assert len({tuple(job["label_to_id"].items()) for job in jobs}) == 3
    for forward, reverse in zip(jobs[::2], jobs[1::2]):
        assert forward["label_to_id"] == reverse["label_to_id"]
        assert forward["payload"]["candidates"] == list(reversed(reverse["payload"]["candidates"]))
    assert len({tuple(c["price_from_kzt"] for c in j["payload"]["candidates"]) for j in jobs[::2]}) == 1
    assert all("private-" not in json.dumps(job["payload"]) for job in jobs)
    data["requests"][0]["features"] = {"different": [-99]}
    assert build_jobs(data) == jobs
    data["requests"][0]["judge_candidates"]["private-a"]["price_from_kzt"] = 100
    assert {job["key"] for job in build_jobs(data)}.isdisjoint(job["key"] for job in jobs)


def test_anonymization_removes_full_and_short_names_and_venue_aliases():
    contractor = SimpleNamespace(name="Сон Гоку", id="private-1", description="Сон Гоку. С Гоку 12 лет опыта.")
    facts = {"description": contractor.description, "price_from_kzt": 100}
    masked = anonymize(contractor, facts, "host")
    assert "Гоку" not in masked["description"] and "12 лет опыта" in masked["description"]
    contractor.description = "Grand Hotel – ресторан. Grand Hotel работает 12 лет."
    assert "Grand Hotel" not in anonymize(contractor, {"description": contractor.description}, "venue")["description"]


def test_archive_is_byte_identical_and_never_overwrites_v1(tmp_path):
    path = tmp_path / "requests.json"
    path.write_bytes(b'{"version":1}\n')
    first = archive_v1(tmp_path)
    assert path.read_bytes() == (tmp_path / "v1/requests.json").read_bytes()
    assert archive_v1(tmp_path) == first
    path.write_text("changed")
    with pytest.raises(ValueError):
        archive_v1(tmp_path)
    assert (tmp_path / "v1/requests.json").read_bytes() == b'{"version":1}\n'
