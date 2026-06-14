# LongMemEval-S — agmem benchmark

Evaluates agmem's BM25 retrieval against the LongMemEval-S benchmark
([arxiv 2410.10813](https://arxiv.org/abs/2410.10813)), which tests
long-term conversational memory in LLM assistants.

LongMemEval is a **conversational** memory benchmark — it measures
chat-history retrieval, not codebase retrieval. agmem's primary use is
code memory; see Track A (`agmem eval-agmem`) for the metric that drives
the real tuning. LongMemEval is a parity number — useful for sanity, not
the loss function.

## Run

```bash
pip install datasets

# 1. Download (pin a revision for reproducibility)
python benchmark/longmemeval/download.py --revision <hf-commit-sha>

# 2a. BM25-only run (fast, no extras)
python benchmark/longmemeval/run.py --top-k 3,5,8,10,20 --out results/bm25

# 2b. Hybrid run (matches the shipped repo default of α=0.3; needs
#     `pip install 'agmem[hybrid]'` — sentence-transformers + numpy)
python benchmark/longmemeval/run.py \
  --top-k 3,5,8,10,20 --hybrid-alpha 0.3 \
  --out results/v2-hybrid03

# Or via CLI
agmem eval-longmemeval --top-k 3,5,8,10,20 --hybrid-alpha 0.3 --out results/baseline
```

## Results — 2026-06-13

500 questions, per-question corpus (~48 distractor sessions per question,
median 48), no LLM calls.

### Headline: **hybrid α=0.3** (matches shipped repo default)

| K  | recall (strict) | recall_any | NDCG   |
|---:|----------------:|-----------:|-------:|
| 3  |           89.5% |      96.0% | 0.899  |
| 5  |       **93.9%** |      97.6% | 0.912  |
| 8  |           96.1% |      98.8% | 0.922  |
| 10 |           97.0% |      99.2% | 0.925  |
| 20 |           99.1% |      99.8% | 0.932  |

MRR: **0.9316**. Cold runtime: 152s on a laptop (embedding 25k session
entries); warm re-runs reuse the content-hash cache and finish in ~15s.

### Baseline: **BM25-only** (no extras, for reference)

| K  | recall (strict) | recall_any | NDCG   |
|---:|----------------:|-----------:|-------:|
| 3  |           87.3% |      95.0% | 0.877  |
| 5  |           91.6% |      97.0% | 0.889  |
| 8  |           94.6% |      97.6% | 0.903  |
| 10 |           95.1% |      97.8% | 0.905  |
| 20 |           97.9% |      99.8% | 0.914  |

MRR: 0.9158. Runtime ~17s.

### Δ from BM25 → hybrid by question type (R@5 strict)

The hybrid score (BM25 ⊕ MiniLM-L6 cosine, α=0.3 fused via min-max
normalisation) helps most on the categories where lexical overlap is
weakest:

| Type                       | n   | BM25-only | Hybrid α=0.3 | Δ        |
|---|---:|---:|---:|---:|
| multi-session              | 133 | 87.1%     | **90.8%**    | +3.7 pp  |
| temporal-reasoning         | 133 | 85.3%     | **88.9%**    | +3.6 pp  |
| single-session-preference  |  30 | 86.7%     | 90.0%        | +3.3 pp  |
| knowledge-update           |  78 | 99.4%     | 99.4%        | saturated|
| single-session-user        |  70 | 98.6%     | 100.0%       | +1.4 pp  |
| single-session-assistant   |  56 | 100.0%    | 100.0%       | saturated|

## Methodology

**Per-question corpus.** Each LongMemEval question ships with ~48 historical
dialogue sessions (gold + distractors). For each question, we build a fresh
corpus of those sessions as agmem `MemoryEntry` records and run
`agmem.search.search()` against the question text. No cross-question state.

**recall@K (strict)** is the standard LongMemEval metric:
`|top_K ∩ gold| / |gold|`, averaged over questions. 65% of LongMemEval-S
questions have 2-6 gold sessions, so strict recall measures whether all
gold sessions surface — much harder than "at least one hit".

**recall_any@K** is a softer variant: fraction of questions where at
least one gold session appears in top-K. Reported alongside strict for
completeness; not the headline.

**NDCG@K** uses standard `1/log2(rank+1)` discount with binary relevance
and ideal-DCG normalisation.

**MRR** is `mean(1/first_gold_rank if hit else 0)` over **all** questions
(misses contribute 0). Standard definition — no miss-skipping.

**Source-ref hygiene.** LongMemEval gold session IDs all carry an
`answer_` prefix while distractors carry `sharegpt_` / `ultrachat_`
prefixes. Putting the raw session_id into `source_ref` (which agmem
weights ×3 in the BM25 corpus + ×2 for the basename) would leak the
literal token `answer` into the corpus of gold entries only. The adapter
substitutes an opaque positional id (`s000`, `s001`, ...) into
`source_ref` to neutralise this; `entry.id` keeps the original
session_id so gold-set comparison still works.

## Reproducibility

- `download.py --revision <sha>` pins the HuggingFace dataset to a
  specific commit and records it in `cache/dataset_info.json`. Re-runs
  with a different `--revision` print a warning.
- BM25 is deterministic — same input, same output across runs.
- Hybrid embeddings are content-hash cached under
  `benchmark/longmemeval/cache/embeddings/` (see `--embed-cache-dir`);
  warm re-runs read from disk and skip the model.
- BM25-only: ~17s on a 2024 MacBook Pro M-series. Hybrid cold: ~152s.
  Linear in question count.
