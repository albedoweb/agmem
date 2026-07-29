---
description: Install the agmem CLI and index the current repo for project memory
---

Set up agmem (persistent project memory) for the current repository. Follow
these steps, reporting progress briefly after each:

1. **Check the binary.** Run `command -v agmem`. If missing, install it —
   prefer whichever tool the user has:
   - `uv tool install --from git+https://github.com/albedoweb/agmem agmem`
   - or `pipx install git+https://github.com/albedoweb/agmem`
   Do NOT use `pip install agmem` — that is an unrelated PyPI package.

2. **Initialize and index.** From the repo root:
   ```bash
   agmem init && agmem index
   ```
   `init` is idempotent. Do not pass `--install-hook` — this plugin already
   provides the UserPromptSubmit hook, and installing both would inject
   context twice. (If the user previously ran `agmem init --install-hook` in
   this repo, suggest removing that hook from `.claude/settings.json`.)

3. **Optional git hooks.** Ask the user if they want memory auto-updated on
   commits and pulls; if yes:
   ```bash
   agmem init --install-git-hook
   ```

4. **Verify.** Run a sample query with identifier-style tokens taken from the
   repo (a module name, a resource, a distinctive file basename):
   ```bash
   agmem context "<identifiers from this repo>" -n 4
   ```
   Show the user the output. If results look thin, mention that memory
   improves as rules/facts are saved with `agmem remember`.

5. Point the user at `agmem --help` and note that everything lives in
   `.agmem/` as plain JSONL they can inspect with `cat` and commit to git if
   they want to share memory with teammates.
