from __future__ import annotations

import json
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
