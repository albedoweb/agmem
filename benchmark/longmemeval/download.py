"""Download the LongMemEval-S dataset from HuggingFace and cache locally.

Usage::

    python benchmark/longmemeval/download.py [--revision <hf-commit-sha>]

Depends on ``pip install datasets``.

Reproducibility: passing ``--revision`` pins the HuggingFace dataset to a
specific commit so the benchmark stays comparable when the upstream dataset
is updated. The resolved revision (or ``"main"`` if unpinned) is recorded
in ``dataset_info.json`` next to the cached data.
"""

from __future__ import annotations

import argparse
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / "cache"

# Dataset identity.
DATASET_REPO = "xiaowu0162/longmemeval-cleaned"
DATASET_SPLIT = "longmemeval_s_cleaned"


def download(cache_dir: Path | None = None, revision: str | None = None) -> Path:
    if cache_dir is None:
        cache_dir = CACHE_DIR

    jsonl_path = cache_dir / "data.jsonl"
    info_path = cache_dir / "dataset_info.json"

    if jsonl_path.exists():
        import json
        n = 0
        cached_rev = "(unknown)"
        if info_path.exists():
            info = json.loads(info_path.read_text())
            n = info.get("n_questions", 0)
            cached_rev = info.get("revision", "(unknown)")
        print(f"Dataset already cached at {cache_dir} ({n} questions, revision={cached_rev})")
        if revision and cached_rev not in (revision, "(unknown)"):
            print(
                f"  WARNING: cached revision {cached_rev!r} ≠ requested {revision!r}.\n"
                f"  Delete the cache and re-run to refresh."
            )
        return cache_dir

    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit(
            "huggingface datasets not installed.\n"
            "  pip install datasets\n"
            "  uv pip install datasets"
        )

    import json

    rev_label = revision or "main (unpinned — see --revision)"
    print(f"Downloading {DATASET_REPO} (split={DATASET_SPLIT}, revision={rev_label})...")
    ds = load_dataset(
        DATASET_REPO,
        split=DATASET_SPLIT,
        revision=revision,
        streaming=True,
    )

    cache_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(jsonl_path, "w") as f:
        for row in ds:
            f.write(json.dumps(row, default=str) + "\n")
            count += 1

    info = {
        "n_questions": count,
        "repo": DATASET_REPO,
        "split": DATASET_SPLIT,
        "revision": revision or "main",
    }
    with open(info_path, "w") as f:
        json.dump(info, f, indent=2)

    print(f"Cached {count} questions to {jsonl_path}")
    print(f"Wrote {info_path}")
    return cache_dir


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download LongMemEval-S dataset for the agmem benchmark")
    p.add_argument(
        "--revision",
        default=None,
        help="Pin to a HuggingFace dataset commit SHA. Default: 'main' (latest, unpinned).",
    )
    args = p.parse_args()
    download(revision=args.revision)
