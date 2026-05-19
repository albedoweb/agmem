"""File-system watcher that keeps the agmem index fresh against active editing.

Design:
- Append events to .agmem/_watch_queue.jsonl as they arrive (cheap, durable).
- Every ``interval`` seconds (default 600), read the queue, dedupe by path,
  call ``apply_paths()``, then atomically rename the queue to empty it.
- On startup, replay any queue left from a prior crash before arming the watcher.
- Skips: .agmem/ itself, .git/, paths matching .gitignore.
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .indexer import _load_gitignore, _should_skip, apply_paths

QUEUE_FILENAME = "_watch_queue.jsonl"
EDITOR_SWAP_EXTS: set[str] = {".swp", ".swx", ".swo", ".swn"}
EDITOR_NOISE_PREFIXES: tuple[str, ...] = (".#", "~$")


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def queue_path(cwd: str | None = None) -> Path:
    return config.agmem_dir(cwd) / QUEUE_FILENAME


def _repo_rel(path: Path, root: Path) -> str | None:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return None


def _is_watchable(path: Path, root: Path, spec) -> bool:
    """True if this absolute path should trigger a reindex event."""
    rel = _repo_rel(path, root)
    if rel is None:
        return False
    if _should_skip(root / rel, root, spec):
        return False
    name = path.name
    if name.startswith("."):
        return False
    if name.startswith(EDITOR_NOISE_PREFIXES):
        return False
    if Path(name).suffix in EDITOR_SWAP_EXTS:
        return False
    return True


def enqueue(cwd: str | None, path: str, op: str) -> None:
    """Append one event to the queue. Caller passes repo-relative path."""
    config.ensure_agmem_dir(cwd)
    record = {"ts": _utc_iso_now(), "path": path, "op": op}
    with open(queue_path(cwd), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def drain_queue(cwd: str | None = None) -> tuple[list[str], list[str]]:
    """Read queue file, return (modified_paths, deleted_paths) deduped.

    Atomic: renames the queue to a tmp file, reads from the tmp, then deletes
    it. Concurrent ``enqueue()`` calls during drain land in the new (empty) queue.
    """
    qpath = queue_path(cwd)
    if not qpath.exists():
        return [], []
    tmp = qpath.with_suffix(".jsonl.draining")
    qpath.rename(tmp)

    modified: dict[str, str] = {}
    for line in tmp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        modified[ev["path"]] = ev["op"]

    tmp.unlink()
    paths_modified = [p for p, op in modified.items() if op in ("created", "modified")]
    paths_deleted = [p for p, op in modified.items() if op == "deleted"]
    return paths_modified, paths_deleted


def apply_queue_once(cwd: str | None = None) -> dict:
    """Drain queue + apply paths. Used by ``agmem flush`` and the watch loop."""
    modified, deleted = drain_queue(cwd)
    if not modified and not deleted:
        return {"upserted": 0, "removed": 0, "skipped_ignored": 0, "events": 0}
    result = apply_paths(modified, deleted, cwd=cwd)
    result["events"] = len(modified) + len(deleted)
    return result


def run_watch(cwd: str | None = None, interval: int = 600) -> None:
    """Run the watch loop until ctrl-C. Polling-based for portability.

    On entry: drain any leftover queue from a prior session.
    Then walk the tree every ``interval`` seconds, diff mtimes, and enqueue changes.
    """
    root = config.find_repo_root(cwd)
    spec = _load_gitignore(root)

    # Replay pre-existing queue (crash recovery)
    applied = apply_queue_once(cwd)
    if applied.get("events", 0) > 0:
        print(f"[watch] Applied {applied['events']} queued events: "
              f"upserted={applied['upserted']}, removed={applied['removed']}")

    print(f"[watch] Watching {root} (polling every {interval}s). Ctrl-C to stop.")

    # Build initial mtime snapshot
    mtime_map: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            full = Path(dirpath) / fname
            if not _is_watchable(full, root, spec):
                continue
            try:
                mtime_map[str(full)] = full.stat().st_mtime
            except OSError:
                pass

    running = True

    def _on_signal(signum, frame):
        nonlocal running
        running = False
        print("\n[watch] Shutting down...")

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        while running:
            time.sleep(interval)
            if not running:
                break

            current: dict[str, float] = {}
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fname in filenames:
                    full = Path(dirpath) / fname
                    if not _is_watchable(full, root, spec):
                        continue
                    try:
                        current[str(full)] = full.stat().st_mtime
                    except OSError:
                        pass

            rel_root = str(root)
            old_paths = set(mtime_map.keys())
            cur_paths = set(current.keys())

            for abs_path in cur_paths - old_paths:
                rel = _repo_rel(Path(abs_path), root)
                if rel:
                    enqueue(cwd, rel, "created")
            for abs_path in old_paths - cur_paths:
                rel = _repo_rel(Path(abs_path), root)
                if rel:
                    enqueue(cwd, rel, "deleted")
            for abs_path in cur_paths & old_paths:
                if current[abs_path] != mtime_map[abs_path]:
                    rel = _repo_rel(Path(abs_path), root)
                    if rel:
                        enqueue(cwd, rel, "modified")

            mtime_map = current

            result = apply_queue_once(cwd)
            if result.get("events", 0) > 0:
                print(f"[watch] Flushed {result['events']} events: "
                      f"upserted={result['upserted']}, removed={result['removed']}")

    finally:
        # Final drain on exit
        result = apply_queue_once(cwd)
        if result.get("events", 0) > 0:
            print(f"[watch] Final flush: {result['events']} events: "
                  f"upserted={result['upserted']}, removed={result['removed']}")
        print("[watch] Done.")
