from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from agmem.watch import (
    QUEUE_FILENAME,
    _is_watchable,
    _repo_rel,
    apply_queue_once,
    drain_queue,
    enqueue,
    queue_path,
)


@pytest.fixture
def tmp_agmem(tmp_path, monkeypatch):
    agmem_dir = tmp_path / ".agmem"
    agmem_dir.mkdir()
    (agmem_dir / "config.yaml").write_text("version: 1\nproject: test\n")
    (agmem_dir / "memories.jsonl").write_text("")
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: tmp_path)
    monkeypatch.setattr("agmem.config.ensure_agmem_dir", lambda cwd=None: agmem_dir)
    monkeypatch.setattr("agmem.config.agmem_dir", lambda cwd=None: agmem_dir)
    monkeypatch.setattr("agmem.config.memories_path", lambda cwd=None: agmem_dir / "memories.jsonl")
    monkeypatch.setattr("agmem.config.read_config", lambda cwd=None: {"version": 1, "project": "test"})

    import agmem
    monkeypatch.setattr(agmem.watch, "config", agmem.config)
    monkeypatch.setattr(agmem.indexer, "config", agmem.config)
    monkeypatch.setattr(agmem.store, "config", agmem.config)
    return tmp_path


class TestRepoRel:
    def test_under_root(self, tmp_path):
        f = tmp_path / "src" / "app.py"
        f.parent.mkdir(parents=True)
        f.touch()
        assert _repo_rel(f, tmp_path) == "src/app.py"

    def test_outside_root(self, tmp_path):
        f = Path("/other/file.py")
        assert _repo_rel(f, tmp_path) is None

    def test_equals_root(self, tmp_path):
        assert _repo_rel(tmp_path, tmp_path) == "."


class TestIsWatchable:
    def test_normal_file(self, tmp_path):
        root = tmp_path
        f = root / "src" / "app.py"
        f.parent.mkdir(parents=True)
        f.touch()
        from agmem.indexer import _load_gitignore
        spec = _load_gitignore(root)
        assert _is_watchable(f, root, spec) is True

    def test_dotfile_excluded(self, tmp_path):
        root = tmp_path
        f = root / ".secret"
        f.touch()
        from agmem.indexer import _load_gitignore
        spec = _load_gitignore(root)
        assert _is_watchable(f, root, spec) is False

    def test_editor_swap_excluded(self, tmp_path):
        root = tmp_path
        f = root / "src" / ".app.py.swp"
        f.parent.mkdir(parents=True)
        f.touch()
        from agmem.indexer import _load_gitignore
        spec = _load_gitignore(root)
        assert _is_watchable(f, root, spec) is False

    def test_editor_temp_prefix(self, tmp_path):
        root = tmp_path
        f = root / ".#app.py"
        f.touch()
        from agmem.indexer import _load_gitignore
        spec = _load_gitignore(root)
        assert _is_watchable(f, root, spec) is False

    def test_agmem_dir_excluded(self, tmp_path):
        root = tmp_path
        (root / ".agmem").mkdir()
        f = root / ".agmem" / "config.yaml"
        f.touch()
        from agmem.indexer import _load_gitignore
        spec = _load_gitignore(root)
        assert _is_watchable(f, root, spec) is False


class TestEnqueue:
    def test_writes_jsonl_line(self, tmp_agmem):
        enqueue(None, "src/app.py", "modified")
        qp = queue_path()
        assert qp.exists()
        lines = qp.read_text().strip().split("\n")
        assert len(lines) == 1
        ev = json.loads(lines[0])
        assert ev["path"] == "src/app.py"
        assert ev["op"] == "modified"
        assert "ts" in ev

    def test_appends_multiple_events(self, tmp_agmem):
        enqueue(None, "src/a.py", "created")
        enqueue(None, "src/b.py", "deleted")
        qp = queue_path()
        events = [json.loads(l) for l in qp.read_text().strip().split("\n") if l.strip()]
        assert len(events) == 2
        assert events[0]["path"] == "src/a.py"
        assert events[1]["path"] == "src/b.py"


class TestDrainQueue:
    def test_empty_queue(self, tmp_agmem):
        mod, dele = drain_queue()
        assert mod == []
        assert dele == []

    def test_dedupes_per_path_last_op_wins(self, tmp_agmem):
        enqueue(None, "src/a.py", "created")
        enqueue(None, "src/a.py", "modified")
        enqueue(None, "src/a.py", "modified")
        enqueue(None, "src/b.py", "deleted")
        mod, dele = drain_queue()
        assert mod == ["src/a.py"]
        assert dele == ["src/b.py"]

    def test_queue_file_removed_after_drain(self, tmp_agmem):
        enqueue(None, "src/a.py", "modified")
        drain_queue()
        qp = queue_path()
        assert not qp.exists()

    def test_create_then_delete_net_modified(self, tmp_agmem):
        enqueue(None, "src/x.py", "created")
        enqueue(None, "src/x.py", "modified")
        mod, dele = drain_queue()
        assert mod == ["src/x.py"]
        assert dele == []


