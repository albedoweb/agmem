"""Review: surface drifted, missing-source, duplicate, and stale entries.

Read-only — does not mutate state. Pair with `agmem verify` (sets drifted_at)
and `agmem forget` (soft-deletes) to keep the store clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .store import MemoryEntry, read_all_entries

STALE_DAYS_DEFAULT = 30
DUPLICATE_JACCARD_THRESHOLD = 0.8
_TOKEN_RE = re.compile(r"[\W_]+", re.UNICODE)


@dataclass
class ReviewReport:
    drifted: list[MemoryEntry] = field(default_factory=list)
    missing_source: list[MemoryEntry] = field(default_factory=list)
    stale: list[MemoryEntry] = field(default_factory=list)
    duplicates: list[tuple[MemoryEntry, MemoryEntry, float]] = field(default_factory=list)
    total_live: int = 0


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.split(text.lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_stale(entry: MemoryEntry, threshold: datetime) -> bool:
    if entry.verified_at is not None:
        return False
    if entry.source != "manual":
        return False
    try:
        ts = datetime.fromisoformat(entry.ts)
    except ValueError:
        return False
    return ts < threshold


def run_review(
    *,
    cwd: str | None = None,
    stale_days: int = STALE_DAYS_DEFAULT,
    duplicate_threshold: float = DUPLICATE_JACCARD_THRESHOLD,
    duplicate_scan_limit: int = 2000,
) -> ReviewReport:
    entries = read_all_entries(cwd)
    repo_root = config.find_repo_root(cwd)
    threshold = datetime.now(timezone.utc) - timedelta(days=stale_days)

    report = ReviewReport(total_live=len(entries))

    for entry in entries:
        if entry.drifted_at is not None:
            report.drifted.append(entry)
        if entry.source_ref and entry.source == "index" and entry.source_hash:
            candidate = repo_root / entry.source_ref
            if not candidate.exists():
                report.missing_source.append(entry)
        if _is_stale(entry, threshold):
            report.stale.append(entry)

    # Duplicate detection (manual entries only — index entries are deterministic by stable id).
    manual = [e for e in entries if e.source == "manual"][:duplicate_scan_limit]
    tokenized = [(e, _tokens(e.text)) for e in manual]
    for i, (a, ta) in enumerate(tokenized):
        for b, tb in tokenized[i + 1:]:
            score = _jaccard(ta, tb)
            if score >= duplicate_threshold:
                report.duplicates.append((a, b, score))

    return report
