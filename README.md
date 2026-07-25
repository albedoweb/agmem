# agmem

[![CI](https://github.com/albedoweb/agmem/actions/workflows/ci.yml/badge.svg)](https://github.com/albedoweb/agmem/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

**Persistent, source-linked project memory for coding agents.** Local JSONL
store. BM25-first retrieval — embeddings are an optional local add-on, never
required. Git-aware — `post-merge` hook keeps memory in sync when teammates
push. No MCP, no SaaS, nothing leaves your machine. Apache 2.0.

![agmem demo](./docs/demo/agmem-demo.gif)

> Your `CLAUDE.md` / `AGENTS.md` / `.cursor/rules/` are static. Your repo
> isn't. `agmem` indexes your code (Terraform, Python, Go, YAML/Helm,
> Markdown), extracts glossary aliases automatically, and answers
> task-relevant queries in well under a second (sub-100ms search on a
> few-thousand-entry store) with source hashes you can `verify`.

Works with **Claude Code, Codex, opencode, Cursor, Aider** — anything that
runs a shell command. 60-second install:

```bash
# Alpha — not yet on PyPI under this name. Install from source:
uv tool install --from git+https://github.com/albedoweb/agmem agmem
# …or from a local clone:
git clone https://github.com/albedoweb/agmem && uv tool install --from ./agmem agmem

cd my-repo && agmem init && agmem index
agmem context "rds bastion ec2 instance"
```

Tested on macOS and Linux. Windows is best-effort: the package imports and
core commands run, but file locking degrades to a no-op (assume a single
writer) and it isn't covered by CI yet.

```text
# Context for: rds bastion ec2 instance

## Constraints
- For bastion hosts in front of AWS data services, prefer
  modules/aws/rds-bastion-ec2 over modules/ec2-instance (generic).
  Mirrors the prod-style pattern.
  (manual · 2026-04-21 · ref: terraform/rds/rds_bastion.tf)

## Facts
- Section "S3 module variables" of `services/s3.md` — s3_bucket_name,
  mandatory_tags, kms_deletion_window_in_days; module path: terraform/modules/aws/s3 […]
  (index · 2026-05-09 · ref: services/s3.md#s3-module-variables)
```

## What it does

`agmem index` walks the repo (respecting `.gitignore`) and extracts:

- Terraform resources, modules, variables, outputs
- Python classes, top-level functions, FastAPI-style routes
- Go types, functions, methods, chi/gin/echo HTTP routes
- YAML / Helm values flattened into searchable dotted key-paths
- Markdown sections (long docs split per H2) + PRD status

`agmem context "<task>"` returns task-relevant chunks ranked by BM25 + lexical
aliases, grouped as **Constraints / Facts / Patterns**, each with `source_ref`
and commit. Hand the markdown to your agent.

Phrase queries as **identifier-rich noun fragments** (`waf-alb-public storefront
ingress-gateway-external count`), not full sentences. BM25 ranks short token
matches over verbose natural language — the agent guide block emitted by
`--emit-claude-md` has the full phrasing convention with examples.

For things the indexer can't infer — team conventions, "don't do X again" —
write them once with `agmem remember "..." --kind rule`. Rules get a 4× score
boost in retrieval.

## Use with your agent

`agmem context` is plain CLI + stdout markdown, so anything that shells out
works. Claude Code has the deepest integration:

```bash
agmem init --emit-claude-md --install-hook --install-git-hook
```

Adds an idempotent block to `CLAUDE.md`, a `UserPromptSubmit` hook, and three
git hooks (`post-commit`, `post-merge`, `post-rewrite`) so memory follows
local commits **and** teammate changes after `git pull`.

For multi-query workflows, add `--session`: follow-up calls demote results
you've already seen, boost sibling sections of files you've read, and end
with a "Haven't seen yet" hint — so each query surfaces something new
instead of repeating the top hits.

For Codex / opencode (`AGENTS.md`), Cursor (`.cursor/rules/`), Aider, or
custom CI scripts — see the [agent integration guide](./DESIGN.md#use-with-your-agent).

## Why not just grep?

- Tokenizes structurally — `s3 bucket` matches `aws_s3_bucket`.
- Knows aliases from your repo's glossary tables (auto-extracted).
- Ranks by relevance (BM25 + per-field weights + 4× boost for `kind=rule`).
- Returns kind-typed answers — Constraints above Facts above Patterns.
- Tracks drift — every entry carries `source_hash` + `source_commit`.
- Searches inside markdown sections, not just whole files.

## Benchmark

agmem against [LongMemEval-S](https://arxiv.org/abs/2410.10813)
(500 questions, per-question corpus of ~48 dialogue sessions). Two
configurations — the default BM25-only install (no heavy deps) and the
opt-in **hybrid α=0.3** mode (install the `[hybrid]` extra, then enable
with two lines in `.agmem/config.yaml`):

| K  | BM25 strict | **Hybrid α=0.3 strict** | recall_any (hybrid) | NDCG (hybrid) |
|---:|------------:|------------------------:|--------------------:|--------------:|
| 3  |       87.3% |               **89.5%** |              96.0%  |  0.899 |
| 5  |       91.6% |               **93.9%** |              97.6%  |  0.912 |
| 8  |       94.6% |               **96.1%** |              98.8%  |  0.922 |
| 10 |       95.1% |               **97.0%** |              99.2%  |  0.925 |
| 20 |       97.9% |               **99.1%** |              99.8%  |  0.932 |

MRR: BM25 0.916  →  **hybrid 0.932**. BM25-only runs in ~17s; hybrid
~152s cold (embeds 25k sessions) and ~15s warm via the on-disk
content-hash cache. No LLM calls, no network at query time.

Heads-up on the extra's weight: `[hybrid]` pulls `sentence-transformers`,
which installs PyTorch (~2 GB) and downloads a 90 MB embedding model on
first use. The default install has zero heavy dependencies — hybrid is
purely additive:

```yaml
# .agmem/config.yaml — after `pip install 'agmem[hybrid]'`
hybrid:
  enabled: true
  alpha: 0.3
```

`recall (strict)` = `|top_K ∩ gold| / |gold|` averaged over questions
(LongMemEval standard; 65% of questions have 2-6 gold sessions, so this is
harder than "any hit in top-K"). `recall_any` is the lenient "≥1 gold in
top-K" variant. NDCG is real `1/log2(rank+1)`.

The hybrid lift is concentrated on the hardest categories — **temporal-
reasoning +3.6 pp** R@5 and **multi-session +3.7 pp** — where lexical
overlap is weakest; categories already saturated by BM25 (knowledge-update,
single-session-*) stay at 100%.

LongMemEval is conversational — it measures chat-history retrieval, not code.
agmem's primary use is code memory; see
[`benchmark/longmemeval/`](./benchmark/longmemeval/) for full methodology and
reproduction steps, and `agmem eval-agmem` for the code-retrieval Track A
metric that drives day-to-day tuning.

## Inspirations

- Anthropic — [*Effective Context Engineering for AI Agents*](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (2026)
- A-MEM: [*Agentic Memory for LLM Agents*](https://github.com/agiresearch/a-mem) (Wang et al., 2025)
- Karpathy's *LLM Wiki* gist + Sara Nobrega's [TDS write-up](https://towardsdatascience.com/give-your-ai-unlimited-updated-context/) (2026)

What we deliberately *don't* do — fine-tune, require a daemon or server,
take an MCP dependency, store anything in the cloud — follows from the
local-first thesis: memory should outlive any single agent, and you should
be able to read it with `cat`. (An optional `agmem watch` file-watcher
exists for hot reindexing; nothing depends on it running.)

## Status

Alpha. Daily-driven on real repos since April 2026. APIs may change between
minor versions until 1.0.

## More

- **[DESIGN.md](./DESIGN.md)** — memory shape, retrieval pipeline, file
  layout, design principles, agent integration guide.
- `agmem --help` — full command reference.

## Development

```bash
git clone <repo> && cd agmem
uv sync && uv run pytest -q
```

## License

Apache 2.0.
