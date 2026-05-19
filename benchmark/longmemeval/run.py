"""Run agmem against the LongMemEval-S benchmark.

Per-question corpus, matching the standard LongMemEval methodology.

Metrics reported:
- recall@K (strict): mean of ``|top_K ∩ gold| / |gold|`` across questions.
  This is the standard LongMemEval recall metric. Headline number.
- recall_any@K: fraction of questions with at least one gold session in
  top-K. Easier than strict recall on multi-gold questions; reported
  alongside for completeness.
- NDCG@K: real ``1/log2(i+2)`` DCG, normalized against the ideal ranking.
- MRR: ``mean(1/first_gold_rank if hit else 0)`` across ALL questions.

Usage::

    python benchmark/longmemeval/run.py [--top-k 3,5,10,20] [--out results/baseline]

Or via CLI::

    agmem eval-longmemeval [--top-k 3,5,10,20] [--out results/baseline]
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agmem.search import search

try:
    # When loaded as a package (`from benchmark.longmemeval.run import …`,
    # `python -m benchmark.longmemeval.run`).
    from .adapter import question_to_corpus
except ImportError:
    # When invoked directly: `python benchmark/longmemeval/run.py`.
    from adapter import question_to_corpus


@dataclass
class PerQuestionResult:
    question_id: str
    question_type: str
    n_gold: int
    recall_strict_at: dict[int, float]   # |topK ∩ gold| / |gold|
    recall_any_at: dict[int, bool]       # |topK ∩ gold| > 0
    first_rank: int | None               # rank of first gold (1-indexed), or None
    ndcg_at: dict[int, float]


@dataclass
class LongMemEvalReport:
    results: list[PerQuestionResult]
    top_k_values: list[int]

    @property
    def n_questions(self) -> int:
        return len(self.results)

    def recall_strict(self, k: int) -> float:
        if not self.results:
            return 0.0
        return statistics.mean(r.recall_strict_at.get(k, 0.0) for r in self.results)

    def recall_any(self, k: int) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.recall_any_at.get(k, False)) / len(self.results)

    def mean_ndcg(self, k: int) -> float:
        if not self.results:
            return 0.0
        return statistics.mean(r.ndcg_at.get(k, 0.0) for r in self.results)

    def mean_mrr(self) -> float:
        """Standard MRR: misses count as 0, denominator is N (all questions)."""
        if not self.results:
            return 0.0
        return statistics.mean(
            (1.0 / r.first_rank) if r.first_rank else 0.0
            for r in self.results
        )

    def by_type(self, k: int) -> list[tuple[str, int, float, float]]:
        """Returns rows of (type, n, recall_strict@k, recall_any@k)."""
        buckets: dict[str, list[PerQuestionResult]] = {}
        for r in self.results:
            buckets.setdefault(r.question_type, []).append(r)
        out = []
        for t in sorted(buckets, key=lambda x: -len(buckets[x])):
            bucket = buckets[t]
            n = len(bucket)
            rs = statistics.mean(q.recall_strict_at.get(k, 0.0) for q in bucket) if n else 0.0
            ra = sum(1 for q in bucket if q.recall_any_at.get(k, False)) / n if n else 0.0
            out.append((t, n, rs, ra))
        return out

    def summary(self) -> str:
        lines = [
            "LongMemEval-S — agmem BM25-only",
            "=" * 60,
            f"Questions: {self.n_questions}",
            f"Date:      {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"{'K':>4}  {'recall (strict)':>16}  {'recall_any':>11}  {'NDCG':>7}",
            "-" * 48,
        ]
        for k in sorted(self.top_k_values):
            lines.append(
                f"  {k:>2}  {self.recall_strict(k):>15.1%}  "
                f"{self.recall_any(k):>10.1%}  {self.mean_ndcg(k):>7.4f}"
            )
        lines.append("")
        lines.append(f"MRR:       {self.mean_mrr():.4f}")

        by_type = self.by_type(5)
        if by_type:
            lines.append("")
            lines.append(f"{'Type':<32} {'n':>4}  {'R@5 strict':>11}  {'R@5 any':>8}")
            lines.append("-" * 60)
            for t, n, rs, ra in by_type:
                lines.append(f"{t:<32} {n:>4}  {rs:>10.1%}  {ra:>7.1%}")

        return "\n".join(lines)

    def to_csv_rows(self) -> list[dict]:
        rows = []
        for r in self.results:
            row = {
                "question_id": r.question_id,
                "question_type": r.question_type,
                "n_gold": r.n_gold,
                "first_rank": r.first_rank if r.first_rank else "",
                "reciprocal_rank": round(1.0 / r.first_rank, 4) if r.first_rank else 0.0,
            }
            for k in sorted(self.top_k_values):
                row[f"recall_strict@{k}"] = round(r.recall_strict_at.get(k, 0.0), 4)
                row[f"recall_any@{k}"] = r.recall_any_at.get(k, False)
                row[f"NDCG@{k}"] = round(r.ndcg_at.get(k, 0.0), 4)
            rows.append(row)
        return rows

    def to_dict(self) -> dict:
        return {
            "n_questions": self.n_questions,
            "top_k": sorted(self.top_k_values),
            "recall_strict": {str(k): self.recall_strict(k) for k in self.top_k_values},
            "recall_any": {str(k): self.recall_any(k) for k in self.top_k_values},
            "mrr": self.mean_mrr(),
            "ndcg": {str(k): self.mean_ndcg(k) for k in self.top_k_values},
            "by_type": [
                {"type": t, "n": n, "recall_strict@5": rs, "recall_any@5": ra}
                for t, n, rs, ra in self.by_type(5)
            ],
            "per_question": [
                {
                    "question_id": r.question_id,
                    "question_type": r.question_type,
                    "n_gold": r.n_gold,
                    "first_rank": r.first_rank,
                    "recall_strict": {str(k): v for k, v in r.recall_strict_at.items()},
                    "recall_any": {str(k): v for k, v in r.recall_any_at.items()},
                    "ndcg": {str(k): v for k, v in r.ndcg_at.items()},
                }
                for r in self.results
            ],
        }


def _load_dataset(cache_dir: Path | None = None) -> list[dict]:
    if cache_dir is None:
        cache_dir = Path(__file__).resolve().parent / "cache"

    jsonl_path = cache_dir / "data.jsonl"
    if not jsonl_path.exists():
        raise SystemExit(
            f"Dataset not cached at {jsonl_path}.\n"
            "  Run: python benchmark/longmemeval/download.py"
        )

    rows = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _ndcg_at(ranked_ids: list[str], gold_ids: set[str], k: int) -> float:
    """Standard NDCG@K with binary relevance: gain = 1 if gold else 0,
    discount = 1/log2(rank+1) where rank is 1-indexed.
    """
    dcg = 0.0
    for i, rid in enumerate(ranked_ids[:k], start=1):
        if rid in gold_ids:
            dcg += 1.0 / math.log2(i + 1)
    ideal = 0.0
    for i in range(1, min(len(gold_ids), k) + 1):
        ideal += 1.0 / math.log2(i + 1)
    return dcg / ideal if ideal > 0 else 0.0


def _run_single_question(
    question: dict,
    top_k_values: list[int],
) -> PerQuestionResult:
    entries = question_to_corpus(question)
    max_k = max(top_k_values)
    ranked = search(question["question"], entries, top_n=max_k)
    ranked_ids = [e.id for e, _ in ranked]
    gold = set(question.get("answer_session_ids", []))
    n_gold = len(gold)

    recall_strict_at: dict[int, float] = {}
    recall_any_at: dict[int, bool] = {}
    ndcg_at: dict[int, float] = {}
    for k in top_k_values:
        top_k_set = set(ranked_ids[:k])
        hits = len(top_k_set & gold)
        recall_strict_at[k] = (hits / n_gold) if n_gold else 0.0
        recall_any_at[k] = hits > 0
        ndcg_at[k] = round(_ndcg_at(ranked_ids, gold, k), 4)

    first_rank: int | None = None
    for i, rid in enumerate(ranked_ids, start=1):
        if rid in gold:
            first_rank = i
            break

    return PerQuestionResult(
        question_id=question["question_id"],
        question_type=question.get("question_type", "unknown"),
        n_gold=n_gold,
        recall_strict_at=recall_strict_at,
        recall_any_at=recall_any_at,
        first_rank=first_rank,
        ndcg_at=ndcg_at,
    )


def run_longmemeval(
    top_k_values: list[int] | None = None,
    cache_dir: Path | None = None,
    limit: int | None = None,
) -> LongMemEvalReport:
    if top_k_values is None:
        top_k_values = [3, 5, 10, 20]

    questions = _load_dataset(cache_dir)
    if limit is not None:
        questions = questions[:limit]

    results: list[PerQuestionResult] = []
    t0 = time.monotonic()
    for i, q in enumerate(questions):
        r = _run_single_question(q, top_k_values)
        results.append(r)
        elapsed = time.monotonic() - t0
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  [{i + 1}/{len(questions)}]  {(i + 1) / elapsed:.1f} q/s  "
                  f"R@5(strict)={statistics.mean(r2.recall_strict_at.get(5, 0.0) for r2 in results):.1%}  "
                  f"latest: {r.question_id}  type={r.question_type}")

    total = time.monotonic() - t0
    print(f"\nDone in {total:.1f}s ({len(questions) / total:.1f} q/s)")

    return LongMemEvalReport(
        results=results,
        top_k_values=top_k_values,
    )


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="LongMemEval-S benchmark for agmem")
    p.add_argument("--top-k", default="3,5,10,20", help="K values for recall@K")
    p.add_argument("--out", default=None, help="Base path for CSV/JSON output")
    p.add_argument("--limit", type=int, default=None, help="Max questions to evaluate")
    args = p.parse_args()

    ks = [int(x.strip()) for x in args.top_k.split(",")]
    report = run_longmemeval(top_k_values=ks, limit=args.limit)

    print()
    print(report.summary())

    if args.out:
        out_base = Path(args.out)
        csv_path = out_base.with_suffix(".csv")
        json_path = out_base.with_suffix(".json")
        with open(csv_path, "w", newline="") as f:
            rows = report.to_csv_rows()
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        with open(json_path, "w") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\nWrote {csv_path} ({len(rows)} rows)")
        print(f"Wrote {json_path}")
