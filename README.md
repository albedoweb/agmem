# agmem

**Persistent, source-linked project memory for coding agents.** Local JSONL
store. BM25 retrieval. Git-aware — `post-merge` hook keeps memory in sync when
teammates push. No embeddings, no MCP, no SaaS. Apache 2.0.

> Your `CLAUDE.md` / `AGENTS.md` / `.cursor/rules/` are static. Your repo
> isn't. `agmem` indexes your code (Terraform, Python, Markdown, ADRs),
> extracts glossary aliases automatically, and answers task-relevant queries
> in ~50ms with source hashes you can `verify`.

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

```text
# Context for: rds bastion ec2 instance

## Constraints
- For bastion hosts in front of AWS data services, prefer
  modules/aws/rds-bastion-ec2 over modules/ec2-instance (generic).
  Mirrors the prod-style pattern.
  (manual · 2026-04-21 · ref: terraform/aws/prod/us-west-2/rds/rds_bastion.tf)

## Facts
- Section "S3 module variables" of `services/s3.md` — s3_bucket_name,
  mandatory_tags, kms_deletion_window_in_days; module path: terraform/modules/aws/s3 […]
  (index · 2026-05-09 · ref: services/s3.md#s3-module-variables)
```

## What it does

`agmem index` walks the repo (respecting `.gitignore`) and extracts:

- Terraform resources, modules, variables, outputs
- Python classes, top-level functions, FastAPI-style routes
- Markdown sections (long docs split per H2) + ADR status
- Glossary terms — `agmem suggest-aliases` lifts `bomber → webhook, delivery`
  out of `glossary.md` so a query for "core" finds `citadel-backend`

`agmem context "<task>"` returns task-relevant chunks ranked by BM25 + lexical
aliases, grouped as **Constraints / Facts / Patterns**, each with `source_ref`
and commit. Hand the markdown to your agent.

Phrase queries as **identifier-rich noun fragments** (`waf-alb-external mytruv
istio-gateway-external count`), not full sentences. BM25 ranks short token
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

agmem's BM25 retrieval against [LongMemEval-S](https://arxiv.org/abs/2410.10813)
(500 questions, per-question corpus of ~48 dialogue sessions):

| K  | recall (strict) | recall_any | NDCG  |
|---:|----------------:|-----------:|------:|
| 3  |           86.6% |      94.8% | 0.872 |
| 5  |       **90.8%** |      96.8% | 0.884 |
| 10 |           94.7% |      98.6% | 0.901 |
| 20 |           97.0% |      99.4% | 0.909 |

MRR: **0.917**. Runtime: ~13s on a laptop. No vectors, no reranking, no LLM
calls.

`recall (strict)` = `|top_K ∩ gold| / |gold|` averaged over questions
(LongMemEval standard; 65% of questions have 2-6 gold sessions, so this is
harder than "any hit in top-K"). `recall_any` is the lenient "≥1 gold in
top-K" variant. NDCG is real `1/log2(rank+1)`.

LongMemEval is conversational — it measures chat-history retrieval, not code.
agmem's primary use is code memory; see
[`benchmark/longmemeval/`](./benchmark/longmemeval/) for full methodology and
reproduction steps, and `agmem eval-agmem` for the code-retrieval Track A
metric that drives day-to-day tuning.

## Inspirations

- Anthropic — [*Effective Context Engineering for AI Agents*](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (2026)
- A-MEM: [*Agentic Memory for LLM Agents*](https://github.com/agiresearch/a-mem) (Wang et al., 2025)
- Karpathy's *LLM Wiki* gist + Sara Nobrega's [TDS write-up](https://towardsdatascience.com/give-your-ai-unlimited-updated-context/) (2026)

What we deliberately *don't* do — fine-tune, run a daemon, take an MCP
dependency, store anything in the cloud — follows from the local-first
thesis: memory should outlive any single agent, and you should be able to
read it with `cat`.

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
