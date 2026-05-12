"""Tests for `agmem verify --follow` git-aware rename detection (Direction 1)."""

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from agmem.store import append_entry, create_entry, read_all_entries
from agmem.verify import _find_renamed_path, run_verify


GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "PATH": "/usr/bin:/bin",
}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=GIT_ENV,
    )


def _patch(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.verify.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    (root / ".agmem").mkdir(exist_ok=True)


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _git(root, "init", "-q")
    _patch(monkeypatch, root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_find_renamed_path_simple_rename(repo: Path):
    content = 'resource "aws_s3_bucket" "x" {}'
    (repo / "old.tf").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    _git(repo, "mv", "old.tf", "new.tf")
    _git(repo, "commit", "-q", "-m", "rename")

    found = _find_renamed_path(repo, "old.tf")
    assert found == "new.tf"


def test_find_renamed_path_chain(repo: Path):
    content = "x" * 200  # large enough so git's rename similarity stays high
    (repo / "a.tf").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    _git(repo, "mv", "a.tf", "b.tf")
    _git(repo, "commit", "-q", "-m", "rename a→b")

    _git(repo, "mv", "b.tf", "c.tf")
    _git(repo, "commit", "-q", "-m", "rename b→c")

    assert _find_renamed_path(repo, "a.tf") == "c.tf"


def test_find_renamed_path_no_rename(repo: Path):
    (repo / "main.tf").write_text("hello")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    assert _find_renamed_path(repo, "main.tf") is None


def test_find_renamed_path_for_truly_deleted(repo: Path):
    (repo / "gone.tf").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "rm", "gone.tf")
    _git(repo, "commit", "-q", "-m", "delete")
    assert _find_renamed_path(repo, "gone.tf") is None


def test_verify_follow_auto_updates_on_pure_rename(repo: Path):
    content = 'resource "aws_s3_bucket" "x" {}\n# some lines\n' * 10
    (repo / "old.tf").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    entry = create_entry(
        "module info",
        source="index",
        source_ref="old.tf",
        source_hash=_sha256(content),
    )
    append_entry(entry)

    _git(repo, "mv", "old.tf", "new.tf")
    _git(repo, "commit", "-q", "-m", "rename")

    result = run_verify(follow_renames=True)
    assert len(result.renamed) == 1
    assert result.renamed[0].old_ref == "old.tf"
    assert result.renamed[0].new_ref == "new.tf"

    refreshed = next(e for e in read_all_entries() if e.id == entry.id)
    assert refreshed.source_ref == "new.tf"
    assert refreshed.verified_at is not None
    assert refreshed.drifted_at is None


def test_verify_without_follow_marks_missing(repo: Path):
    content = "x" * 500
    (repo / "old.tf").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    entry = create_entry(
        "module info",
        source="index",
        source_ref="old.tf",
        source_hash=_sha256(content),
    )
    append_entry(entry)

    _git(repo, "mv", "old.tf", "new.tf")
    _git(repo, "commit", "-q", "-m", "rename")

    result = run_verify(follow_renames=False)
    assert len(result.missing) == 1
    assert len(result.renamed) == 0
    refreshed = next(e for e in read_all_entries() if e.id == entry.id)
    assert refreshed.source_ref == "old.tf"
    assert refreshed.drifted_at is not None


def test_verify_follow_rename_with_content_change_yields_hint(repo: Path):
    original = "x" * 500
    (repo / "old.tf").write_text(original)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    entry = create_entry(
        "module info",
        source="index",
        source_ref="old.tf",
        source_hash=_sha256(original),
    )
    append_entry(entry)

    _git(repo, "mv", "old.tf", "new.tf")
    # Append new content so similarity stays >50% for git rename detection but hash differs.
    (repo / "new.tf").write_text(original + "\n# new line added\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rename + edit")

    result = run_verify(follow_renames=True)
    assert len(result.renamed) == 0
    assert len(result.rename_hints) == 1
    hint = result.rename_hints[0]
    assert hint.entry.id == entry.id
    assert hint.candidate_ref == "new.tf"

    refreshed = next(e for e in read_all_entries() if e.id == entry.id)
    # We did NOT auto-rewrite source_ref because hash differs.
    assert refreshed.source_ref == "old.tf"
    assert refreshed.drifted_at is not None
    assert refreshed.verified_at is None


def test_verify_follow_truly_deleted_falls_through_to_missing(repo: Path):
    content = "y" * 500
    (repo / "gone.tf").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    entry = create_entry(
        "x",
        source="index",
        source_ref="gone.tf",
        source_hash=_sha256(content),
    )
    append_entry(entry)

    _git(repo, "rm", "gone.tf")
    _git(repo, "commit", "-q", "-m", "delete")

    result = run_verify(follow_renames=True)
    assert len(result.missing) == 1
    assert len(result.renamed) == 0
    assert len(result.rename_hints) == 0
