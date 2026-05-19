"""Configuration: .agmem directory discovery, config.yaml read/write."""

import json
import os
from pathlib import Path

import yaml

CONFIG_FILENAME = "config.yaml"
MEMORIES_FILENAME = "memories.jsonl"
AGMEM_DIRNAME = ".agmem"

CLAUDE_MD_START = "<!-- agmem:start -->"
CLAUDE_MD_END = "<!-- agmem:end -->"

GIT_HOOK_MARKER = "# agmem:post-commit"  # legacy marker, retained for back-compat detection
GIT_HOOK_BLOCK_START = "# agmem:block-start"
GIT_HOOK_BLOCK_END = "# agmem:block-end"


def _hook_inner(hook_name: str, since_arg: str, gate: str | None = None) -> str:
    """Build the marker-delimited inner block for a given hook."""
    body = (
        f"if command -v agmem >/dev/null 2>&1; then\n"
        f"    agmem update --since {since_arg} >/dev/null 2>&1 || true\n"
        f"    agmem hot --refresh >/dev/null 2>&1 || true\n"
        f"fi"
    )
    if gate:
        # Keep block markers at top-level so re-install can replace cleanly,
        # gate the agmem invocation inside.
        body = f"if {gate}; then\n    {body.replace(chr(10), chr(10) + '    ')}\nfi"
    return (
        f"{GIT_HOOK_BLOCK_START}\n"
        f"# agmem:{hook_name}\n"
        f"# Auto-update agmem memory. Safe to remove (delete file or this block).\n"
        f"{body}\n"
        f"{GIT_HOOK_BLOCK_END}"
    )


# Hook recipes: name → (since-ref, optional shell gate).
# - post-commit: local commits → diff against parent.
# - post-merge: `git pull` (and other merges) → diff against pre-merge tip.
# - post-rewrite: rebase (incl. `git pull --rebase`); skip on `amend` (post-commit covers that).
GIT_HOOK_RECIPES: dict[str, tuple[str, str | None]] = {
    "post-commit": ("HEAD~1", None),
    "post-merge": ("ORIG_HEAD", None),
    "post-rewrite": ("ORIG_HEAD", '[ "$1" = "rebase" ]'),
}


def _hook_body(hook_name: str) -> str:
    since, gate = GIT_HOOK_RECIPES[hook_name]
    return f"#!/bin/sh\n{_hook_inner(hook_name, since, gate)}\n"


# Back-compat: previously a single post-commit body. Some tests/imports still reference these.
GIT_HOOK_BODY_INNER = _hook_inner("post-commit", "HEAD~1")
GIT_HOOK_BODY = _hook_body("post-commit")

CLAUDE_MD_BLOCK = f"""{CLAUDE_MD_START}
## agmem — project memory

Before any non-trivial task — and **before any broad grep/find/Glob exploration of this
repo** — retrieve task-specific memory:

```bash
agmem context "<task, phrased for BM25>" -n 8 --session
```

agmem is your first lookup. Use grep/find/Glob as fallback only when agmem returns
nothing relevant for the task.

### Phrase the query for BM25

agmem uses BM25 — short identifier-like tokens rank better than full sentences. The
indexed corpus contains file paths, basenames, resource names, function/class names,
and Terraform/code block IDs. The closer your query is to that vocabulary, the better
the result. Before calling, rewrite the query:

- Drop articles, prepositions, wh-words (`the`, `for`, `in`, `how`, `where`).
- Use noun-phrase fragments, 3-7 tokens.
- Prefer **identifiers**: file basenames (`waf-alb-external`), resource names
  (`rds_proxy`, `my-truv`), module dirs, ticket IDs (`INF-3728`). Keep snake_case
  / kebab-case as they appear in code.
- If you can guess a likely filename or module, include the basename token.

Examples:

  ✗ `agmem context "where are the Grafana Slack contact points and templates"`
  ✓ `agmem context "grafana-contact-points slack notification templates"`

  ✗ `agmem context "Enable WAF in monitoring mode for mytruv public ALB"`
  ✓ `agmem context "waf-alb-external mytruv istio-gateway-external count"`

  ✗ `agmem context "how are secrets loaded and the config refresh endpoint"`
  ✓ `agmem context "secrets config refresh endpoint"`

If the first call returns weak results, **refine the tokens — don't retype the
natural-language form.** Try different basenames, snake_case variants, or the
identifiers you noticed in the top-K text.

### Treating the output

Treat the output's **Constraints** section as project rules — do not contradict them
without explicit user override. Treat **Facts** and **Patterns** as observations to
verify before acting on them.

### Saving memories

To save a new memory after learning something durable about this repo:

```bash
agmem remember "<the fact>" --kind fact --tag <topic>
agmem remember "<the rule>" --kind rule --tag <topic>
```
{CLAUDE_MD_END}"""

