"""Tests for CLI commands via typer.testing.CliRunner."""

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from agmem.cli import app

runner = CliRunner()


def _patch_config(monkeypatch, tmpdir: str):
    tdp = Path(tmpdir)
    monkeypatch.setattr(
        "agmem.cli.init_config", lambda project_name=None: None
    )
    monkeypatch.setattr(
        "agmem.cli.read_config", lambda: {"version": 1, "project": "test"}
    )
    monkeypatch.setattr(
        "agmem.cli.read_all_entries", lambda: []
    )
    monkeypatch.setattr(
        "agmem.cli.append_entry", lambda entry: None
    )
    monkeypatch.setattr(
        "agmem.cli.search_filtered", lambda query, limit=10, tag=None, cwd=None: []
    )
    monkeypatch.setattr(
        "agmem.store.config.agmem_dir", lambda cwd=None: tdp / ".agmem"
    )
    monkeypatch.setattr(
        "agmem.store.config.memories_path", lambda cwd=None: tdp / ".agmem" / "memories.jsonl"
    )
    monkeypatch.setattr(
        "agmem.store.config.config_path", lambda cwd=None: tdp / ".agmem" / "config.yaml"
    )
    monkeypatch.setattr(
        "agmem.store.config.ensure_agmem_dir", lambda cwd=None: (tdp / ".agmem")
    )
    monkeypatch.setattr(
        "agmem.store.config.read_config", lambda cwd=None: {"version": 1, "project": "test"}
    )
    monkeypatch.setattr(
        "agmem.store.config.init_config", lambda project_name=None: {"version": 1, "project": project_name or "test"}
    )


def _with_memory(monkeypatch, tmpdir: str, entries_data: list[dict]):
    """Set up memories in the temp dir and patch read/append accordingly."""
    tdp = Path(tmpdir)
    (tdp / ".agmem").mkdir(exist_ok=True)
    from agmem.store import MemoryEntry
    entries = [MemoryEntry.from_dict(d) for d in entries_data]

    import json
    with open(tdp / ".agmem" / "memories.jsonl", "w") as f:
        for e in entries:
            f.write(json.dumps(e.to_dict()) + "\n")

    monkeypatch.setattr(
        "agmem.store.config.agmem_dir", lambda cwd=None: tdp / ".agmem"
    )
    monkeypatch.setattr(
        "agmem.store.config.memories_path", lambda cwd=None: tdp / ".agmem" / "memories.jsonl"
    )
    monkeypatch.setattr(
        "agmem.store.config.config_path", lambda cwd=None: tdp / ".agmem" / "config.yaml"
    )
    monkeypatch.setattr(
        "agmem.store.config.ensure_agmem_dir", lambda cwd=None: (tdp / ".agmem")
    )
    monkeypatch.setattr(
        "agmem.store.config.read_config", lambda cwd=None: {"version": 1, "project": "test"}
    )
    monkeypatch.setattr(
        "agmem.store.config.init_config", lambda project_name=None: {"version": 1, "project": project_name or "test"}
    )

    return entries


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "agmem" in result.stdout


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "remember" in result.stdout


