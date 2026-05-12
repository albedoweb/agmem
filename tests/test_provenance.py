"""Tests for B1 provenance fields and kind handling on MemoryEntry."""

import json
import tempfile
from pathlib import Path

from agmem.store import (
    DEFAULT_KIND,
    VALID_KINDS,
    MemoryEntry,
    append_entry,
    create_entry,
    read_all_entries,
)


def test_create_entry_default_kind():
    entry = create_entry("hi")
    assert entry.kind == DEFAULT_KIND
    assert entry.source_lines is None
    assert entry.source_hash is None
    assert entry.source_commit is None


def test_create_entry_with_kind_and_provenance():
    entry = create_entry(
        "no destroy in prod",
        kind="rule",
        source_ref="docs/runbook.md",
        source_lines=[10, 25],
        source_hash="abcdef",
        source_commit="cafef00d",
    )
    assert entry.kind == "rule"
    assert entry.source_lines == [10, 25]
    assert entry.source_hash == "abcdef"
    assert entry.source_commit == "cafef00d"


def test_invalid_kind_falls_back_to_default():
    entry = create_entry("x", kind="bogus")
    assert entry.kind == DEFAULT_KIND


def test_round_trip_omits_default_kind_in_dict():
    entry = create_entry("plain fact")
    d = entry.to_dict()
    assert "kind" not in d  # default kind not serialized to keep JSONL clean
    restored = MemoryEntry.from_dict(d)
    assert restored.kind == DEFAULT_KIND


def test_round_trip_emits_non_default_kind():
    entry = create_entry("rule text", kind="rule")
    d = entry.to_dict()
    assert d["kind"] == "rule"
    restored = MemoryEntry.from_dict(d)
    assert restored.kind == "rule"


def test_round_trip_skips_unset_provenance():
    entry = create_entry("plain")
    d = entry.to_dict()
    for k in ("source_lines", "source_hash", "source_commit", "verified_at", "drifted_at", "deleted_at"):
        assert k not in d


def test_invalid_source_lines_normalized():
    raw = {
        "id": "01TEST",
        "ts": "2026-05-07T00:00:00+00:00",
        "text": "x",
        "tags": [],
        "source": "manual",
        "source_ref": "f.py",
        "source_lines": ["bad"],
    }
    entry = MemoryEntry.from_dict(raw)
    assert entry.source_lines is None


def test_soft_delete_filtered_by_default(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: tmpdir / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: tmpdir / ".agmem" / "memories.jsonl",
    )
    (tmpdir / ".agmem").mkdir(exist_ok=True)

    alive = create_entry("alive")
    dead = create_entry("dead")
    dead.deleted_at = "2026-05-07T00:00:00+00:00"
    append_entry(alive)
    append_entry(dead)

    visible = read_all_entries()
    assert {e.text for e in visible} == {"alive"}

    everything = read_all_entries(include_deleted=True)
    assert {e.text for e in everything} == {"alive", "dead"}


def test_jsonl_includes_provenance_fields(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: tmpdir / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: tmpdir / ".agmem" / "memories.jsonl",
    )
    (tmpdir / ".agmem").mkdir(exist_ok=True)

    entry = create_entry(
        "fact with provenance",
        kind="fact",
        source_ref="src/api.py",
        source_lines=[1, 30],
        source_hash="deadbeef",
        source_commit="cafef00d" * 5,
    )
    append_entry(entry)

    raw = (tmpdir / ".agmem" / "memories.jsonl").read_text()
    parsed = json.loads([line for line in raw.splitlines() if line.strip()][0])
    assert parsed["source_lines"] == [1, 30]
    assert parsed["source_hash"] == "deadbeef"
    assert parsed["source_commit"].startswith("cafef00d")


def test_valid_kinds_constant():
    assert "rule" in VALID_KINDS
    assert "fact" in VALID_KINDS
    assert "pattern" in VALID_KINDS
