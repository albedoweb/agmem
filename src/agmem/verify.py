"""Verification: re-hash referenced files, mark entries as verified or drifted.

`run_verify` supports an optional `follow_renames=True` mode that uses
`git log --follow --diff-filter=R` to detect when a referenced file moved.
If git proposes a new path AND the new file's content hash matches the
recorded `source_hash`, we auto-update `source_ref` (content is bit-identical,
so there's nothing to compare). If the new path's hash differs (file renamed
AND modified), we mark the entry drifted and surface the rename candidate as
a hint — never auto-rewriting on hash mismatch.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .store import MemoryEntry, read_all_entries, rewrite_entries


@dataclass
class RenamedEntry:
    entry: MemoryEntry
    old_ref: str
    new_ref: str


@dataclass
class RenameHint:
    entry: MemoryEntry
    candidate_ref: str


class VerifyResult:
    __slots__ = ("verified", "drifted", "missing", "skipped", "renamed", "rename_hints")

    def __init__(self) -> None:
        self.verified: list[MemoryEntry] = []
        self.drifted: list[MemoryEntry] = []
        self.missing: list[MemoryEntry] = []
        self.skipped: list[MemoryEntry] = []
        self.renamed: list[RenamedEntry] = []
        self.rename_hints: list[RenameHint] = []

    @property
    def counts(self) -> dict[str, int]:
        return {
            "verified": len(self.verified),
            "drifted": len(self.drifted),
            "missing": len(self.missing),
            "skipped": len(self.skipped),
            "renamed": len(self.renamed),
            "rename_hints": len(self.rename_hints),
        }


def _file_sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _is_directory_entry(entry: MemoryEntry) -> bool:
    return "directory" in entry.tags


def _build_rename_chain(root: Path) -> list[tuple[str, str]]:
    """Return all rename events in this repo's history, chronologically ordered.

    `git log --follow -- <old>` doesn't work when the old path no longer exists
    (which is exactly our case). Instead we collect every rename in the repo
    history once, then resolve any old_path → current_path lookup against the chain.
    """
    try:
        result = subprocess.run(
            [
                "git", "log", "--all", "--reverse",
                "--diff-filter=R", "--name-status", "--pretty=format:",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    chain: list[tuple[str, str]] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line.startswith("R"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            chain.append((parts[1], parts[2]))
    return chain


def _resolve_renamed_path(chain: list[tuple[str, str]], old_path: str) -> str | None:
    """Walk a chronologically-ordered rename chain forward from `old_path`."""
    current = old_path
    for src, dst in chain:
        if src == current:
            current = dst
    return current if current != old_path else None


def _find_renamed_path(root: Path, old_path: str) -> str | None:
    """One-shot helper used by tests; for batch use call `_build_rename_chain` once."""
    return _resolve_renamed_path(_build_rename_chain(root), old_path)


def run_verify(
    *,
    id_prefix: str | None = None,
    cwd: str | None = None,
    follow_renames: bool = False,
) -> VerifyResult:
    """Verify entries against on-disk file hashes; mutate verified_at/drifted_at and persist.

    Skips entries without source_hash (e.g., directory summaries) and entries whose
    source_ref doesn't resolve to a regular file (unless follow_renames recovers them).
    """
    all_entries = read_all_entries(cwd, include_deleted=True)
    repo_root = config.find_repo_root(cwd)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = VerifyResult()
    rename_chain = _build_rename_chain(repo_root) if follow_renames else []

    targets: set[str] = set()
    if id_prefix:
        targets = {e.id for e in all_entries if e.id.startswith(id_prefix)}

    for entry in all_entries:
        if id_prefix and entry.id not in targets:
            continue
        if entry.is_deleted:
            result.skipped.append(entry)
            continue
        if not entry.source_hash or not entry.source_ref:
            result.skipped.append(entry)
            continue
        if _is_directory_entry(entry):
            result.skipped.append(entry)
            continue

        candidate = repo_root / entry.source_ref
        if not candidate.is_file():
            handled = False
            if follow_renames:
                new_path = _resolve_renamed_path(rename_chain, entry.source_ref)
                if new_path:
                    new_candidate = repo_root / new_path
                    if new_candidate.is_file():
                        new_hash = _file_sha256(new_candidate)
                        if new_hash is not None and new_hash == entry.source_hash:
                            # Bit-identical content moved to a new path → safe to update.
                            old_ref = entry.source_ref
                            entry.source_ref = new_path
                            entry.verified_at = now
                            entry.drifted_at = None
                            result.renamed.append(RenamedEntry(
                                entry=entry,
                                old_ref=old_ref,
                                new_ref=new_path,
                            ))
                            handled = True
                        else:
                            # File was renamed AND modified → drift with hint.
                            entry.drifted_at = now
                            entry.verified_at = None
                            result.drifted.append(entry)
                            result.rename_hints.append(RenameHint(
                                entry=entry,
                                candidate_ref=new_path,
                            ))
                            handled = True
            if not handled:
                entry.drifted_at = now
                entry.verified_at = None
                result.missing.append(entry)
            continue

        current = _file_sha256(candidate)
        if current is None:
            result.skipped.append(entry)
            continue
        if current == entry.source_hash:
            entry.verified_at = now
            entry.drifted_at = None
            result.verified.append(entry)
        else:
            entry.drifted_at = now
            entry.verified_at = None
            result.drifted.append(entry)

    rewrite_entries(all_entries, cwd)
    return result
