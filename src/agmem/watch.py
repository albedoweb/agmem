"""File-system watcher that keeps agmem indexes fresh against active editing.

One process watches any number of repos. Each repo is an independent
``RepoWatcher`` (its own root, gitignore spec, mtime snapshot, ``.agmem`` index
and ``_watch_queue.jsonl``); ``run_watch`` polls them all on a single loop.

Design:
- Append events to each repo's .agmem/_watch_queue.jsonl as they arrive.
- Every ``interval`` seconds (default 600), per repo: diff mtimes, enqueue
  changes, ``apply_paths()``, then atomically empty the queue.
- On startup, replay any queue left from a prior crash before arming.
- Repo list comes from positional args, else the global watchlist
  (``$XDG_CONFIG_HOME/agmem/watchlist`` → ``~/.config/agmem/watchlist``), which
  is re-read each cycle (hot-reload) so repos can be added/removed without a
  restart. A heartbeat file (``watch.status.json``) records pid/version/roots.
- Per-repo ticks are exception-isolated: one bad repo can't take down the rest.
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


def _config_base() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "agmem"


def watchlist_path() -> Path:
    return _config_base() / "watchlist"


def status_path() -> Path:
    return _config_base() / "watch.status.json"


def read_watchlist() -> list[str]:
    """Repo paths from the global watchlist: one per line, ``#`` comments and
    blank lines ignored, ``~`` expanded. Missing file → empty list."""
    p = watchlist_path()
    if not p.exists():
        return []
    roots: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        roots.append(os.path.expanduser(line))
    return roots


def _agmem_version() -> str:
    try:
        from importlib.metadata import version
        return version("agmem")
    except Exception:
        return "unknown"


def write_status(roots: list[str], interval: int, started_at: str,
                 last_tick: str | None) -> None:
    """Heartbeat so 'is it running / is it stale?' is answerable without ps."""
    sp = status_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "pid": os.getpid(),
        "version": _agmem_version(),
        "started_at": started_at,
        "last_tick": last_tick,
        "interval": interval,
        "roots": roots,
    }
    tmp = sp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(sp)


def read_status() -> dict | None:
    sp = status_path()
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_status() -> None:
    try:
        status_path().unlink()
    except OSError:
        pass


def _resolve_roots(given: list[str]) -> list[str]:
    """Map each dir to its repo root (worktrees → main repo), dedupe, preserve
    order. Pure — see ``_warn_nested_roots`` for the nesting warning."""
    resolved: list[str] = []
    seen: set[str] = set()
    for g in given:
        try:
            root = str(config.find_repo_root(g))
        except Exception:
            root = str(Path(g).expanduser().resolve())
        if root not in seen:
            seen.add(root)
            resolved.append(root)
    return resolved


def _warn_nested_roots(roots: list[str], already_warned: set[tuple[str, str]]) -> None:
    """Print a nested-root warning once per (inner, outer) pair. Subsequent
    hot-reload cycles with the same nesting stay silent — `already_warned`
    persists across calls so the log isn't flooded every ``interval`` seconds."""
    for a in roots:
        for b in roots:
            if a != b and a.startswith(b.rstrip("/") + "/"):
                key = (a, b)
                if key not in already_warned:
                    already_warned.add(key)
                    print(f"[watch] WARNING: {a} is nested under {b}; the outer "
                          f"watcher will also index {a}'s files into {b}'s index.")
                break


def _sleep_interruptible(seconds: float, still_running) -> None:
    """Sleep ``seconds`` but re-check ``still_running()`` roughly once a second.

    A bare ``time.sleep(interval)`` is the wrong primitive for a stoppable loop:
    when a signal handler that doesn't raise (ours just flips a flag) fires, PEP
    475 resumes the sleep for the *remaining* interval — so a 600s watcher would
    take up to 600s to honor Ctrl-C/SIGTERM. Chunking caps shutdown latency at ~1s.
    """
    remaining = float(seconds)
    while still_running() and remaining > 0:
        chunk = min(1.0, remaining)
        time.sleep(chunk)
        remaining -= chunk


def _scan_tree(root: Path, spec) -> dict[str, float]:
    out: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            full = Path(dirpath) / fname
            if not _is_watchable(full, root, spec):
                continue
            try:
                out[str(full)] = full.stat().st_mtime
            except OSError:
                pass
    return out