DEFAULT_CONFIG: dict[str, str | int] = {
    "version": 1,
    "project": "",
}


def _resolve_cwd(cwd: str | None = None) -> Path:
    return Path(cwd or os.getcwd()).resolve()


def find_repo_root(cwd: str | None = None) -> Path:
    """Return the repository root, resolving git worktrees to the main worktree.

    A "git worktree" (created via ``git worktree add``) has a ``.git`` FILE,
    not a directory, containing ``gitdir: <path>/.git/worktrees/<name>``. All
    worktrees of the same repo should share one ``.agmem/`` — the indexed
    memory is about the codebase, not the branch — so we resolve back to
    the main worktree.

    Submodules also have a ``.git`` file (``gitdir: <super>/.git/modules/...``)
    but those are conceptually separate repos and should have their own
    ``.agmem/``; we leave the submodule path as-is.
    """
    path = _resolve_cwd(cwd)
    for parent in [path, *path.parents]:
        git_marker = parent / ".git"
        if not git_marker.exists():
            continue
        if git_marker.is_dir():
            return parent
        if git_marker.is_file():
            main = _resolve_worktree_main_repo(git_marker)
            if main is not None:
                return main
            # Submodule or malformed — treat the marker dir as its own repo.
            return parent
    return path


def _resolve_worktree_main_repo(git_file: Path) -> Path | None:
    """Parse a worktree ``.git`` file and return the main repo's working tree.

    Returns ``None`` if the file points at anything other than a worktree
    (notably submodules, whose gitdir is under ``<super>/.git/modules/``).
    """
    try:
        content = git_file.read_text()
    except OSError:
        return None
    gitdir_str: str | None = None
    for line in content.splitlines():
        if line.startswith("gitdir:"):
            gitdir_str = line.split(":", 1)[1].strip()
            break
    if not gitdir_str:
        return None
    gitdir = Path(gitdir_str)
    if not gitdir.is_absolute():
        gitdir = (git_file.parent / gitdir).resolve()
    parts = gitdir.parts
    # Worktree pattern: <main-repo>/.git/worktrees/<name>
    try:
        idx = parts.index("worktrees")
    except ValueError:
        return None  # Not a worktree (likely submodule under .git/modules/).
    if idx == 0 or parts[idx - 1] != ".git":
        return None
    return Path(*parts[: idx - 1])


def agmem_dir(cwd: str | None = None) -> Path:
    return find_repo_root(cwd) / AGMEM_DIRNAME


def config_path(cwd: str | None = None) -> Path:
    return agmem_dir(cwd) / CONFIG_FILENAME


def memories_path(cwd: str | None = None) -> Path:
    return agmem_dir(cwd) / MEMORIES_FILENAME


def ensure_agmem_dir(cwd: str | None = None) -> Path:
    path = agmem_dir(cwd)
    path.mkdir(exist_ok=True)
    return path


def read_config(cwd: str | None = None) -> dict[str, str | int]:
    cfg_file = config_path(cwd)
    if cfg_file.exists():
        with open(cfg_file) as f:
            loaded = yaml.safe_load(f)
            return loaded or {}
    return {}