class TestApplyQueueOnce:
    def test_no_events_returns_zero(self, tmp_agmem):
        result = apply_queue_once()
        assert result["events"] == 0

    def test_upserts_file(self, tmp_agmem):
        code = """
def hello():
    return "world"

class Greeter:
    def greet(self):
        pass
"""
        f = tmp_agmem / "src" / "app.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(code)

        enqueue(None, "src/app.py", "modified")
        result = apply_queue_once()

        assert result["upserted"] >= 1
        assert result["events"] == 1

        from agmem.store import read_all_entries
        entries = read_all_entries()
        refs = [e.source_ref for e in entries]
        assert any(ref and ref.endswith("app.py") for ref in refs), f"refs: {refs}"

    def test_skips_gitignored_paths(self, tmp_agmem):
        (tmp_agmem / ".gitignore").write_text("*.log\n")
        f = tmp_agmem / "debug.log"
        f.write_text("xxx")

        enqueue(None, "debug.log", "modified")
        apply_queue_once()

        from agmem.store import read_all_entries
        entries = read_all_entries()
        assert not any("debug.log" == e.source_ref for e in entries)

    def test_handles_deleted_path(self, tmp_agmem):
        code = "def old_func():\n    pass\n"
        f = tmp_agmem / "src" / "old.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(code)
        enqueue(None, "src/old.py", "modified")
        apply_queue_once()

        from agmem.store import read_all_entries
        entries = read_all_entries()
        refs = [e.source_ref for e in entries]
        assert any(ref and ref.endswith("old.py") for ref in refs), f"refs: {refs}"

        f.unlink()
        enqueue(None, "src/old.py", "deleted")
        result = apply_queue_once()
        assert result["removed"] == 1

        entries = read_all_entries()
        refs = [e.source_ref for e in entries]
        assert not any(ref and ref.endswith("old.py") for ref in refs), f"refs: {refs}"


# ---------------------------------------------------------------------------
# Multi-repo watch: watchlist, status heartbeat, root resolution, RepoWatcher
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    ad = path / ".agmem"
    ad.mkdir()
    (ad / "config.yaml").write_text(f"version: 1\nproject: {path.name}\n")
    (ad / "memories.jsonl").write_text("")
    return path


class TestWatchlist:
    def test_reads_paths_skips_comments_and_blanks(self, tmp_path, monkeypatch):
        import agmem.watch as W
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        wl = tmp_path / "agmem" / "watchlist"
        wl.parent.mkdir(parents=True)
        wl.write_text("# header\n\n/repo/one\n  /repo/two  \n# comment\n")
        assert W.read_watchlist() == ["/repo/one", "/repo/two"]

    def test_missing_returns_empty(self, tmp_path, monkeypatch):
        import agmem.watch as W
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nope"))
        assert W.read_watchlist() == []

    def test_tilde_expanded(self, tmp_path, monkeypatch):
        import agmem.watch as W
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        wl = tmp_path / "agmem" / "watchlist"
        wl.parent.mkdir(parents=True)
        wl.write_text("~/proj\n")
        assert W.read_watchlist() == [str(Path.home() / "proj")]

    def test_paths_under_xdg_config_home(self, tmp_path, monkeypatch):
        import agmem.watch as W
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert W.watchlist_path() == tmp_path / "agmem" / "watchlist"
        assert W.status_path() == tmp_path / "agmem" / "watch.status.json"


class TestStatusFile:
    def test_write_read_clear_roundtrip(self, tmp_path, monkeypatch):
        import agmem.watch as W
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        W.write_status(["/r/a", "/r/b"], 600, "2026-05-26T00:00:00Z", None)
        st = W.read_status()
        assert st is not None
        assert st["roots"] == ["/r/a", "/r/b"]
        assert st["interval"] == 600
        assert st["pid"] == os.getpid()
        W.clear_status()
        assert W.read_status() is None

    def test_clear_when_missing_is_safe(self, tmp_path, monkeypatch):
        import agmem.watch as W
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nope"))
        W.clear_status()  # no raise


class TestResolveRoots:
    def test_dedupe_and_worktree_resolution(self, monkeypatch):
        import agmem.watch as W
        mapping = {"/repo/wt": "/repo", "/repo": "/repo", "/other": "/other"}
        monkeypatch.setattr("agmem.config.find_repo_root", lambda g: Path(mapping[str(g)]))
        assert W._resolve_roots(["/repo/wt", "/repo", "/other"]) == ["/repo", "/other"]

    def test_resolve_roots_is_silent(self, monkeypatch, capsys):
        """Pure function — no printing. (Warning lives in _warn_nested_roots
        so it doesn't flood the log on every hot-reload cycle.)"""
        import agmem.watch as W
        monkeypatch.setattr("agmem.config.find_repo_root", lambda g: Path(str(g)))
        out = W._resolve_roots(["/ws", "/ws/sub"])
        assert out == ["/ws", "/ws/sub"]
        assert capsys.readouterr().out == ""


