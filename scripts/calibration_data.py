"""Preserve v1 bytes and recompute v2 features with the current production scorer."""
from collections import Counter
from dataclasses import asdict
from datetime import date
from hashlib import sha256
import inspect
import json
from pathlib import Path
import re
import shutil
import sys

from matcher.model import FEATURES, MatchRequest
from matcher.ranking import CATEGORY_GROUPS, weights_for
from scripts.calibration_protocol import protocol

ROOT = Path(__file__).resolve().parents[1]
GROUP_CATEGORIES = {"general": "Фотограф", "venue": "Банкетный зал",
                    "host": "Ведущий", "no_presence": "Флорист"}


def request_object(fields):
    return MatchRequest(**(fields | {"event_date": date.fromisoformat(fields["event_date"])}))


def group_priors():
    return {group: tuple(weights_for(MatchRequest("Алматы", date(2026, 10, 1), "свадьба", category, 1, 4))[f]
                         for f in FEATURES) for group, category in GROUP_CATEGORIES.items()}


def validate_split(data):
    queries = data["requests"]
    groups = {split: {q["family"] for q in queries if q["split"] == split} for split in ("train", "held_out")}
    if (groups["train"] & groups["held_out"] or len({q["id"] for q in queries}) != 16
            or Counter(q["split"] for q in queries) != {"train": 10, "held_out": 6}):
        raise ValueError("Нужны прежние 16 запросов, split 10/6 и непересекающиеся семейства.")


def archive_v1(directory):
    archive = directory / "v1"
    archive.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for source in sorted(directory.iterdir()):
        if not source.is_file():
            continue
        target = archive / source.name
        if not target.exists():
            shutil.copy2(source, target)
        if source.read_bytes() != target.read_bytes():
            raise ValueError(f"Архив v1 отличается от исходника: {source.name}")
        hashes[source.name] = sha256(target.read_bytes()).hexdigest()
    return hashes


def anonymize(contractor, facts, group):
    aliases = [contractor.name, *contractor.name.split(), contractor.id]
    if group == "venue":
        aliases.append(re.split(r"\s[–—]\s", contractor.description, maxsplit=1)[0])
    if contractor.name == "Нами":
        aliases.append("Николаевич")
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(a) for a in sorted(set(aliases), key=len, reverse=True)) + r")(?!\w)"
    return facts | {"description": re.sub(pattern, "[кандидат]", facts["description"], flags=re.IGNORECASE)}


def prepare(directory):
    import openai
    from openai.resources.chat.completions import AsyncCompletions
    from matcher.embeddings import EmbeddingScorer, request_text, split_sentences
    from matcher.filtering import filter_pool
    from matcher.pipeline import choose_scorer, get_contractors
    from matcher.ranking import score_all

    hashes = archive_v1(directory)
    old = json.loads((directory / "v1/requests.json").read_text())
    validate_split(old)
    parameters = inspect.signature(AsyncCompletions.create).parameters
    supported = {key: key in parameters for key in protocol()["parameters"]}
    if not all(supported.values()):
        raise ValueError("SDK не поддерживает обязательные параметры судьи.")
    scorer, contractors = choose_scorer(), get_contractors()
    if not isinstance(scorer, EmbeddingScorer) or scorer._anchors is None:
        raise ValueError("Требуется EmbeddingScorer с готовыми якорями каталога.")
    scorer.cache_path = ROOT / ".work/calibration-v2-embeddings.json"
    requests = [request_object(q["request"]) for q in old["requests"]]
    pools = [filter_pool(contractors, request)[1] for request in requests]
    texts = [request_text(request) for request in requests]
    texts += [text for pool in pools for c in pool for text in (c.description, *split_sentences(c.description))]
    before = len(scorer._vectors)
    if any(not 4 <= len(pool) <= 6 for pool in pools):
        raise ValueError("В каждом запросе нужны все 4–6 eligible-кандидатов.")
    scorer.cache_texts(texts)
    queries = []
    for original, request, eligible in zip(old["requests"], requests, pools, strict=True):
        if {c.id for c in eligible} != set(original["candidates"]):
            raise ValueError(f"Изменился eligible-пул {original['id']}.")
        q = {key: original[key] for key in ("id", "family", "split", "request")}
        q["group"] = CATEGORY_GROUPS.get(request.category, "general")
        cards = score_all(eligible, request, scorer)
        q["features"] = {c.contractor.id: [getattr(c.score, f) for f in FEATURES] for c in cards}
        q["prior_scores"] = {c.contractor.id: c.score.total for c in cards}
        q["prior_weights"] = weights_for(request)
        q["candidates"], q["judge_candidates"] = {}, {}
        for c in eligible:
            facts = {**{f: getattr(c, f) for f in ("price_from_kzt", "languages", "max_hours", "description")},
                     "budget_kzt": request.budget_kzt, "formats": c.event_formats,
                     "flags": {f: getattr(c, f) for f in ("price_imputed", "city_imputed", "synthetic")}}
            q["candidates"][c.id] = facts
            q["judge_candidates"][c.id] = anonymize(c, facts, q["group"])
        queries.append(q)
    return {"protocol": protocol(), "requests": queries, "v1_sha256": hashes,
            "source_hashes": {p: sha256((ROOT / p).read_bytes()).hexdigest() for p in (
                "data/contractors.csv", "data/synthetic_extra.csv", "data/embeddings.json",
                "matcher/ranking.py", "matcher/embeddings.py")},
            "scorer": {"type": type(scorer).__name__, "model": scorer.model, "dimensions": scorer.dimensions,
                       "anchors": asdict(scorer._anchors), "new_vectors": len(scorer._vectors) - before},
            "runtime": {"python": sys.version.split()[0], "openai": openai.__version__,
                        "chat_completions_parameters_supported": supported},
            "priors_by_group": {g: dict(zip(FEATURES, values)) for g, values in group_priors().items()}}
