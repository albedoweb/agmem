"""Tests for store module: create, append, read, atomic write."""

import json
import tempfile
from pathlib import Path

from agmem.store import (
    MemoryEntry,
    append_entry,
    create_entry,
    read_all_entries,
)


def test_create_entry():
    entry = create_entry("test memory")
    assert entry.text == "test memory"
    assert entry.id
    assert entry.ts
    assert entry.source == "manual"
    assert entry.source_ref is None
    assert entry.tags == []


def test_create_entry_with_tags():
    entry = create_entry("test", tags=["foo", "bar"], source="cli", source_ref="file:line")
    assert entry.tags == ["foo", "bar"]
    assert entry.source == "cli"
    assert entry.source_ref == "file:line"


def test_entry_round_trip():
    entry = create_entry("round trip test", tags=["a"], source="manual")
    d = entry.to_dict()
    restored = MemoryEntry.from_dict(d)
    assert restored.id == entry.id
    assert restored.ts == entry.ts
    assert restored.text == entry.text
    assert restored.tags == entry.tags


def test_append_and_read(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr(
        "agmem.store.config.agmem_dir", lambda cwd=None: tmpdir / ".agmem"
    )
    monkeypatch.setattr(
        "agmem.store.config.memories_path", lambda cwd=None: tmpdir / ".agmem" / "memories.jsonl"
    )

    (tmpdir / ".agmem").mkdir(exist_ok=True)

    e1 = create_entry("first memory", tags=["t1"])
    e2 = create_entry("second memory", tags=["t2"])
    e3 = create_entry("third memory", tags=["t1", "t2"])

    append_entry(e1)
    append_entry(e2)
    append_entry(e3)

    entries = read_all_entries()
    assert len(entries) == 3
    assert entries[0].text == "first memory"
    assert entries[1].text == "second memory"
    assert entries[2].text == "third memory"


def test_read_nonexistent(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr(
        "agmem.store.config.memories_path", lambda cwd=None: tmpdir / "nonexistent.jsonl"
    )
    entries = read_all_entries()
    assert entries == []


def test_jsonl_format(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr(
        "agmem.store.config.agmem_dir", lambda cwd=None: tmpdir / ".agmem"
    )
    monkeypatch.setattr(
        "agmem.store.config.memories_path", lambda cwd=None: tmpdir / ".agmem" / "memories.jsonl"
    )

    (tmpdir / ".agmem").mkdir(exist_ok=True)

    entry: MemoryEntry = create_entry("format test", tags=["a"], source="manual", source_ref="ref:123")
    append_entry(entry)

    path = tmpdir / ".agmem" / "memories.jsonl"
    with open(path) as f:
        lines = [l for l in f if l.strip()]

    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["text"] == "format test"
    assert data["tags"] == ["a"]
    assert data["source"] == "manual"
    assert data["source_ref"] == "ref:123"
    assert "id" in data
    assert "ts" in data


def test_tag_filtering(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr(
        "agmem.store.config.agmem_dir", lambda cwd=None: tmpdir / ".agmem"
    )
    monkeypatch.setattr(
        "agmem.store.config.memories_path", lambda cwd=None: tmpdir / ".agmem" / "memories.jsonl"
    )

    (tmpdir / ".agmem").mkdir(exist_ok=True)

    append_entry(create_entry("a", tags=["billing"]))
    append_entry(create_entry("b", tags=["auth"]))
    append_entry(create_entry("c", tags=["billing", "constraint"]))

    entries = read_all_entries()
    billing_entries = [e for e in entries if "billing" in [t.lower() for t in e.tags]]
    assert len(billing_entries) == 2
