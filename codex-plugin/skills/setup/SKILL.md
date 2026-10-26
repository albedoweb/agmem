---
name: setup
description: Install the agmem CLI and initialize project memory when the user requests agmem setup or onboarding in a repository.
---

# Set up agmem

Work in the target repository. Check for `agmem` first. If absent, install
from the project source using an available isolated installer:

```sh
uv tool install --from git+https://github.com/albedoweb/agmem agmem
```

Alternatively use `pipx install git+https://github.com/albedoweb/agmem`.
The project needs Python 3.12 or newer; with uv, add `--python 3.12` if needed.
For development in a local agmem checkout, `uv tool install --editable .`
uses that checkout. Never install the unrelated PyPI package named `agmem`.

Inspect the repository's ignore rules and private/untracked notes before
indexing. The indexer respects the root `.gitignore` but can include untracked
files. Exclude private material before generating an index intended for sharing.

```sh
agmem init
agmem index
agmem context '<identifiers from this repository>' -n 4
```

Inspect the resulting source references and verify that the sample query points
to real code. Explain where `.agmem/` lives and how to refresh it.

This Codex plugin provides skills; it does not install background hooks or a
watcher. Install Git hooks only when requested. Enable a shared index only when
the user requests it; preserve the repository's default ignore policy otherwise.
If Codex is running in a linked worktree, explain that agmem currently resolves
memory to the main checkout before initializing or updating it.
