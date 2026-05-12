# DESIGN.md

How agmem is built. For users who want the *why* behind the CLI surface — what's
in the store, how retrieval ranks results, what files end up where, and the
principles that constrain the project.

For getting started, see [README](./README.md). For the rolling implementation
plan and open questions, see `internal-docs/` in the source tree (gitignored;
maintainers only).

---

## Memory shape

Each entry in `.agmem/memories.jsonl` is one JSON line:

```json
{
  "id": "01J...",
  "ts": "2026-05-09T12:00:00+00:00",
  "text": "Reuse existing terraform modules in modules/aws/ ...",
  "kind": "rule",
  "tags": ["terraform", "modules"],
  "source": "manual",
  "source_ref": "terraform/modules/aws",
  "source_hash": "sha256:...",
  "source_commit": "19ea5748..."
}
```

| Field | Meaning |
|---|---|
| `id` | ULID. For indexer-written entries, it's a stable hash of `(source, source_ref)` so reindex preserves identity. |
| `ts` | When the entry was first written (ISO 8601 UTC). |
| `text` | Free-form; what the agent eventually sees. |
| `kind` | One of `rule` / `fact` / `pattern`. Drives retrieval boost (rules ×4, patterns ×1.5). |
| `tags` | Flat list. Used for `--tag` filter and as an extra signal in the `hot` cache. |
| `source` | `manual` (human via `agmem remember`), `index` (the indexer), or your own label. |
| `source_ref` | Path or `path#anchor` for section entries. |
| `source_hash` | sha256 of the referenced file (or section, for split markdown). Lets `verify` detect drift. |
| `source_commit` | Git HEAD when the entry was written. Lets you trace what was true when. |
| `verified_at` | Set by `agmem verify` when the file's hash still matches. |
| `drifted_at` | Set when verify finds a hash mismatch. |
| `deleted_at` | Soft-delete marker. `forget` sets it; search hides such entries. |

The store is **append-only JSONL** with `fcntl.LOCK_EX + fsync` on writes;
soft-deletes for revivability; atomic rewrites via `os.replace(.tmp)` for the
rare cases that need bulk updates (rename, reindex).

---

## How retrieval works

The pipeline is intentionally lexical — no embeddings, no neural inference, no
network calls.

1. **Tokenize** with `re.split(r"[\W_]+")`, so `aws_s3_bucket` matches `s3 bucket`
   and a query like "fastapi route handler" overlaps the indexer's structured
   tags.

2. **Stop-words filtered** — articles, common verbs (`use`, `make`, `work`),
   wh-words (`how`, `what`), and natural-language scaffolding (`about`, `still`,
   `just`). Generic English doesn't carry signal in retrieval.

