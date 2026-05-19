"""Tests for `agmem stats` and the `collect_stats()` snapshot.

Shape stability matters: external scripts (autoresearch loops, dashboards) will
diff these dicts across runs, so any field rename is a breaking change. Keep
the explicit-key assertions strict.
"""

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agmem.cli import app
from agmem.stats import collect_stats
from agmem.store import append_entry, create_entry

runner = CliRunner()


def _patch_root(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    monkeypatch.setattr(
        "agmem.config.read_config", lambda cwd=None: {"version": 1, "project": "test"}
    )
    monkeypatch.setattr("agmem.cli.read_config", lambda: {"version": 1, "project": "test"})
    (root / ".agmem").mkdir(exist_ok=True)


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_root(monkeypatch, root)
    yield root


def test_collect_stats_empty(repo: Path):
    snap = collect_stats()
    assert snap["agmem_dir"] == str(repo / ".agmem")
    assert snap["memories"]["live"] == 0
    assert snap["memories"]["deleted"] == 0
    assert snap["memories"]["total"] == 0
    assert snap["memories"]["by_kind"] == {}
    assert snap["memories"]["by_source"] == {}
    assert snap["memories"]["drifted"] == 0
    assert snap["memories"]["verified"] == 0
    assert snap["memories"]["latest_index_ts"] is None
    assert snap["memories"]["latest_manual_ts"] is None
    assert snap["hot"]["exists"] is False
    assert snap["memories_file"]["exists"] is False


def test_collect_stats_counts_by_kind_and_source(repo: Path):
    append_entry(create_entry("rule one", kind="rule", source="manual"))
    append_entry(create_entry("rule two", kind="rule", source="manual"))
    append_entry(create_entry("fact one", kind="fact", source="index", source_ref="a.md"))
    append_entry(create_entry("pattern one", kind="pattern", source="index", source_ref="b.md"))

    snap = collect_stats()
    assert snap["memories"]["live"] == 4
    assert snap["memories"]["by_kind"] == {"rule": 2, "fact": 1, "pattern": 1}
    assert snap["memories"]["by_source"] == {"manual": 2, "index": 2}
    assert snap["memories"]["latest_index_ts"] is not None
    assert snap["memories"]["latest_manual_ts"] is not None


def test_collect_stats_separates_deleted(repo: Path):
    from agmem.store import read_all_entries, rewrite_entries
    append_entry(create_entry("alive", kind="fact"))
    append_entry(create_entry("doomed", kind="fact"))
    entries = read_all_entries(include_deleted=True)
    entries[-1].deleted_at = "2026-05-09T00:00:00+00:00"
    rewrite_entries(entries)

    snap = collect_stats()
    assert snap["memories"]["live"] == 1
    assert snap["memories"]["deleted"] == 1
    assert snap["memories"]["total"] == 2
    # by_kind/by_source only count live entries
    assert snap["memories"]["by_kind"] == {"fact": 1}


def test_collect_stats_drifted_and_verified_counts(repo: Path):
    from agmem.store import read_all_entries, rewrite_entries
    append_entry(create_entry("a", kind="fact"))
    append_entry(create_entry("b", kind="fact"))
    entries = read_all_entries()
    entries[0].drifted_at = "2026-05-09T00:00:00+00:00"
    entries[1].verified_at = "2026-05-09T00:00:00+00:00"
    rewrite_entries(entries)

    snap = collect_stats()
    assert snap["memories"]["drifted"] == 1
    assert snap["memories"]["verified"] == 1


def test_collect_stats_hot_state(repo: Path):
    (repo / ".agmem" / "_hot.md").write_text("# rules\n- be careful\n", encoding="utf-8")
    snap = collect_stats()
    assert snap["hot"]["exists"] is True
    assert snap["hot"]["size"] > 0
    assert snap["hot"]["mtime"] is not None


def test_cli_stats_emits_json(repo: Path):
    append_entry(create_entry("hello", kind="fact"))
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["memories"]["live"] == 1
    assert payload["memories"]["by_kind"] == {"fact": 1}


def test_cli_stats_text_mode(repo: Path):
    append_entry(create_entry("hello", kind="rule", source="manual"))
    result = runner.invoke(app, ["stats", "--text"])
    assert result.exit_code == 0
    assert "memories: live=1" in result.stdout
    assert "rule=1" in result.stdout
    assert "manual=1" in result.stdout


def test_cli_list_json(repo: Path):
    append_entry(create_entry("first", kind="fact", tags=["foo"]))
    append_entry(create_entry("second", kind="rule"))
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["text"] == "first"
    assert payload[0]["tags"] == ["foo"]


def test_cli_hot_json_when_missing(repo: Path):
    result = runner.invoke(app, ["hot", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["exists"] is False


def test_cli_hot_json_when_present(repo: Path):
    (repo / ".agmem" / "_hot.md").write_text("# hi\n", encoding="utf-8")
    result = runner.invoke(app, ["hot", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["exists"] is True
    assert payload["content"] == "# hi\n"
    assert payload["chars"] == 5
    assert payload["mtime"] is not None


def test_cli_review_json(repo: Path):
    append_entry(create_entry("a", kind="fact"))
    result = runner.invoke(app, ["review", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "total_live" in payload
    assert payload["drifted"] == []
    assert payload["missing_source"] == []
    assert payload["stale"] == []
    assert payload["duplicates"] == []
