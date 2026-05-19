"""Tests for ``config.find_repo_root`` — especially the git-worktree
resolution behavior introduced for shared-memory worktrees.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agmem.config import find_repo_root


def _make_main_repo(tmp_path: Path) -> Path:
    """Make a plain non-bare repo at ``tmp_path/main`` with one commit."""
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], cwd=main, check=True)
    return main


def test_finds_plain_git_dir(tmp_path):
    main = _make_main_repo(tmp_path)
    # Subdir of the main repo → walk up finds main/.git (directory).
    sub = main / "src"
    sub.mkdir()
    assert find_repo_root(str(sub)) == main


def test_returns_cwd_when_no_git(tmp_path):
    # Nothing has .git → returns the cwd itself (fallback).
    plain = tmp_path / "no_git"
    plain.mkdir()
    assert find_repo_root(str(plain)) == plain.resolve()


def test_resolves_git_worktree_to_main(tmp_path):
    """A worktree's ``.git`` is a file; ``find_repo_root`` should jump back
    to the main worktree so agmem reads the same ``.agmem/`` from both."""
    main = _make_main_repo(tmp_path)
    # Create a worktree under main/.claude/worktrees/feat
    wt_path = main / ".claude" / "worktrees" / "feat"
    wt_path.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-q", str(wt_path), "-b", "feat"],
        cwd=main, check=True,
    )

    # Sanity: .git inside the worktree is a FILE, not a directory.
    assert (wt_path / ".git").is_file()

    # From the worktree root: should resolve to main.
    assert find_repo_root(str(wt_path)) == main

    # From a subdir inside the worktree: same result.
    inner = wt_path / "src"
    inner.mkdir()
    assert find_repo_root(str(inner)) == main


def test_worktree_resolution_preserves_subdir_root_when_main_unrecognized(tmp_path):
    """Malformed ``.git`` file → fall back to using the marker dir as root."""
    fake_wt = tmp_path / "broken_wt"
    fake_wt.mkdir()
    (fake_wt / ".git").write_text("gitdir: /nonexistent/path/without/worktrees/marker\n")
    # No "worktrees" in the gitdir path → treated as submodule-like; root = fake_wt itself.
    assert find_repo_root(str(fake_wt)) == fake_wt


def test_submodule_keeps_own_root(tmp_path):
    """A ``.git`` file pointing under ``.git/modules/`` is a submodule.
    Submodules are separate projects → keep the submodule path as root."""
    super_repo = _make_main_repo(tmp_path)
    submod = super_repo / "vendor" / "sub"
    submod.mkdir(parents=True)
    # Fake submodule marker — git's own format.
    (submod / ".git").write_text(
        f"gitdir: {super_repo}/.git/modules/vendor/sub\n"
    )
    # Pretend the modules dir exists so the path looks plausible.
    (super_repo / ".git" / "modules" / "vendor" / "sub").mkdir(parents=True, exist_ok=True)

    # Submodule should keep its OWN root, not jump to super_repo.
    assert find_repo_root(str(submod)) == submod


def test_resolves_relative_gitdir_paths(tmp_path):
    """Some tooling writes relative ``gitdir:`` entries; resolve them."""
    main = _make_main_repo(tmp_path)
    wt = main / ".claude" / "worktrees" / "rel"
    wt.parent.mkdir(parents=True)
    wt.mkdir()
    # Relative path that resolves correctly back to main repo's gitdir.
    rel = os.path.relpath(main / ".git" / "worktrees" / "rel", start=wt)
    (wt / ".git").write_text(f"gitdir: {rel}\n")
    # The actual gitdir path doesn't need to exist for this test — we
    # only care that the parser handles relative paths.
    assert find_repo_root(str(wt)) == main