3. **Expand the query** through `aliases` — built-in cloud aliases like
   `mongodb ↔ docdb`, `redis ↔ elasticache`, `lambda ↔ aws_lambda`, plus
   project-specific entries from `aliases.yaml` (curated) and `aliases.auto.yaml`
   (extracted by `agmem suggest-aliases` from your repo's glossary tables).

4. **BM25Okapi** with `b=0.85` (slightly stronger length penalty than the 0.75
   default) over a multi-field corpus per entry:
   - `text` — the body, weight ×1.
   - `source_ref` — repeated ×3 in the corpus, so a query that matches the file
     path beats a long doc that mentions the word once.
   - **Filename basename** ×2 (skipped for `README.md` to avoid noise).
   - **Markdown title** ×2 (extracted from indexer-generated text).

5. **Kind boost**: rules ×4, patterns ×1.5, facts ×1. Rules are
   meta-instructions that should override the agent's defaults — they need to
   surface even at modest BM25 scores.

6. **Render** grouped by kind in the order **Constraints → Facts → Patterns**,
   each entry showing `text`, `source_ref`, commit, and a `DRIFTED` marker if
   set. Section entries (long markdown split per H2) get their bodies trimmed
   to ~280 characters in `context` output to keep the agent's token budget
   sane; `recall` shows the full text for debugging.

A 1000-entry repo searches in ~50ms.

### Section-level entries

Markdown files with `>= 4` H2 sections and `> 1500` bytes get split: one master
overview entry plus one entry per H2 section (`source_ref = path.md#slug`).
Each section keeps its own `source_hash`, so drift detection is per-section.
Short docs stay as a single entry.

### Indexer

`agmem index` walks the repo (respects `.gitignore`, skips `node_modules` /
`__pycache__` / `.venv` / similar), parses `.tf` / `.py` / `.md` / `.mdx`, and
emits per-directory summaries plus per-file content entries. Re-running is
idempotent: same content → same `id`, `verified_at` survives.

`agmem update --since HEAD~1` is a diff-aware partial reindex. After the
post-merge git hook fires (i.e. after `git pull`), only the touched files and
their directory summaries are re-emitted; the rest of the store is untouched.

---

## File layout

```
my-repo/
├── .agmem/
│   ├── memories.jsonl       # the store (append-only JSONL, soft-delete)
│   ├── _hot.md              # pre-rendered cache for `agmem hot`
│   ├── aliases.yaml         # hand-curated project aliases (optional)
│   ├── aliases.auto.yaml    # auto-generated by `suggest-aliases` (optional)
│   ├── parsers.yaml         # extend KEY_FILES / EXT_LABELS, disable parsers
│   ├── testq.yaml           # retrieval regression fixture
│   ├── testq-snapshots/     # ranking snapshots for diff
│   └── config.yaml
├── CLAUDE.md                # contains an agmem block (if you ran --emit-claude-md)
├── .claude/settings.json    # contains the agmem hook (if --install-hook)
└── .git/hooks/
    ├── post-commit          # `agmem update --since HEAD~1` after each local commit
    ├── post-merge           # `agmem update --since ORIG_HEAD` after `git pull`
    └── post-rewrite         # same, gated on `$1 = rebase` (post-commit covers --amend)
```

`.agmem/` is **per-repo and per-machine**. Add it to `.gitignore` if you don't
want to share memory with teammates; commit it if you do.

### How the per-extension parsers work

Built-in parsers live in `agmem.parsers`:

- **`tf.py`** — Terraform: `resource`, `data`, `module`, `variable`, `output`,
  `provider`, `locals`. Tags include cloud-resource hints
  (`aws_docdb_cluster → mongodb`).
- **`py.py`** — Python: top-level classes, functions, FastAPI-style routes
  (regex on `@router.get/post/...`). Tags include framework hints
  (`Document → mongodb`, `BaseModel → pydantic`).
- **`md.py`** — Markdown: H1/H2/H3 headings, ADR-style status (single-line and
  multi-line under `## Status`), `last_updated:`, table-row density.
  `split_sections()` slices long docs into per-H2 chunks for the indexer.

A user file `.agmem/parsers.yaml` can extend `KEY_FILES`, add `EXT_LABELS`, or
disable a parser:

```yaml
disabled_parsers: [md]
key_files:
  Procfile: Heroku procfile
extension_labels:
  graphql: GraphQL schema
```

---

## Design principles

- **Local-first.** Everything is a file in `.agmem/`. Inspect with `cat`,
  version with git, edit in your editor. There's no daemon, no service, no
  remote.

- **CLI only, no MCP.** Tool integration is shell-out + stdout markdown. Works
  with anything that can call a binary — Claude Code, Codex, Cursor, a shell
  script in CI, your editor's snippet macro. MCP adds a trust gap and a moving
  target; we don't take that on.

- **Deterministic indexing.** `id = ULID(sha256("index::" + source_ref)[:16])`.
  Reindexing the same tree produces identical IDs, so `verified_at` survives
  re-runs and there's no duplicate explosion. A renamed file gets its history
  followed via `git log --diff-filter=R`, with `verify --follow` updating the
  `source_ref` automatically.

- **Honest provenance.** A claim about a file always carries the file's hash
  and commit at write time. `verify` re-hashes; mismatch → `drifted_at` set;
  `review` surfaces drifted/duplicate/stale/missing-source entries. Verify
  before trusting.

- **No fine-tuning.** Memory is in-context substrate, never weights. There is
  no training step. What you see in `.agmem/` is exactly what the agent will
  see at retrieval time.

- **Boring tech where possible.** BM25 over JSONL beats embeddings + vector DB
  for this scope: searches are fast, results are explainable, the data is grep-
  and diff-able. Embedding-based discovery (for fuzzy aliases) is on the
  roadmap as opt-in, not as the default substrate.

- **Don't write what you can derive.** Every memory should have provenance you
  can re-check. Indexer entries are derivable from the repo state; user
  entries should be facts/rules/patterns that *aren't* obviously in the code.

---

## Use with your agent

`agmem context` is plain CLI + stdout markdown, so **anything that can run a
shell command can consume it**.

### Claude Code (deepest integration)

```bash
agmem init --emit-claude-md --install-hook --install-git-hook
```

Three idempotent things:

- Marker-delimited block in `CLAUDE.md` telling the agent to call `agmem hot`
  at session start and `agmem context "<task>"` before non-trivial work.
- A `UserPromptSubmit` hook in `.claude/settings.json` that injects context
  automatically before every prompt.
- Git hooks (`post-commit`, `post-merge`, `post-rewrite`) so memory follows
  your local commits **and** teammate changes after `git pull` /
  `git pull --rebase`.

Re-running the install only updates the agmem sections, never your other
config.

### Codex / opencode (`AGENTS.md` convention)

Both Codex and opencode read `AGENTS.md` at the repo root. Add a few lines:

```markdown
## Project memory (agmem)

Before any non-trivial coding task, run:

\`\`\`bash
agmem hot           # instant cache: rules + recent facts/patterns (~500 tokens)
agmem context "<short task description>" -n 8
\`\`\`

Treat **Constraints** as project rules — do not contradict without explicit
override. Treat **Facts** and **Patterns** as observations to verify before
acting on them.
```

Then install the git hooks separately so memory stays fresh:

```bash
agmem init --install-git-hook
```

### Cursor (`.cursor/rules/*.mdc`)

Add a rule file `.cursor/rules/agmem.mdc` with the same instructions, scoped to
the whole repo. Cursor will surface it to its agent on every prompt.

### Aider (`.aider.conf.yml` / system message)

Add a `read` entry pointing to a file with the agmem instructions, or paste the
same block into Aider's system message. Aider's `/run agmem context "..."`
works mid-session.

### Anything else (CI, scripts, custom agents)

```bash
agmem context "$(cat task-description.txt)" -n 8 > /tmp/context.md
# pipe /tmp/context.md into whatever your agent reads as system context
```

The output is markdown with stable headers (`## Constraints`, `## Facts`,
`## Patterns`), so it's parseable if you need to.

---

## Comparison to similar tools

`agmem` overlaps with many things and is identical to none. Brief positioning:

| Tool family | Difference |
|---|---|
| **Mem0 / Letta / Zep / mem-style libraries** | They target chat agents and personal long-term memory across topics. agmem is code-specific, git-aware, diff-aware, and inspectable as plain JSONL. |
| **Vector DBs (Chroma, Qdrant, pgvector, etc.)** | agmem doesn't use embeddings at all. We index lexically and bound retrieval cost to ~50ms with no GPU/network. |
| **MCP servers (memory tools, filesystem mounts)** | agmem is shell-out, not MCP. No protocol coupling, no daemon. Wires into any agent that runs commands. |
| **Doc generators (mkdocs, automated readmes)** | Those produce static docs. agmem produces a queryable, source-linked, kind-typed memory store with drift detection. |
| **CLAUDE.md / `.cursorrules` / `.aiderules` etc.** | Those are global static rules. agmem retrieves *task-relevant* memory per query and lets you keep facts/patterns separate from rules. |
