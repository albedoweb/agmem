# Shared source index

This repository commits `.agmem/config.yaml` and `.agmem/memories.jsonl` as a
reviewed snapshot of its public source. Other `.agmem` files remain ignored:
retrieval sessions, hot caches, watcher queues, embeddings, and evaluation data.
The local `AGENT_BRIEF.md` is excluded from both Git and indexing.

Refresh from the repository root after source changes:

```sh
agmem index
git diff -- .agmem/memories.jsonl
```

Review all new source references and text before staging the snapshot. The
indexer may include untracked files that are not excluded by the root
`.gitignore`; ignore private files before indexing. Do not force-add runtime
state or private memories. Treat the snapshot as a navigation aid and verify
answers against current source.

Use full indexing for this snapshot so the repository-wide aggregate is also
refreshed. Incremental updates replace derived entries for affected directories,
including obsolete Markdown sections. Watcher startup performs a full reindex
to reconcile downtime changes; subsequent polls remain incremental. Manual
memories are preserved and require their own review.

`source_commit` records HEAD at indexing time, so an index included in a new
commit normally refers to its parent. File hashes describe the indexed working
tree contents. Do not repeatedly amend a commit just to embed its own hash.
