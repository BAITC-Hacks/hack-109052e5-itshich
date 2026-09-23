#!/usr/bin/env python3
"""Build embeddings, or round and compact an existing cache offline with --compact."""
import argparse
import csv
import json
import os
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

    def create(self, *, model, input):
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[0.0] * 8)
                                     for i, _ in enumerate(input)])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", "--input", type=Path, default=ROOT / "data/contractors.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/embeddings.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Use a fake client that returns 8-dimensional zero vectors")
    mode.add_argument("--compact", action="store_true", help="Round the existing --output cache to 6 decimals without network")
    args = parser.parse_args(argv)
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
    if not args.dry_run and not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is required; use --dry-run for offline verification")

    # This reader deliberately does not depend on matcher.data or its loader.
    extra = args.csv.with_name("synthetic_extra.csv")
    sources = [args.csv] + ([extra] if extra != args.csv and extra.exists() else [])
    descriptions = []
    for source in sources:
        with source.open(encoding="utf-8-sig", newline="") as handle:
            descriptions.extend(row["description"] for row in csv.DictReader(handle))
    sentences = [sentence for description in descriptions for sentence in split_sentences(description)]
    texts = list(dict.fromkeys([*descriptions, *sentences]))
    scorer = EmbeddingScorer(cache_path=args.output, client=ZeroEmbeddingsClient() if args.dry_run else None)
    embedded = scorer.cache_texts(texts)
    data = json.loads(args.output.read_text(encoding="utf-8"))
    if data["model"] != scorer.model or not all(content_key(scorer.model, text) in data["vectors"] for text in texts):
        raise OSError(f"Could not persist all vectors to {args.output}")
    print(f"contractors={len(descriptions)} descriptions={len(descriptions)} sentences={len(sentences)} "
          f"unique_texts={len(texts)} embedded={embedded} reused={len(texts) - embedded} "
          f"vectors={len(data['vectors'])} model={scorer.model} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
