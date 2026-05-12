"""Tests for A1+A2: emit_claude_md and install_claude_hook helpers."""

import json
import tempfile
from pathlib import Path

import agmem.config as config_mod
from agmem.config import (
    CLAUDE_MD_END,
    CLAUDE_MD_START,
    GIT_HOOK_MARKER,
    emit_claude_md,
    install_claude_hook,
    install_git_hook,
)


def _patch_repo(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(config_mod, "find_repo_root", lambda cwd=None: root)


def test_emit_claude_md_creates_new(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)

    path, action = emit_claude_md()
    assert action == "created"
    assert path == root / "CLAUDE.md"

    content = path.read_text()
    assert CLAUDE_MD_START in content
    assert CLAUDE_MD_END in content
    assert "agmem context" in content


def test_emit_claude_md_idempotent(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    emit_claude_md()
    _, action = emit_claude_md()
    assert action == "unchanged"


def test_emit_claude_md_appends_to_existing(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    pre_existing = "# Project\n\nSome user notes here.\n"
    (root / "CLAUDE.md").write_text(pre_existing)

    path, action = emit_claude_md()
    assert action == "updated"
    content = path.read_text()
    assert pre_existing.strip() in content
    assert CLAUDE_MD_START in content


def test_emit_claude_md_replaces_existing_block(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    stale_block = (
        "# Project\n\n"
        f"{CLAUDE_MD_START}\n"
        "OLD AGMEM INSTRUCTIONS\n"
        f"{CLAUDE_MD_END}\n\n"
        "Trailing user content.\n"
    )
    (root / "CLAUDE.md").write_text(stale_block)

    path, action = emit_claude_md()
    assert action == "updated"
    content = path.read_text()
    assert "OLD AGMEM INSTRUCTIONS" not in content
    assert "Trailing user content." in content
    assert content.count(CLAUDE_MD_START) == 1


def test_install_claude_hook_creates_settings(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)

    path, action = install_claude_hook()
    assert action == "created"
    assert path == root / ".claude" / "settings.json"

    data = json.loads(path.read_text())
    hooks = data["hooks"]["UserPromptSubmit"]
    assert any(
        h.get("command") == "agmem hook inject"
        for matcher in hooks
        for h in matcher.get("hooks", [])
    )


def test_install_claude_hook_merges_existing(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    settings_dir = root / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps({"theme": "dark", "hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "other-tool"}]}
        ]}})
    )

    path, action = install_claude_hook()
    assert action == "updated"
    data = json.loads(path.read_text())
    assert data["theme"] == "dark"
    commands = [
        h.get("command")
        for matcher in data["hooks"]["UserPromptSubmit"]
        for h in matcher.get("hooks", [])
    ]
    assert "other-tool" in commands
    assert "agmem hook inject" in commands


def test_install_claude_hook_idempotent(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    install_claude_hook()
    _, action = install_claude_hook()
    assert action == "unchanged"


def test_install_git_hook_no_git(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    results = install_git_hook()
    assert "_repo" in results
    assert results["_repo"][1] == "no-git"


def test_install_git_hook_creates_all_three(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()

    results = install_git_hook()
    assert set(results) == {"post-commit", "post-merge", "post-rewrite"}
    import stat
    for name, (path, action) in results.items():
        assert action == "created", f"{name}: {action}"
        assert path.exists()
        assert path.stat().st_mode & stat.S_IXUSR


def test_post_commit_hook_uses_head_parent(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()
    results = install_git_hook()
    body = results["post-commit"][0].read_text()
    assert "agmem update --since HEAD~1" in body
    assert GIT_HOOK_MARKER in body


def test_post_merge_hook_uses_orig_head(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()
    results = install_git_hook()
    body = results["post-merge"][0].read_text()
    assert "agmem update --since ORIG_HEAD" in body
    assert "# agmem:post-merge" in body
    # post-merge must NOT be gated — every merge should trigger reindex.
    assert 'if [ "$1"' not in body
    assert "if $1" not in body


def test_post_rewrite_hook_gates_on_rebase(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()
    results = install_git_hook()
    body = results["post-rewrite"][0].read_text()
    assert "agmem update --since ORIG_HEAD" in body
    assert "# agmem:post-rewrite" in body
    # Must skip on amend — that's covered by post-commit already.
    assert '[ "$1" = "rebase" ]' in body


def test_all_hooks_have_valid_sh_syntax(monkeypatch, tmp_path):
    """Catch regressions where the generated body wouldn't run as a /bin/sh script."""
    import subprocess
    root = tmp_path
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()
    results = install_git_hook()
    for name, (path, _) in results.items():
        result = subprocess.run(
            ["/bin/sh", "-n", str(path)], capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, (
            f"{name} has invalid shell syntax: {result.stderr}"
        )


def test_install_git_hook_idempotent(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()
    install_git_hook()
    results = install_git_hook()
    for name, (_, action) in results.items():
        assert action == "unchanged", f"{name}: {action}"


def test_install_git_hook_appends_to_existing_user_hook(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()
    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir()
    pre_existing = "#!/bin/sh\necho 'user pre-existing hook'\n"
    (hooks_dir / "post-merge").write_text(pre_existing)

    results = install_git_hook()
    assert results["post-merge"][1] == "updated"
    body = results["post-merge"][0].read_text()
    assert "user pre-existing hook" in body
    assert "# agmem:post-merge" in body
    assert "ORIG_HEAD" in body


def test_install_git_hook_replaces_outdated_block(monkeypatch):
    """When our block markers already exist with stale content, re-running install
    replaces only the delimited block while preserving user content around it."""
    from agmem.config import GIT_HOOK_BLOCK_END, GIT_HOOK_BLOCK_START

    root = Path(tempfile.mkdtemp())
    _patch_repo(monkeypatch, root)
    (root / ".git").mkdir()
    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir()

    user_pre = "#!/bin/sh\necho 'user pre-block'\n\n"
    user_post = "\n\necho 'user post-block'\n"
    old_inner = (
        f"{GIT_HOOK_BLOCK_START}\n"
        f"# old body without hot refresh\n"
        f"agmem update --since HEAD~1 >/dev/null 2>&1 || true\n"
        f"{GIT_HOOK_BLOCK_END}"
    )
    (hooks_dir / "post-commit").write_text(user_pre + old_inner + user_post)

    results = install_git_hook()
    assert results["post-commit"][1] == "updated"
    body = results["post-commit"][0].read_text()
    assert "user pre-block" in body
    assert "user post-block" in body
    assert "agmem hot --refresh" in body
    assert "old body without hot refresh" not in body
