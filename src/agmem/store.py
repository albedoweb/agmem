"""JSONL memory store: read, atomic append, ULID generation."""

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone

from ulid import ULID

from . import config

VALID_KINDS: tuple[str, ...] = ("rule", "fact", "pattern")
DEFAULT_KIND = "fact"


def stable_id(source: str, source_ref: str) -> str:
    """Return a deterministic ULID derived from (source, source_ref).

    Used by the indexer so reindex produces the same id for the same path,
    preventing duplicate explosion and preserving verified_at/drifted_at.
    """
    digest = hashlib.sha256(f"{source}::{source_ref}".encode("utf-8")).digest()
    return str(ULID.from_bytes(digest[:16]))


class MemoryEntry:
    def __init__(
        self,
        id: str,
        ts: str,
        text: str,
        tags: list[str] | None = None,
        source: str = "manual",
        source_ref: str | None = None,
        source_lines: list[int] | None = None,
        source_hash: str | None = None,
        source_commit: str | None = None,
        verified_at: str | None = None,
        drifted_at: str | None = None,
        deleted_at: str | None = None,
        kind: str = DEFAULT_KIND,
    ):
        self.id = id
        self.ts = ts
        self.text = text
        self.tags = tags or []
        self.source = source
        self.source_ref = source_ref
        self.source_lines = source_lines
        self.source_hash = source_hash
        self.source_commit = source_commit
        self.verified_at = verified_at
        self.drifted_at = drifted_at
        self.deleted_at = deleted_at
        self.kind = kind if kind in VALID_KINDS else DEFAULT_KIND

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEntry":
        lines = d.get("source_lines")
        if lines is not None and not (
            isinstance(lines, list) and len(lines) == 2 and all(isinstance(n, int) for n in lines)
        ):
            lines = None
        return cls(
            id=d["id"],
            ts=d["ts"],
            text=d["text"],
            tags=d.get("tags", []),
            source=d.get("source", "manual"),
            source_ref=d.get("source_ref"),
            source_lines=lines,
            source_hash=d.get("source_hash"),
            source_commit=d.get("source_commit"),
            verified_at=d.get("verified_at"),
            drifted_at=d.get("drifted_at"),
            deleted_at=d.get("deleted_at"),
            kind=d.get("kind", DEFAULT_KIND),
        )

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "ts": self.ts,
            "text": self.text,
            "tags": self.tags,
            "source": self.source,
            "source_ref": self.source_ref,
        }
        # Only emit optional provenance fields when set, to keep JSONL clean
        for key in (
            "source_lines",
            "source_hash",
            "source_commit",
            "verified_at",
            "drifted_at",
            "deleted_at",
        ):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        if self.kind != DEFAULT_KIND:
            d["kind"] = self.kind
        return d

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return f"MemoryEntry(id={self.id}, kind={self.kind}, text={self.text[:60]}...)"


def create_entry(
    text: str,
    tags: list[str] | None = None,
    source: str = "manual",
    source_ref: str | None = None,
    source_lines: list[int] | None = None,
    source_hash: str | None = None,
    source_commit: str | None = None,
    kind: str = DEFAULT_KIND,
) -> MemoryEntry:
    return MemoryEntry(
        id=str(ULID()),
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        text=text,
        tags=tags or [],
        source=source,
        source_ref=source_ref,
        source_lines=source_lines,
        source_hash=source_hash,
        source_commit=source_commit,
        kind=kind,
    )


def read_all_entries(cwd: str | None = None, include_deleted: bool = False) -> list[MemoryEntry]:
    path = config.memories_path(cwd)
    if not path.exists():
        return []
    entries = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entry = MemoryEntry.from_dict(d)
            except (json.JSONDecodeError, KeyError):
                continue
            if entry.is_deleted and not include_deleted:
                continue
            entries.append(entry)
    return entries


def append_entry(entry: MemoryEntry, cwd: str | None = None) -> None:
    config.ensure_agmem_dir(cwd)
    path = config.memories_path(cwd)
    line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def find_entries_by_id_prefix(prefix: str, cwd: str | None = None) -> list[MemoryEntry]:
    """Return all entries whose id starts with the given prefix (incl. soft-deleted)."""
    if not prefix:
        return []
    return [e for e in read_all_entries(cwd, include_deleted=True) if e.id.startswith(prefix)]


def rewrite_entries(entries: list[MemoryEntry], cwd: str | None = None) -> None:
    """Atomically rewrite the JSONL store with the given entries (preserving order)."""
    config.ensure_agmem_dir(cwd)
    path = config.memories_path(cwd)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            for entry in entries:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, path)
