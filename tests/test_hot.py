"""Tests for `agmem hot` pre-computed cache (Direction 5)."""

import tempfile
from pathlib import Path

import pytest

from agmem.hot import (
    DEFAULT_BUDGET_CHARS,
    _rank_entries,
    hot_path,
    read_hot,
    render_hot,
    run_refresh,
)
from agmem.store import append_entry, create_entry


def _patch(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.hot.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.hot.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.hot.config.ensure_agmem_dir",
        lambda cwd=None: (root / ".agmem").mkdir(exist_ok=True) or (root / ".agmem"),
    )
    monkeypatch.setattr("agmem.hot.config.read_config", lambda cwd=None: {"project": "demo"})
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    (root / ".agmem").mkdir(exist_ok=True)


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch(monkeypatch, root)
    return root


def test_render_hot_empty():
    text, stats = render_hot([], [], [])
    assert "# Project memory snapshot" in text
    assert stats == {"chars": len(text), "rules": 0, "facts": 0, "patterns": 0}


def test_render_hot_includes_all_rules():
    rules = [
        create_entry("Never run terraform destroy in prod", kind="rule"),
        create_entry("Reuse existing modules", kind="rule"),
    ]
    text, stats = render_hot(rules, [], [])
    assert "## Constraints" in text
    assert "terraform destroy" in text
    assert "Reuse existing modules" in text
    assert stats["rules"] == 2


def test_render_hot_truncates_facts_to_budget():
    rules: list = []
    facts = [create_entry(f"Fact {i}: " + "x" * 80, kind="fact") for i in range(20)]
    # Budget so small that only ~3 facts fit
    text, stats = render_hot(rules, facts, [], budget_chars=400)
    assert stats["facts"] < 20
    assert len(text) <= 600  # header + a few facts; never wildly over


def test_rank_entries_drifted_last():
    e_clean = create_entry("clean", kind="fact")
    e_clean.verified_at = "2026-05-08T00:00:00+00:00"
    e_clean.ts = "2026-05-01T00:00:00+00:00"

    e_drifted = create_entry("drifted", kind="fact")
    e_drifted.drifted_at = "2026-05-08T00:00:00+00:00"
    e_drifted.ts = "2026-05-08T00:00:00+00:00"

    ranked = _rank_entries([e_drifted, e_clean])
    assert ranked[0].text == "clean"
    assert ranked[1].text == "drifted"


def test_rank_entries_verified_more_recent_first():
    e_old = create_entry("old verify", kind="fact")
    e_old.verified_at = "2026-04-01T00:00:00+00:00"
    e_new = create_entry("new verify", kind="fact")
    e_new.verified_at = "2026-05-08T00:00:00+00:00"
    ranked = _rank_entries([e_old, e_new])
    assert ranked[0].text == "new verify"


def test_run_refresh_writes_file(repo: Path):
    append_entry(create_entry("Reuse existing modules", kind="rule", tags=["terraform"]))
    append_entry(create_entry("S3 module path: terraform/modules/aws/s3", kind="fact"))

    result = run_refresh()
    assert result["stats"]["rules"] == 1
    assert result["stats"]["facts"] == 1

    path = hot_path()
    assert path.exists()
    content = path.read_text()
    assert "<!-- agmem:hot" in content
    assert "Reuse existing modules" in content
    assert "S3 module path" in content


def test_run_refresh_excludes_index_facts(repo: Path):
    # Manual fact — should be in hot
    append_entry(create_entry("Manual fact about deployment", kind="fact"))
    # Index fact (auto-generated) — should NOT be in hot
    append_entry(create_entry(
        "Directory `src` contains 5 files",
        kind="fact",
        source="index",
        source_ref="src",
    ))
    run_refresh()
    text = read_hot() or ""
    assert "Manual fact about deployment" in text
    assert "Directory `src`" not in text


def test_read_hot_returns_none_if_no_cache(repo: Path):
    assert read_hot() is None


def test_render_hot_warns_when_rules_overflow_budget():
    # 10 long rules; budget intentionally tiny.
    rules = [create_entry(f"Rule {i}: " + "y" * 200, kind="rule") for i in range(10)]
    text, stats = render_hot(rules, [], [], budget_chars=300)
    assert stats["rules"] == 10  # all rules included regardless
    assert "exceeds" in text  # warning emitted