def write_config(data: dict, cwd: str | None = None) -> None:
    ensure_agmem_dir(cwd)
    with open(config_path(cwd), "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)


def init_config(project_name: str | None = None) -> dict[str, str | int]:
    cfg: dict[str, str | int] = dict(DEFAULT_CONFIG)
    if project_name:
        cfg["project"] = project_name
    write_config(cfg)
    return cfg


def emit_claude_md(cwd: str | None = None) -> tuple[Path, str]:
    """Write or update the agmem block in CLAUDE.md at the repo root.

    Returns (path, action) where action is one of: 'created', 'updated', 'unchanged'.
    Idempotent: replaces existing block delimited by markers, otherwise appends.
    """
    repo_root = find_repo_root(cwd)
    path = repo_root / "CLAUDE.md"
    if not path.exists():
        path.write_text(CLAUDE_MD_BLOCK + "\n", encoding="utf-8")
        return path, "created"

    current = path.read_text(encoding="utf-8")
    start_idx = current.find(CLAUDE_MD_START)
    end_idx = current.find(CLAUDE_MD_END)
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        block_end = end_idx + len(CLAUDE_MD_END)
        new_text = current[:start_idx] + CLAUDE_MD_BLOCK + current[block_end:]
        if new_text == current:
            return path, "unchanged"
        path.write_text(new_text, encoding="utf-8")
        return path, "updated"

    separator = "" if current.endswith("\n") else "\n"
    path.write_text(current + separator + "\n" + CLAUDE_MD_BLOCK + "\n", encoding="utf-8")
    return path, "updated"


def install_claude_hook(cwd: str | None = None) -> tuple[Path, str]:
    """Install agmem UserPromptSubmit hook into `.claude/settings.json` at repo root.

    Returns (path, action) where action is one of: 'created', 'updated', 'unchanged'.
    Merges with existing hooks: skips if our matcher is already present.
    """
    repo_root = find_repo_root(cwd)
    settings_dir = repo_root / ".claude"
    settings_dir.mkdir(exist_ok=True)
    path = settings_dir / "settings.json"

    existed = path.exists()
    if existed:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}

    hooks_root = data.setdefault("hooks", {})
    user_prompt_hooks = hooks_root.setdefault("UserPromptSubmit", [])

    agmem_command = "agmem hook inject"
    for matcher in user_prompt_hooks:
        for h in matcher.get("hooks", []):
            if h.get("type") == "command" and h.get("command") == agmem_command:
                return path, "unchanged"

    user_prompt_hooks.append({
        "hooks": [
            {
                "type": "command",
                "command": agmem_command,
            }
        ]
    })

    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path, "updated" if existed else "created"


def _resolve_git_hooks_dir(repo_root: Path) -> Path | None:
    """Return the git hooks/ directory, or None if no .git found.

    Supports both regular .git directories and worktree-style gitdir-link files.
    """
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return None
    if git_dir.is_file():
        try:
            line = git_dir.read_text(encoding="utf-8").strip()
            if line.startswith("gitdir:"):
                git_dir = Path(line.split(":", 1)[1].strip())
        except OSError:
            return None
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    return hooks_dir


def _install_one_git_hook(hooks_dir: Path, hook_name: str) -> tuple[Path, str]:
    """Install a single hook, preserving any user content around our delimited block."""
    path = hooks_dir / hook_name
    inner = _hook_inner(hook_name, *GIT_HOOK_RECIPES[hook_name])
    full_body = _hook_body(hook_name)

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        start_idx = existing.find(GIT_HOOK_BLOCK_START)
        end_idx = existing.find(GIT_HOOK_BLOCK_END)
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            block_end = end_idx + len(GIT_HOOK_BLOCK_END)
            new_text = existing[:start_idx] + inner + existing[block_end:]
            if new_text == existing:
                return path, "unchanged"
            path.write_text(new_text, encoding="utf-8")
            path.chmod(0o755)
            return path, "updated"
        # Legacy single-hook (post-commit) body without block markers — leave alone.
        if hook_name == "post-commit" and GIT_HOOK_MARKER in existing:
            return path, "unchanged"
        merged = existing.rstrip() + "\n\n" + inner + "\n"
        path.write_text(merged, encoding="utf-8")
        path.chmod(0o755)
        return path, "updated"

    path.write_text(full_body, encoding="utf-8")
    path.chmod(0o755)
    return path, "created"


def install_git_hook(cwd: str | None = None) -> dict[str, tuple[Path, str]]:
    """Install/update agmem git hooks: post-commit, post-merge, post-rewrite.

    Returns ``{hook_name: (path, action)}`` where action is one of:
    ``created``, ``updated``, ``unchanged``. If the repo has no ``.git`` directory,
    returns ``{"_repo": (repo_root, "no-git")}``.

    - ``post-commit`` runs ``agmem update --since HEAD~1`` after each local commit.
    - ``post-merge`` runs ``agmem update --since ORIG_HEAD`` after ``git pull`` /
      other merges, so memory follows incoming changes from teammates.
    - ``post-rewrite`` runs the same diff after ``git pull --rebase`` (gated on
      ``$1 = rebase`` so amends don't double-fire alongside post-commit).
    """
    repo_root = find_repo_root(cwd)
    hooks_dir = _resolve_git_hooks_dir(repo_root)
    if hooks_dir is None:
        return {"_repo": (repo_root, "no-git")}

    return {
        name: _install_one_git_hook(hooks_dir, name)
        for name in GIT_HOOK_RECIPES
    }
