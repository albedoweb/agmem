# Changelog

All notable changes to agmem are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer
(pre-1.0: minor bumps may break APIs).

## [Unreleased]

## [0.1.0] — 2026-07-25

First public release.

### Retrieval
- BM25 retrieval (`rank-bm25`, b=0.85) with multi-field weighting
  (source path ×3, basename ×2, title ×2), kind boosts (`rule` ×4,
  `pattern` ×1.5), and conservative stemming.
- Query alias expansion from auto-extracted glossary tables plus
  user-defined `.agmem/aliases.yaml`.
- Optional hybrid dense retrieval (`[hybrid]` extra): local
  sentence-transformers embeddings fused with BM25 via min-max score
  interpolation; content-hash `.npy` cache; graceful BM25 fallback when
  the model is unavailable or offline.
- Session-aware retrieval (`--session`): demotes already-seen entries,
  boosts sibling sections, appends a "Haven't seen yet" hint.
- Low-signal query warning: meta-word-only queries ("review pull request")
  get a stderr nudge to rewrite around identifiers.

### Indexing
- Parsers: Terraform, Python (incl. FastAPI routes + docstring first
  lines), Go (types, methods, HTTP routes), YAML/Helm (dotted key-paths
  with values), Markdown (H2 section splitting, ADR status, glossaries).
- Git-aware incremental updates (`agmem update --since`), full reindex
  with atomic rewrite, drift tracking via `source_hash`/`source_commit`.
- Optional multi-repo file watcher (`agmem watch`).

### Agent integration
- `agmem init --emit-claude-md --install-hook --install-git-hook`:
  idempotent CLAUDE.md block, Claude Code `UserPromptSubmit` hook
  (session-aware), post-commit/post-merge/post-rewrite git hooks.

### Evaluation
- `agmem eval-agmem`: Track A eval against real agent-session logs
  (strict/soft Hit@K, recall, MRR, follow-rate) + parameter sweeps.
- LongMemEval-S benchmark harness: R@5 91.6% (BM25) / 93.9% (hybrid)
  on 500 questions, no LLM calls.
