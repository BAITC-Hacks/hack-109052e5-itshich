"""Durable API attempt accounting, raw-response journal and resumable collection."""
import asyncio
from collections import Counter
import json
import os
import random

from scripts.calibration_protocol import parse_ranking


def append_record(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
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
        if row.get("event") == "response" and row["raw"].get("model") == row.get("expected_model"):
            try:
                parsed, ranking = parse_ranking(row["raw"], row["label_to_id"])
                row = row | {"parsed": parsed, "ranking": ranking}
            except (ValueError, TypeError, IndexError, KeyError):
                pass
        # A saved success must survive a stale attempt/error record for the same key.
        if records.get(row["key"], {}).get("ranking") is None:
            records[row["key"]] = row
        offset += len(line)
    if content and offset == len(content) and not content.endswith(b"\n"):
        with path.open("ab") as handle:
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    return records


def pending_jobs(jobs, records):
    return [job for job in jobs if records.get(job["key"], {}).get("ranking") is None]


async def collect(jobs, protocol, path, client):
    from openai import APIStatusError, APIConnectionError

    records = load_judgments(path)
    attempts = Counter(row["key"] for line in path.read_text().splitlines()
                       if (row := json.loads(line)).get("event") == "attempt") if path.exists() else Counter()
    semaphore, jitter = asyncio.Semaphore(min(4, protocol["concurrency"])), random.Random(42)
    max_attempts = min(6, protocol["max_attempts"])

    async def judge(job):
        async with semaphore:
            metadata = {key: value for key, value in job.items() if key not in {"payload", "schema"}}
            metadata["expected_model"] = protocol["parameters"]["model"]
            for attempt in range(attempts[job["key"]], max_attempts):
                if sum(attempts.values()) >= min(149, protocol["max_calls"]):
                    raise RuntimeError("Достигнут общий лимит 149 попыток судьи.")
                attempts[job["key"]] += 1
                append_record(path, {**metadata, "event": "attempt", "raw": None})
                try:
                    response = await client.chat.completions.create(**protocol["parameters"],
                        response_format={"type": "json_schema", "json_schema": {
                            "name": "candidate_ranking", "strict": True, "schema": job["schema"]}},
                        messages=[{"role": "system", "content": protocol["prompt"]},
                                  {"role": "user", "content": json.dumps(job["payload"], ensure_ascii=False)}])
                except (APIStatusError, APIConnectionError) as error:
                    status = getattr(error, "status_code", 0)
                    append_record(path, {**metadata, "event": "error", "raw": None,
                                         "error": type(error).__name__, "status": status})
                    if (status and status != 429 and status < 500) or attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(2 ** attempt + jitter.random())
                    continue
                raw = response.model_dump()
                # Persist the entire response before any parsing or validation can fail.
                append_record(path, {**metadata, "event": "response", "raw": raw})
                try:
                    if response.model != protocol["parameters"]["model"]:
                        raise ValueError("Snapshot ответа отличается от запрошенного.")
                    parsed, ranking = parse_ranking(raw, job["label_to_id"])
                except (ValueError, TypeError, IndexError, KeyError) as error:
                    append_record(path, {**metadata, "event": "invalid", "raw": None,
                                         "error": str(error)})
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(2 ** attempt + jitter.random())
                    continue
                append_record(path, {**metadata, "event": "validated", "parsed": parsed,
                                     "ranking": ranking})
                print(f"Сохранено: {job['request_id']} / {job['assignment']} / {job['direction']}"
                      f"; начато попыток: {sum(attempts.values())}", flush=True)
                return
            raise RuntimeError("Исчерпаны шесть попыток; см. журнал.")

    outcomes = await asyncio.gather(*(judge(job) for job in pending_jobs(jobs, records)), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
