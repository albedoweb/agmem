"""Machine-readable state probe for ``agmem stats``.

Single-shot snapshot of memory store + hot cache state. Designed for scripted
loops that need a structured before/after view — e.g., an autoresearch-style
loop that proposes a memory edit, runs sessions, then compares the resulting
state against a baseline. Keep this pure read-only and side-effect free.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .hot import hot_path
from .store import read_all_entries


def _file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        st = path.stat()
    except OSError:
        return {"exists": True, "size": None, "mtime": None}
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    return {"exists": True, "size": st.st_size, "mtime": mtime}


def collect_stats(cwd: str | None = None) -> dict[str, Any]:
    """Return a structured snapshot of the current memory state.

    Shape is stable and intended to be diffed across runs. Counts split into
    live (not deleted) and total so loops can detect soft-deleted growth too.
    """
    agmem_path = config.agmem_dir(cwd)
    entries = read_all_entries(cwd=cwd, include_deleted=True)

    by_kind: dict[str, int] = {}
    by_source: dict[str, int] = {}
    drifted = 0
    verified = 0
    deleted = 0
    live = 0
    latest_index_ts: str | None = None
    latest_manual_ts: str | None = None

    for e in entries:
        if e.is_deleted:
            deleted += 1
            continue
        live += 1
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        by_source[e.source] = by_source.get(e.source, 0) + 1
        if e.drifted_at:
            drifted += 1
        if e.verified_at:
            verified += 1
        if e.source == "index" and (latest_index_ts is None or e.ts > latest_index_ts):
            latest_index_ts = e.ts
        if e.source == "manual" and (latest_manual_ts is None or e.ts > latest_manual_ts):
            latest_manual_ts = e.ts

    return {
        "agmem_dir": str(agmem_path),
        "memories": {
            "live": live,
            "deleted": deleted,
            "total": live + deleted,
            "by_kind": by_kind,
            "by_source": by_source,
            "drifted": drifted,
            "verified": verified,
            "latest_index_ts": latest_index_ts,
            "latest_manual_ts": latest_manual_ts,
        },
        "hot": _file_state(hot_path(cwd)),
        "memories_file": _file_state(config.memories_path(cwd)),
    }
