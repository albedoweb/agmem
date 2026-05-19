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

# 2. Run
python benchmark/longmemeval/run.py --top-k 3,5,10,20 --out results/baseline

# Or via CLI
agmem eval-longmemeval --top-k 3,5,10,20 --out results/baseline
```

## Results

500 questions, BM25-only retrieval, per-question corpus (~48 distractor
sessions per question, median 48), no vectors, no reranking, no LLM
calls. Runtime ~13s on a laptop.

| K  | recall (strict) | recall_any | NDCG   |
|---:|----------------:|-----------:|-------:|
| 3  |           86.6% |      94.8% | 0.872  |
| 5  |       **90.8%** |      96.8% | 0.884  |
| 10 |           94.7% |      98.6% | 0.901  |
| 20 |           97.0% |      99.4% | 0.909  |

MRR: **0.9167**

Per question type, recall@5:

| Type                       | n   | R@5 strict | R@5 any |
|---|---:|---:|---:|
| multi-session              | 133 | 83.7%      | 97.0%   |
| temporal-reasoning         | 133 | 85.5%      | 94.0%   |
| knowledge-update           |  78 | 98.7%      | 100.0%  |
| single-session-user        |  70 | 98.6%      | 98.6%   |
| single-session-assistant   |  56 | 100.0%     | 100.0%  |
| single-session-preference  |  30 | 90.0%      | 90.0%   |

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
- 13s on a 2024 MacBook Pro M-series. Linear in question count.