class RepoWatcher:
    """Per-repo watch state. All index/queue writes route through the cwd-keyed
    helpers, so each repo stays isolated (own ``.agmem`` index + queue)."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.root = config.find_repo_root(cwd)
        self.spec = _load_gitignore(self.root)
        self.mtime_map: dict[str, float] = {}

    def bootstrap(self) -> dict:
        """Replay any leftover queue (crash recovery), then snapshot mtimes."""
        applied = apply_queue_once(self.cwd)
        self.mtime_map = _scan_tree(self.root, self.spec)
        return applied

    def tick(self) -> dict:
        """One poll cycle: diff mtimes against the snapshot, enqueue, apply."""
        current = _scan_tree(self.root, self.spec)
        old = set(self.mtime_map)
        cur = set(current)
        for abs_path in cur - old:
            rel = _repo_rel(Path(abs_path), self.root)
            if rel:
                enqueue(self.cwd, rel, "created")
        for abs_path in old - cur:
            rel = _repo_rel(Path(abs_path), self.root)
            if rel:
                enqueue(self.cwd, rel, "deleted")
        for abs_path in cur & old:
            if current[abs_path] != self.mtime_map[abs_path]:
                rel = _repo_rel(Path(abs_path), self.root)
                if rel:
                    enqueue(self.cwd, rel, "modified")
        self.mtime_map = current
        return apply_queue_once(self.cwd)


def _build_watcher(root: str) -> RepoWatcher | None:
    """Bootstrap a RepoWatcher, or return None (with a reason) if the repo
    isn't usable — never auto-initializes a repo that lacks ``.agmem``."""
    if not config.agmem_dir(root).exists():
        print(f"[watch] skip {root}: not initialized (run `agmem init` there).")
        return None
    try:
        watcher = RepoWatcher(root)
        applied = watcher.bootstrap()
        if applied.get("events", 0) > 0:
            print(f"[watch] {root}: replayed {applied['events']} queued events.")
        return watcher
    except Exception as exc:  # noqa: BLE001 — one bad repo must not abort startup
        print(f"[watch] skip {root}: bootstrap failed ({exc}).")
        return None


def run_watch(
    roots: list[str] | str | None = None,
    interval: int = 600,
    cwd: str | None = None,
    hot_reload: bool = True,
) -> None:
    """Watch one or more repos in a single process until Ctrl-C.

    ``roots`` (a path or list) or legacy ``cwd`` pins an explicit set. If neither
    is given, the global watchlist is used and — when ``hot_reload`` — re-read
    each cycle so repos can be added/removed without a restart. Falls back to the
    current repo when the watchlist is empty.
    """
    explicit = roots is not None or cwd is not None

    def desired_roots() -> list[str]:
        if roots is not None:
            given = [roots] if isinstance(roots, str) else list(roots)
        elif cwd is not None:
            given = [cwd]
        else:
            given = read_watchlist() or [str(config.find_repo_root(None))]
        return _resolve_roots(given)

    watchers: dict[str, RepoWatcher] = {}
    for root in desired_roots():
        watcher = _build_watcher(root)
        if watcher is not None:
            watchers[root] = watcher

    if not watchers:
        print("[watch] No watchable repos. Add paths to "
              f"{watchlist_path()} or run from an initialized repo.")
        return

    started_at = _utc_iso_now()
    write_status(list(watchers), interval, started_at, None)
    where = "args" if explicit else f"watchlist ({watchlist_path()})"
    print(f"[watch] Watching {len(watchers)} repo(s) from {where}, "
          f"polling every {interval}s. Ctrl-C to stop.")
    for root in watchers:
        print(f"          - {root}")

    warned_nested: set[tuple[str, str]] = set()
    _warn_nested_roots(list(watchers), warned_nested)

    running = True

    def _on_signal(signum, frame):
        nonlocal running
        running = False
        print("\n[watch] Shutting down...")

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        while running:
            _sleep_interruptible(interval, lambda: running)
            if not running:
                break

            if hot_reload and not explicit:
                desired = desired_roots()
                desired_set = set(desired)
                for root in desired:
                    if root not in watchers:
                        watcher = _build_watcher(root)
                        if watcher is not None:
                            watchers[root] = watcher
                            print(f"[watch] + added {root}")
                for root in list(watchers):
                    if root not in desired_set:
                        del watchers[root]
                        print(f"[watch] - dropped {root}")
                _warn_nested_roots(list(watchers), warned_nested)

            for root, watcher in list(watchers.items()):
                try:
                    result = watcher.tick()
                    if result.get("events", 0) > 0:
                        print(f"[watch] {root}: flushed {result['events']} events "
                              f"(upserted={result['upserted']}, removed={result['removed']}).")
                except Exception as exc:  # noqa: BLE001 — isolate per-repo failures
                    print(f"[watch] {root}: tick error ({exc}); continuing.")

            write_status(list(watchers), interval, started_at, _utc_iso_now())

    finally:
        for root, watcher in list(watchers.items()):
            try:
                result = apply_queue_once(watcher.cwd)
                if result.get("events", 0) > 0:
                    print(f"[watch] {root}: final flush {result['events']} events.")
            except Exception:  # noqa: BLE001
                pass
        clear_status()
        print("[watch] Done.")
