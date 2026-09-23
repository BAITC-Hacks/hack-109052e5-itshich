"""Reproducible, resumable offline weight calibration with a blind LLM judge."""
import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from itertools import combinations, product
import json
import math
import os
from pathlib import Path
import random
from statistics import fmean

from matcher.model import FEATURES
ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Pair:
    request_id: str
    a: str
    b: str
    y: float

def bt_loss(pairs, scores, thetas=(1.0, 1.0, 1.0)):
    """Macro-average soft-label Bradley–Terry loss, T=0.20, log-prior penalty."""
    losses = {}
    for pair in pairs:
        z = (scores[pair.request_id][pair.a] - scores[pair.request_id][pair.b]) / 0.20
        losses.setdefault(pair.request_id, []).append(
            max(z, 0) + math.log1p(math.exp(-abs(z))) - pair.y * z)
    if not losses:
        raise ValueError("Нет согласованных пар для обучения.")
    return fmean(map(fmean, losses.values())) + 0.05 * sum(math.log(t) ** 2 for t in thetas)

def grid():
    return tuple(product((0.75, 1.0, 1.25), repeat=3))

def weights(group, thetas, duration=True):
    prior = {"general": (0.25, 0.55, 0, 0.05, 0.15), "venue": (0.25, 0.60, 0, 0.05, 0.10),
             "host": (0.25, 0.55, 0, 0.10, 0.10), "decor": (0.25, 0.60, 0, 0, 0.15)}[group]
    multipliers = (thetas[0], 1, 1, thetas[1] if duration else 0, thetas[2])
    values = tuple(p * t for p, t in zip(prior, multipliers))
    return tuple(value / sum(values) for value in values)

def score_requests(requests, thetas=(1, 1, 1), current=False):
    return {q["id"]: {cid: round(sum(v * w for v, w in zip(values,
            (0.35, 0.35, 0.10, 0.10, 0.10) if current else weights(
                q["group"], thetas, q["request"]["duration_hours"] is not None))), 4)
            for cid, values in q["features"].items()} for q in requests}

def fit(requests, pairs):
    training = [p for p in pairs if p.request_id in {q["id"] for q in requests if q["split"] == "train"}]
    results = [(theta, bt_loss(training, score_requests(requests, theta), theta)) for theta in grid()]
    return min(results, key=lambda item: (item[1], sum(math.log(t) ** 2 for t in item[0]), item[0]))

def append_record(path, row):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

def load_judgments(path):
    records, offset = {}, 0
    content = path.read_bytes() if path.exists() else b""
    for line in content.splitlines(keepends=True):
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            if line.endswith(b"\n"):
                raise ValueError("Повреждена завершённая строка журнала.") from None
            with path.open("r+b") as handle:
                handle.truncate(offset)
            break
        records[row["key"]] = row
        offset += len(line)
    if content and offset == len(content) and not content.endswith(b"\n"):
        with path.open("ab") as handle:
            handle.write(b"\n")
    return records

def pending_jobs(jobs, records):
    return [job for job in jobs if records.get(job["key"], {}).get("raw") is None]

def aggregate(records, kind="pair"):
    grouped, pairs = {}, []
    for row in records:
        if row["kind"] != kind:
            continue
        a, b = sorted((row["a"], row["b"]))
        winner = (row.get("parsed") or {}).get("winner")
        label = 0.5 if winner == "tie" else float(row[winner.lower()] == a) if winner in ("A", "B") else None
        grouped.setdefault((row["request_id"], a, b), {})[row["a"]] = label
    for key, orders in sorted(grouped.items()):
        if len(orders) == 2 and len(set(orders.values())) == 1 and None not in orders.values():
            pairs.append(Pair(*key, next(iter(orders.values()))))
    return pairs

def evaluate(requests, pairs, scores, grades):
    rows = []
    for q in requests:
        order = sorted(scores[q["id"]], key=lambda cid: (-scores[q["id"]][cid], cid))
        labels = [p for p in pairs if p.request_id == q["id"]]
        agreement = lambda p: p.y if order.index(p.a) < order.index(p.b) else 1 - p.y
        boundary = [p for p in labels if (p.a in order[:3]) != (p.b in order[:3])]
        relevance = [grades.get((q["id"], cid)) for cid in order]
        dcg = lambda values: sum((2 ** value - 1) / math.log2(i + 2) for i, value in enumerate(values[:3]))
        ideal = dcg(sorted(relevance, reverse=True)) if None not in relevance else 0
        rows.append({"id": q["id"], "top3": order[:3], "pairs": len(labels), "boundary_pairs": len(boundary),
                     "coverage": len(labels) / math.comb(len(order), 2),
                     "pairwise_agreement": fmean(map(agreement, labels)) if labels else None,
                     "boundary_agreement": fmean(map(agreement, boundary)) if boundary else None,
                     "ndcg_at_3": dcg(relevance) / ideal if ideal else None})
    return {"per_request": rows, **{key: fmean(values) if values else None
            for key in ("pairwise_agreement", "boundary_agreement", "ndcg_at_3")
            for values in [[row[key] for row in rows if row[key] is not None]]}}

