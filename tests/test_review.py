"""Tests for `agmem review` (B3)."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agmem.review import run_review
from agmem.store import append_entry, create_entry


def _patch(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.review.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    (root / ".agmem").mkdir(exist_ok=True)


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch(monkeypatch, root)
    return root


def test_review_empty(repo: Path):
    report = run_review()
    assert report.total_live == 0
    assert report.drifted == []
    assert report.missing_source == []
    assert report.stale == []
    assert report.duplicates == []


def test_review_flags_drifted(repo: Path):
    e = create_entry("x", source="index", source_ref="main.tf", source_hash="abc")
    e.drifted_at = "2026-05-07T00:00:00+00:00"
    append_entry(e)
    report = run_review()
    assert len(report.drifted) == 1


def test_review_flags_missing_source(repo: Path):
    append_entry(create_entry(
        "x", source="index", source_ref="gone.tf", source_hash="abc",
    ))
    report = run_review()
    assert len(report.missing_source) == 1


def test_review_existing_source_not_flagged(repo: Path):
    (repo / "main.tf").write_text("ok")
    append_entry(create_entry(
        "x", source="index", source_ref="main.tf", source_hash="abc",
    ))
    report = run_review()
    assert len(report.missing_source) == 0


def test_review_flags_stale_manual(repo: Path, monkeypatch):
    e = create_entry("old fact", source="manual")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    e.ts = long_ago
    append_entry(e)
    report = run_review(stale_days=30)
    assert len(report.stale) == 1


def test_review_skips_stale_when_verified(repo: Path):
    e = create_entry("old fact", source="manual")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    e.ts = long_ago
    e.verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    append_entry(e)
    report = run_review(stale_days=30)
    assert len(report.stale) == 0


def test_review_finds_duplicates(repo: Path):
    append_entry(create_entry(
        "S3 buckets must always have encryption enabled and versioning on",
        source="manual",
    ))
    append_entry(create_entry(
        "S3 buckets always must have encryption enabled and versioning on",
        source="manual",
    ))
    report = run_review()
    assert len(report.duplicates) >= 1
    a, b, score = report.duplicates[0]
    assert score >= 0.8
