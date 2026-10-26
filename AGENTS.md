# Working on agmem

Before broad exploration, query the project memory using concrete identifiers:

```sh
agmem context "indexer watch source_ref" -n 8
```

Use the result to locate code, then inspect the referenced files. The committed
index is a snapshot, not proof that a claim is current. Current source files,
tests, and applicable instructions take precedence over retrieved text. Search
the source directly when memory is missing, stale, or insufficient.

Use one-shot queries by default: `--session` shares state between agents, and
linked Git worktrees currently resolve their memory to the main checkout.

Development setup: `uv sync`. Checks: `uv run pytest -q` and
`uv run ruff check src/ tests/`. Hybrid model tests are optional and may skip.

After changing indexed source files, refresh with a full `agmem index`, review
the diff in `.agmem/memories.jsonl`, and include the updated snapshot when
appropriate. Incremental updates refresh affected directories; use a full index
for the shared repository-wide snapshot. Keep runtime state and private notes
out of the shared index.
See [shared index maintenance](docs/shared-index.md).

The Codex plugin and installation steps are in [docs/codex-plugin.md](docs/codex-plugin.md).