def build_jobs(data):
    base = [(q, a, b) for q in data["requests"] for a, b in combinations(sorted(q["candidates"]), 2)]
    repeated = random.Random(data["protocol"]["repeat_seed"]).sample(base, min(len(base), data["protocol"]["repeat_pairs"]))
    specs = [(kind, q, a, b) for kind, triples in (("pair", base), ("repeat", repeated))
             for q, left, right in triples for a, b in ((left, right), (right, left))]
    specs = specs[:2 * len(base)] + [("grade", q, a, None) for q in data["requests"]
                                   for a in sorted(q["candidates"])] + specs[2 * len(base):]
    jobs = []
    for kind, q, a, b in specs:
        payload = {"request": q["request"], "A": q["candidates"][a]}
        if b is not None:
            payload["B"] = q["candidates"][b]
        job = {"kind": kind, "request_id": q["id"], "a": a, "b": b, "payload": payload}
        key = sha256(json.dumps((job, data["protocol"]), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        jobs.append({**job, "key": key})
    return jobs

async def collect(jobs, protocol, path, client):
    from openai import APIStatusError, APIConnectionError
    records = load_judgments(path)
    attempts = Counter(json.loads(line)["key"] for line in path.read_text().splitlines()
                       if json.loads(line).get("event") == "attempt") if path.exists() else Counter()
    semaphore, jitter = asyncio.Semaphore(min(4, protocol["concurrency"])), random.Random()
    async def judge(job):
        async with semaphore:
            metadata = {key: value for key, value in job.items() if key != "payload"}
            for attempt in range(attempts[job["key"]], min(6, protocol["max_attempts"])):
                if sum(attempts.values()) >= min(699, protocol["max_calls"]):
                    raise RuntimeError("Достигнут лимит вызовов судьи.")
                attempts[job["key"]] += 1
                append_record(path, {**metadata, "event": "attempt", "raw": None})
                kind = "grade" if job["kind"] == "grade" else "pair"
                try:
                    response = await client.chat.completions.create(**protocol["parameters"],
                        response_format={"type": "json_schema", "json_schema": {"name": kind, "strict": True,
                                         "schema": protocol[kind + "_schema"]}},
                        messages=[{"role": "system", "content": protocol[kind + "_prompt"]},
                                  {"role": "user", "content": json.dumps(job["payload"], ensure_ascii=False)}])
                except (APIStatusError, APIConnectionError) as error:
                    status = getattr(error, "status_code", 0)
                    append_record(path, {**metadata, "raw": None, "error": type(error).__name__, "status": status})
                    if (status and status != 429 and status < 500) or attempt == 5:
                        raise
                    await asyncio.sleep(2 ** attempt + jitter.random())
                    continue
                try:
                    parsed = json.loads(response.choices[0].message.content) if response.choices[0].finish_reason == "stop" else None
                except (ValueError, TypeError):
                    parsed = None
                append_record(path, {**metadata, "raw": response.model_dump(), "parsed": parsed})
                print(f"Сохранено: {job['request_id']} / {job['kind']} / {sum(attempts.values())} вызовов", flush=True)
                return
            raise RuntimeError("Исчерпаны шесть попыток; см. журнал.")
    await asyncio.gather(*(judge(job) for job in pending_jobs(jobs, records)))

def prepare(data):
    from matcher.embeddings import EmbeddingScorer, request_text
    from matcher.filtering import filter_pool
    from matcher.model import MatchRequest
    from matcher.pipeline import choose_scorer, get_contractors
    from matcher.ranking import score_all
    scorer, contractors = choose_scorer(), get_contractors()
    if not isinstance(scorer, EmbeddingScorer):
        raise ValueError("Калибровка требует производственный EmbeddingScorer.")
    from matcher.config import create_client
    scorer.client, scorer.cache_path = create_client(), ROOT / ".work/calibration-embeddings.json"
    if scorer.client is not None:
        scorer.client = scorer.client.with_options(timeout=60, max_retries=5)
    requests = [MatchRequest(**(q["request"] | {"event_date": date.fromisoformat(q["request"]["event_date"])}))
                for q in data["requests"]]
    scorer.cache_texts([request_text(request) for request in requests])
    for q, request in zip(data["requests"], requests):
        eligible = filter_pool(contractors, request)[1]
        if not 4 <= len(eligible) <= 6:
            raise ValueError(f"Запрос {q['id']}: требуется 4–6 прошедших фильтр кандидатов.")
        cards = score_all(eligible, request, scorer)
        q["features"] = {c.contractor.id: [getattr(c.score, f) for f in FEATURES] for c in cards}
        q["main_scores"] = {c.contractor.id: c.score.total for c in cards}
        q["candidates"] = {c.id: {**{f: getattr(c, f) for f in ("price_from_kzt", "languages", "max_hours", "description")},
            "budget_kzt": request.budget_kzt, "formats": c.event_formats,
            "flags": {f: getattr(c, f) for f in ("price_imputed", "city_imputed", "synthetic")}} for c in eligible}
    data["source_hashes"] = {p: sha256((ROOT / p).read_bytes()).hexdigest() for p in (
        "data/contractors.csv", "data/synthetic_extra.csv", "data/embeddings.json", "matcher/ranking.py", "matcher/embeddings.py")}
    data["scorer"] = {"type": type(scorer).__name__, "model": scorer.model, "dimensions": scorer.dimensions}

def main():
    parser = argparse.ArgumentParser(description="Калибровка весов со слепым LLM-судьёй.")
    parser.add_argument("--prepare", action="store_true", help="Подготовить признаки без вызовов судьи")
    parser.add_argument("--offline", action="store_true", help="Пересчитать результат по сохранённым ответам")
    parser.add_argument("--limit", type=int, default=699, help="Максимум заданий за этот запуск")
    args = parser.parse_args()
    path, journal = (ROOT / "data/calibration" / name for name in ("requests.json", "judgments.jsonl"))
    data = json.loads(path.read_text())
    groups = {split: {q["family"] for q in data["requests"] if q["split"] == split} for split in ("train", "held_out")}
    if groups["train"] & groups["held_out"] or Counter(q["split"] for q in data["requests"]) != {"train": 10, "held_out": 6}:
        raise ValueError("Нужны 10 train / 6 held-out без пересечения семейств.")
    if not all("features" in q for q in data["requests"]):
        if args.offline:
            raise ValueError("Нет замороженных признаков; сначала запустите --prepare.")
        prepare(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    jobs = build_jobs(data)
    if args.prepare:
        print(f"Подготовлено: {len(data['requests'])} запросов, {len(jobs)} заданий судье.")
        return
    if not args.offline:
        from matcher import config  # Load the local API key without logging it.
        from openai import AsyncOpenAI
        asyncio.run(collect(pending_jobs(jobs, load_judgments(journal))[:args.limit], data["protocol"], journal,
                            AsyncOpenAI(timeout=60, max_retries=0)))
    records = load_judgments(journal)
    if pending_jobs(jobs, records):
        raise ValueError(f"Осталось заданий: {len(pending_jobs(jobs, records))}; повторите запуск.")
    pairs = aggregate(rows := [records[job["key"]] for job in jobs])
    theta, loss = fit(data["requests"], pairs)
    grades = {(r["request_id"], r["a"]): r["parsed"]["grade"] for r in rows if r["kind"] == "grade" and r["parsed"]}
    states = {"current_main": {q["id"]: q["main_scores"] for q in data["requests"]},
              "production_semantic_old_weights": score_requests(data["requests"], current=True),
              "priors": score_requests(data["requests"]), "calibrated": score_requests(data["requests"], theta)}
    metrics = {state: {split: evaluate([q for q in data["requests"] if q["split"] == split], pairs, scores, grades)
                      for split in ("train", "held_out")} for state, scores in states.items()}
    metrics["coverage"] = {"eligible_pairs": sum(math.comb(len(q["features"]), 2) for q in data["requests"]),
                           "retained_pairs": len(pairs), "ties": sum(p.y == 0.5 for p in pairs)}
    baseline = {(r["request_id"], r["a"], r["b"]): r["parsed"] for r in rows if r["kind"] == "pair"}
    repeats = [r for r in rows if r["kind"] == "repeat"]
    metrics["stability"] = {"repeated_pairs": len(repeats) // 2, "changed_winner_calls": sum(
        (r["parsed"] or {}).get("winner") != (baseline[(r["request_id"], r["a"], r["b"])] or {}).get("winner") for r in repeats)}
    result = {"chosen_thetas": dict(zip(("budget", "duration", "quality", "text"), (*theta, 1.0))), "train_loss": loss,
              "weights_by_group": {g: dict(zip(FEATURES, weights(g, theta))) for g in ("general", "venue", "host", "decor")},
              "metrics": metrics, "requests_sha256": sha256(path.read_bytes()).hexdigest(),
              "judgments_sha256": sha256(journal.read_bytes()).hexdigest()}
    path.with_name("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Готово: theta={theta}, train loss={loss:.6f}, согласовано {len(pairs)} пар.")

if __name__ == "__main__":
    main()
