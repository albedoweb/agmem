"""Tests for B4 (forget soft-delete) and B2 (verify) commands."""

import tempfile
from pathlib import Path

from agmem.store import (
    append_entry,
    create_entry,
    find_entries_by_id_prefix,
    read_all_entries,
    rewrite_entries,
)
from agmem.verify import run_verify


def _patch_store(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    (root / ".agmem").mkdir(exist_ok=True)


def _patch_verify(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.verify.config.find_repo_root", lambda cwd=None: root)
    _patch_store(monkeypatch, root)


def test_find_entries_by_id_prefix(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_store(monkeypatch, root)

    e1 = create_entry("first")
    e2 = create_entry("second")
    append_entry(e1)
    append_entry(e2)

    # Use full id since ULIDs in the same millisecond share their time-encoded prefix.
    matches = find_entries_by_id_prefix(e1.id)
    assert len(matches) == 1
    assert matches[0].id == e1.id

    # Ambiguous prefix returns multiple matches when ids overlap by time component.
    shared_prefix = e1.id[:10]
    if all(e.id.startswith(shared_prefix) for e in [e1, e2]):
        ambiguous = find_entries_by_id_prefix(shared_prefix)
        assert len(ambiguous) == 2


def test_rewrite_entries_atomic(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_store(monkeypatch, root)

    e1 = create_entry("a")
    e2 = create_entry("b")
    append_entry(e1)
    append_entry(e2)

    e1.deleted_at = "2026-05-07T00:00:00+00:00"
    rewrite_entries([e1, e2])

    visible = read_all_entries()
    everything = read_all_entries(include_deleted=True)
    assert {e.text for e in visible} == {"b"}
    assert len(everything) == 2


def test_verify_marks_unchanged_file_as_verified(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_verify(monkeypatch, root)

    target = root / "config.tf"
    target.write_text('resource "aws_s3_bucket" "x" {}')
    import hashlib
    sha = hashlib.sha256(target.read_bytes()).hexdigest()

    entry = create_entry(
        "x", source="index", source_ref="config.tf", source_hash=sha,
    )
    append_entry(entry)

    result = run_verify(id_prefix=entry.id)
    assert len(result.verified) == 1
    refreshed = read_all_entries()[0]
    assert refreshed.verified_at is not None
    assert refreshed.drifted_at is None


def test_verify_marks_changed_file_as_drifted(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_verify(monkeypatch, root)

    target = root / "main.tf"
    target.write_text("original content")
    entry = create_entry(
        "x", source="index", source_ref="main.tf",
        source_hash="0" * 64,  # intentionally wrong hash
    )
    append_entry(entry)

    result = run_verify(id_prefix=entry.id)
    assert len(result.drifted) == 1
    refreshed = read_all_entries()[0]
    assert refreshed.drifted_at is not None
    assert refreshed.verified_at is None


def test_verify_handles_missing_file(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_verify(monkeypatch, root)

    entry = create_entry(
        "x", source="index", source_ref="gone.tf", source_hash="abc",
    )
    append_entry(entry)
    result = run_verify(id_prefix=entry.id)
    assert len(result.missing) == 1
    refreshed = read_all_entries()[0]
    assert refreshed.drifted_at is not None


def test_verify_skips_entries_without_hash(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_verify(monkeypatch, root)

    entry = create_entry("manual rule", source="manual")
    append_entry(entry)
    result = run_verify(id_prefix=entry.id)
    assert len(result.skipped) == 1
    refreshed = read_all_entries()[0]
    assert refreshed.verified_at is None
    assert refreshed.drifted_at is None


def test_verify_skips_directory_entries(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_verify(monkeypatch, root)

    entry = create_entry(
        "Directory `src` ...",
        tags=["index", "directory"],
        source="index",
        source_ref="src",
        source_hash="abc",
    )
    append_entry(entry)
    result = run_verify(id_prefix=entry.id)
    assert len(result.skipped) == 1
