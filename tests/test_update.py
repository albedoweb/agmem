"""Tests for `agmem update --since` (C3)."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from agmem.indexer import run_index, run_update
from agmem.store import read_all_entries, stable_id


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin",
        },
    )


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _git(root, "init", "-q")

    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    monkeypatch.setattr("agmem.indexer.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.indexer.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    monkeypatch.setattr(
        "agmem.indexer.config.ensure_agmem_dir",
        lambda cwd=None: (root / ".agmem").mkdir(exist_ok=True) or (root / ".agmem"),
    )
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _write(root: Path, path: str, content: str) -> None:
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_update_no_changes(repo: Path):
    _write(repo, "main.tf", 'resource "aws_s3_bucket" "x" {}')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    run_index(cwd=str(repo))
    result = run_update(since_ref="HEAD", cwd=str(repo))
    assert result["modified"] == 0
    assert result["added"] == 0
    assert result["deleted"] == 0


def test_update_modified_file_refreshes_entry(repo: Path):
    _write(repo, "main.tf", 'resource "aws_s3_bucket" "x" {}')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    run_index(cwd=str(repo))
    main_id = stable_id("index", "main.tf")
    before = next(e for e in read_all_entries(cwd=str(repo)) if e.id == main_id)

    _write(repo, "main.tf", 'resource "aws_s3_bucket" "x" {}\nresource "aws_s3_bucket" "y" {}')
    result = run_update(since_ref="HEAD", cwd=str(repo))
    assert result["modified"] == 1
    assert result["upserted"] >= 1

    after = next(e for e in read_all_entries(cwd=str(repo)) if e.id == main_id)
    assert after.source_hash != before.source_hash
    assert after.id == before.id
    assert "aws_s3_bucket" in after.text


def test_update_deleted_file_removes_entry(repo: Path):
    _write(repo, "main.tf", 'resource "aws_s3_bucket" "x" {}')
    _write(repo, "lambda.tf", 'resource "aws_lambda_function" "y" {}')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    run_index(cwd=str(repo))
    lambda_id = stable_id("index", "lambda.tf")
    assert any(e.id == lambda_id for e in read_all_entries(cwd=str(repo)))

    (repo / "lambda.tf").unlink()
    result = run_update(since_ref="HEAD", cwd=str(repo))
    assert result["deleted"] == 1
    assert result["removed"] >= 1
    assert not any(e.id == lambda_id for e in read_all_entries(cwd=str(repo)))


def test_update_added_file_appears(repo: Path):
    _write(repo, "main.tf", 'resource "aws_s3_bucket" "x" {}')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    run_index(cwd=str(repo))
    new_id = stable_id("index", "vpc.tf")
    assert not any(e.id == new_id for e in read_all_entries(cwd=str(repo)))

    _write(repo, "vpc.tf", 'resource "aws_vpc" "main" {}')
    _git(repo, "add", "-A")
    result = run_update(since_ref="HEAD", cwd=str(repo))
    assert result["added"] == 1
    assert result["upserted"] >= 1
    assert any(e.id == new_id for e in read_all_entries(cwd=str(repo)))


def test_update_preserves_unrelated_entries(repo: Path):
    _write(repo, "a/main.tf", 'resource "aws_s3_bucket" "x" {}')
    _write(repo, "b/main.tf", 'resource "aws_lambda_function" "y" {}')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    run_index(cwd=str(repo))
    b_id = stable_id("index", "b/main.tf")
    before = next(e for e in read_all_entries(cwd=str(repo)) if e.id == b_id)

    _write(repo, "a/main.tf", 'resource "aws_s3_bucket" "x" {}\n# changed')
    run_update(since_ref="HEAD", cwd=str(repo))
    after = next(e for e in read_all_entries(cwd=str(repo)) if e.id == b_id)
    # b/main.tf wasn't in the diff → entry unchanged including ts.
    assert after.ts == before.ts
    assert after.source_hash == before.source_hash
