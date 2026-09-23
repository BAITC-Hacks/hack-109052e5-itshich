#!/usr/bin/env python3
"""Build embeddings and fixed catalogue anchors, or compact an existing cache offline."""
import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from matcher.embeddings import EmbeddingScorer, content_key, split_sentences


class ZeroEmbeddingsClient:
    """Offline OpenAI boundary for exercising the exact cache-building path."""

    def __init__(self):
        self.embeddings = self

    def create(self, *, model, dimensions, input):
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[0.0] * dimensions)
                                     for i, _ in enumerate(input)])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", "--input", type=Path, default=ROOT / "data/contractors.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/embeddings.json")
    parser.add_argument("--anchors", action="store_true", help="Добавить все запросы каталога и фиксированные якоря P05/P95")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Use a fake client that returns zero vectors of the configured dimensions")
    mode.add_argument("--compact", action="store_true", help="Round the existing --output cache to 6 decimals without network")
    args = parser.parse_args(argv)
    if args.compact and args.anchors:
        parser.error("--anchors нельзя объединять с --compact")
    if args.compact:
        before = args.output.stat().st_size
        data = json.loads(args.output.read_text(encoding="utf-8"))
        data["vectors"] = {key: [round(value, 6) for value in vector]
                           for key, vector in data["vectors"].items()}
        content = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        args.output.write_text(content, encoding="utf-8")
        print(f"compacted={args.output} vectors={len(data['vectors'])} "
              f"bytes_before={before} bytes_after={args.output.stat().st_size}")
        return 0
    # This reader deliberately does not depend on matcher.data or its loader.
    extra = args.csv.with_name("synthetic_extra.csv")
    sources = [args.csv] + ([extra] if extra != args.csv and extra.exists() else [])
    rows = []
    for source in sources:
        with source.open(encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    descriptions = [row["description"] for row in rows]
    queries = sorted({" ".join(filter(None, (event_format, category, row["city"], language)))
                      for row in rows for event_format in row["event_formats"].split("|")
                      for category in row["categories"].split("|")
                      for language in ("", *row["languages"].split("|"))}) if args.anchors else []
    sentences = [sentence for description in descriptions for sentence in split_sentences(description)]
    texts = list(dict.fromkeys([*descriptions, *sentences, *queries]))
    scorer = EmbeddingScorer(cache_path=args.output, client=ZeroEmbeddingsClient() if args.dry_run else None)
    embedded = scorer.cache_texts(texts)
    anchors = scorer.calibrate_anchors(queries, descriptions) if args.anchors else None
    data = json.loads(args.output.read_text(encoding="utf-8"))
    if (data["model"] != scorer.model or data["dimensions"] != scorer.dimensions
            or not all(content_key(scorer.model, text, scorer.dimensions) in data["vectors"] for text in texts)
            or anchors is not None and data.get("anchors") != asdict(anchors)):
        raise OSError(f"Could not persist all vectors to {args.output}")
    print(f"contractors={len(descriptions)} descriptions={len(descriptions)} sentences={len(sentences)} "
          f"unique_texts={len(texts)} embedded={embedded} reused={len(texts) - embedded} "
          f"vectors={len(data['vectors'])} model={scorer.model} dimensions={scorer.dimensions} dry_run={args.dry_run}")
    if anchors is not None:
        print(f"queries={len(queries)} anchors={asdict(anchors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
