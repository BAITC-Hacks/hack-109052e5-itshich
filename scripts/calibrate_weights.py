"""Run and replay the second, listwise weight calibration experiment."""
import argparse
import asyncio
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from matcher.model import FEATURES
from scripts.calibration_data import prepare, validate_split
from scripts.calibration_fit import bt_loss, fit, score_requests, weight_table
from scripts.calibration_journal import collect, load_judgments, pending_jobs
from scripts.calibration_metrics import aggregate, consistency, evaluate, majority_orders
from scripts.calibration_protocol import build_jobs


def summarize(data, jobs, path, journal):
    records = load_judgments(journal)
    remaining = pending_jobs(jobs, records)
    if remaining:
        raise ValueError(f"Осталось заданий: {len(remaining)}; повторите запуск.")
    rows = [records[job["key"]] for job in jobs]
    pairs, labels = aggregate(rows)
    priors = {g: tuple(values[f] for f in FEATURES) for g, values in data["priors_by_group"].items()}
    (theta, loss), search = fit(data["requests"], pairs, priors)
    consensus, stability = majority_orders(rows), consistency(rows)
    states = {"priors": score_requests(data["requests"], priors=priors),
              "calibrated": score_requests(data["requests"], theta, priors=priors),
              "old_weights_new_anchors": score_requests(data["requests"], old_weights=True)}
    if states["priors"] != {q["id"]: q["prior_scores"] for q in data["requests"]}:
        raise ValueError("Приоры не воспроизводят текущий production score.")
    metrics = {state: {split: evaluate([q for q in data["requests"] if q["split"] == split], pairs, scores, consensus)
                      for split in ("train", "held_out")} for state, scores in states.items()}
    gain = metrics["calibrated"]["held_out"]["pairwise_agreement"] - metrics["priors"]["held_out"]["pairwise_agreement"]
    gates = {"held_out_gain_at_least_5pp": gain >= 0.05 - 1e-12,
             "swap_consistency_at_least_80pct": stability["swap"]["macro_agreement"] >= 0.8,
             "label_shuffle_consistency_at_least_80pct": stability["label_shuffle"]["macro_agreement"] >= 0.8}
    recommended = "calibrated" if all(gates.values()) else "priors"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    raw = [r["raw"] for r in events if r.get("event") == "response"]
    usage = {key: sum((r.get("usage") or {}).get(key, 0) for r in raw)
             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    training = [p for p in pairs if p.request_id in {q["id"] for q in data["requests"] if q["split"] == "train"}]
    result = {"version": 2, "chosen_thetas": dict(zip(("budget", "duration", "quality", "text"), (*theta, 1.0))),
              "train_loss": loss, "prior_train_loss": bt_loss(training, states["priors"]),
              "weights_by_group": weight_table(theta, priors), "grid_results": search,
              "recommended_state": recommended, "recommended_weights_by_group": weight_table(
                  theta if recommended == "calibrated" else (1, 1, 1), priors),
              "held_out_pairwise_gain": gain, "acceptance": gates, "metrics": metrics,
              "consistency": stability, "majority_orders": consensus,
              "coverage": {"eligible_pairs": len(labels), "retained_pairs": len(pairs),
                           **dict(Counter(label["kind"] for label in labels))},
              "labels": labels, "retained_pairs": [asdict(pair) for pair in pairs],
              "api": {"jobs": len(jobs), "attempts": sum(r.get("event") == "attempt" for r in events),
                      "raw_responses": len(raw), "errors": sum(r.get("event") == "error" for r in events),
                      "invalid_responses": sum(r.get("event") == "invalid" for r in events),
                      "snapshots": dict(Counter(r.get("model") for r in raw)), "usage": usage,
                      "confidence": dict(Counter(str(item["confidence"]) for r in rows for item in r["parsed"]["ranking"]))},
              "requests_sha256": sha256(path.read_bytes()).hexdigest(),
              "judgments_sha256": sha256(journal.read_bytes()).hexdigest()}
    path.with_name("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Готово: theta={theta}, train loss={loss:.6f}, принято {len(pairs)}/{len(labels)} пар;"
          f" рекомендация: {recommended}; held-out изменение: {gain * 100:+.2f} п.п.")
    return result


def main():
    parser = argparse.ArgumentParser(description="Раунд 2: слепая listwise-калибровка весов.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="Пересчитать признаки без судейства")
    mode.add_argument("--offline", action="store_true", help="Воспроизвести результат без сети")
    parser.add_argument("--limit", type=int, default=96, help="Максимум новых заданий за запуск")
    args = parser.parse_args()
    directory = ROOT / "data/calibration"
    path, journal = (directory / "v2" / name for name in ("requests.json", "judgments.jsonl"))
    if not path.exists():
        if args.offline:
            parser.error("Нет признаков v2; сначала запустите --prepare.")
        data = prepare(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    data = json.loads(path.read_text())
    validate_split(data)
    jobs = build_jobs(data)
    if args.prepare:
        print(f"Подготовлено: {len(data['requests'])} запросов, {len(jobs)} заданий судье; якоря: {data['scorer']['anchors']}.")
        return
    pending = pending_jobs(jobs, load_judgments(journal))
    if not args.offline and pending:
        from matcher import config  # Load the key without logging it.
        from openai import AsyncOpenAI
        async def run():
            async with AsyncOpenAI(timeout=90, max_retries=0) as client:
                await collect(pending[:max(0, args.limit)], data["protocol"], journal, client)
        asyncio.run(run())
    summarize(data, jobs, path, journal)


if __name__ == "__main__":
    main()