class TestWarnNestedRoots:
    def test_warns_on_first_seen_pair(self, capsys):
        import agmem.watch as W
        warned: set[tuple[str, str]] = set()
        W._warn_nested_roots(["/ws", "/ws/sub"], warned)
        out = capsys.readouterr().out
        assert "nested under" in out
        assert ("/ws/sub", "/ws") in warned

    def test_silent_on_repeat_for_same_pair(self, capsys):
        import agmem.watch as W
        warned: set[tuple[str, str]] = set()
        W._warn_nested_roots(["/ws", "/ws/sub"], warned)
        capsys.readouterr()  # discard first print
        W._warn_nested_roots(["/ws", "/ws/sub"], warned)
        assert capsys.readouterr().out == ""

    def test_warns_only_new_pair_on_partial_overlap(self, capsys):
        import agmem.watch as W
        warned: set[tuple[str, str]] = set()
        W._warn_nested_roots(["/ws", "/ws/sub"], warned)
        capsys.readouterr()
        W._warn_nested_roots(["/ws", "/ws/sub", "/ws/another"], warned)
        out = capsys.readouterr().out
        assert "/ws/another" in out
        assert "/ws/sub" not in out  # old pair stays silent

    def test_no_warning_when_disjoint(self, capsys):
        import agmem.watch as W
        warned: set[tuple[str, str]] = set()
        W._warn_nested_roots(["/a", "/b"], warned)
        assert capsys.readouterr().out == ""


class TestRepoWatcherTick:
    def test_detects_created_and_applies(self, tmp_agmem):
        import agmem.watch as W
        w = W.RepoWatcher(cwd=str(tmp_agmem))
        w.bootstrap()  # snapshot (empty: only .agmem/.git present)
        (tmp_agmem / "new_mod.py").write_text("def f():\n    return 1\n")
        result = w.tick()
        assert result["events"] >= 1
        assert "new_mod.py" in (tmp_agmem / ".agmem" / "memories.jsonl").read_text()


class TestRunWatchMultiRepo:
    def test_one_cycle_indexes_each_repo(self, tmp_path, monkeypatch):
        import agmem.watch as W
        repo_a = _init_repo(tmp_path / "a")
        repo_b = _init_repo(tmp_path / "b")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

        captured: dict = {}

        def fake_signal(sig, handler):
            if sig == W.signal.SIGINT:
                captured["h"] = handler

        monkeypatch.setattr(W.signal, "signal", fake_signal)

        calls = {"n": 0}

        def fake_sleep(_):
            calls["n"] += 1
            if calls["n"] == 1:
                (repo_a / "alpha.py").write_text("def a():\n    return 1\n")
                (repo_b / "beta.py").write_text("def b():\n    return 2\n")
            else:
                captured["h"](W.signal.SIGINT, None)

        monkeypatch.setattr(W.time, "sleep", fake_sleep)

        W.run_watch(roots=[str(repo_a), str(repo_b)], interval=1, hot_reload=False)

        assert "alpha.py" in (repo_a / ".agmem" / "memories.jsonl").read_text()
        assert "beta.py" in (repo_b / ".agmem" / "memories.jsonl").read_text()
        assert W.read_status() is None  # cleared on clean exit

    def test_no_watchable_repos_returns_early(self, tmp_path, monkeypatch, capsys):
        import agmem.watch as W
        # an explicit dir with no .agmem → skipped → nothing to watch
        bare = tmp_path / "bare"
        bare.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        W.run_watch(roots=[str(bare)], interval=1)
        out = capsys.readouterr().out
        assert "not initialized" in out or "No watchable repos" in out


class TestInterruptibleSleep:
    def test_stops_promptly_when_flag_clears(self, monkeypatch):
        import agmem.watch as W
        monkeypatch.setattr(W.time, "sleep", lambda s: None)
        ticks = {"n": 0}

        def still():
            ticks["n"] += 1
            return ticks["n"] <= 3

        W._sleep_interruptible(600, still)
        assert ticks["n"] == 4  # exited at the first False, not after ~600 chunks

    def test_sleeps_full_duration_in_chunks(self, monkeypatch):
        import agmem.watch as W
        slept = {"total": 0.0}
        monkeypatch.setattr(W.time, "sleep", lambda s: slept.__setitem__("total", slept["total"] + s))
        W._sleep_interruptible(3, lambda: True)
        assert slept["total"] == 3.0