def test_init(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tdp = Path(tmpdir)
        _patch_config(monkeypatch, tmpdir)
        monkeypatch.setattr(
            "agmem.cli.read_config",
            lambda: {},
        )
        monkeypatch.setattr(
            "agmem.cli.init_config",
            lambda project_name=None: None,
        )
        result = runner.invoke(app, ["init", "--project", "test"])
        assert result.exit_code == 0


def test_init_idempotent(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _patch_config(monkeypatch, tmpdir)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1


def test_remember_without_init(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(
            "agmem.cli.read_config",
            lambda: (_ for _ in ()).throw(Exception("not found")),
        )
        result = runner.invoke(app, ["remember", "some text"])
        assert result.exit_code == 1


def test_remember_and_list(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        entries = _with_memory(monkeypatch, tmpdir, [
            {
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "test memory",
                "tags": ["foo", "bar"], "source": "manual", "source_ref": None,
            }
        ])
        monkeypatch.setattr(
            "agmem.cli.read_all_entries", lambda: entries
        )
        monkeypatch.setattr(
            "agmem.cli.append_entry", lambda entry: None
        )
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "test memory" in result.stdout


def test_list_tag_filter(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        entries = _with_memory(monkeypatch, tmpdir, [
            {
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "billing note",
                "tags": ["billing"], "source": "manual", "source_ref": None,
            },
            {
                "id": "01B", "ts": "2026-01-02T00:00:00+00:00",
                "text": "auth note",
                "tags": ["auth"], "source": "manual", "source_ref": None,
            },
        ])
        monkeypatch.setattr(
            "agmem.cli.read_all_entries", lambda: entries
        )
        result = runner.invoke(app, ["list", "-t", "billing"])
        assert result.exit_code == 0
        assert "billing note" in result.stdout
        assert "auth note" not in result.stdout


def test_recall_markdown(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _with_memory(monkeypatch, tmpdir, [
            {
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "Billing webhooks must be idempotent.",
                "tags": ["billing"], "source": "manual", "source_ref": None,
            },
            {
                "id": "01B", "ts": "2026-01-02T00:00:00+00:00",
                "text": "Auth uses JWT tokens.",
                "tags": ["auth"], "source": "manual", "source_ref": None,
            },
        ])
        from agmem.store import MemoryEntry

        def _mock_search(query, limit=10, tag=None, cwd=None):
            entry = MemoryEntry.from_dict({
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "Billing webhooks must be idempotent.",
                "tags": ["billing"], "source": "manual", "source_ref": None,
            })
            return [(entry, 0.5)]

        monkeypatch.setattr("agmem.cli.search_filtered", _mock_search)
        monkeypatch.setattr("agmem.cli.read_all_entries", 
            lambda: [MemoryEntry.from_dict({
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "Billing webhooks must be idempotent.",
                "tags": ["billing"], "source": "manual", "source_ref": None,
            })]
        )
        result = runner.invoke(app, ["recall", "billing webhook"])
        assert result.exit_code == 0
        assert "idempotent" in result.stdout


def test_recall_json(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _with_memory(monkeypatch, tmpdir, [
            {
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "A memory entry.",
                "tags": [], "source": "manual", "source_ref": None,
            },
        ])
        from agmem.store import MemoryEntry
        entry = MemoryEntry.from_dict({
            "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
            "text": "A memory entry.",
            "tags": [], "source": "manual", "source_ref": None,
        })
        monkeypatch.setattr("agmem.cli.search_filtered", lambda query, limit=10, tag=None, cwd=None: [(entry, 0.5)])
        result = runner.invoke(app, ["recall", "memory", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["text"] == "A memory entry."
        assert "score" in data[0]


def test_context_markdown(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _with_memory(monkeypatch, tmpdir, [
            {
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "Do not call Stripe directly.",
                "tags": ["billing"], "source": "manual", "source_ref": None,
            },
        ])
        from agmem.store import MemoryEntry
        entry = MemoryEntry.from_dict({
            "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
            "text": "Do not call Stripe directly.",
            "tags": ["billing"], "source": "manual", "source_ref": None,
        })
        monkeypatch.setattr("agmem.cli.search_filtered", lambda query, limit=10, tag=None, cwd=None: [(entry, 0.5)])
        result = runner.invoke(app, ["context", "fix stripe bug"])
        assert result.exit_code == 0
        assert "Context for" in result.stdout
        assert "Stripe" in result.stdout
        assert "Hint" in result.stdout


def test_context_json(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _with_memory(monkeypatch, tmpdir, [
            {
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "Important constraint.",
                "tags": [], "source": "manual", "source_ref": None,
            },
        ])
        from agmem.store import MemoryEntry
        entry = MemoryEntry.from_dict({
            "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
            "text": "Important constraint.",
            "tags": [], "source": "manual", "source_ref": None,
        })
        monkeypatch.setattr("agmem.cli.search_filtered", lambda query, limit=10, tag=None, cwd=None: [(entry, 0.5)])
        result = runner.invoke(app, ["context", "task", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1


def test_context_empty(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _with_memory(monkeypatch, tmpdir, [])
        monkeypatch.setattr("agmem.cli.search_filtered", lambda query, limit=10, tag=None, cwd=None: [])
        result = runner.invoke(app, ["context", "some task"])
        assert result.exit_code == 0
        assert "No relevant memories" in result.stdout


def test_list_empty(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _with_memory(monkeypatch, tmpdir, [])
        monkeypatch.setattr("agmem.cli.read_all_entries", lambda: [])
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No entries" in result.stdout


def test_recall_no_results(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        _with_memory(monkeypatch, tmpdir, [
            {
                "id": "01A", "ts": "2026-01-01T00:00:00+00:00",
                "text": "something",
                "tags": [], "source": "manual", "source_ref": None,
            },
        ])
        monkeypatch.setattr("agmem.cli.search_filtered", lambda query, limit=10, tag=None, cwd=None: [])
        result = runner.invoke(app, ["recall", "zzznotfound"])
        assert result.exit_code == 0
