"""Tests for `agmem ask` — learning-mode wrapper with session state."""

import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agmem.ask import (
    AskSession,
    SESSION_FILENAME,
    _collect_sibling_suggestions,
    _collect_tag_suggestions,
    _rerank_for_session,
    is_session_stale,
    load_session,
    reset_session,
    run_ask,
    save_session,
)
from agmem.store import MemoryEntry, append_entry, create_entry


def _patch_root(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    (root / ".agmem").mkdir(exist_ok=True)


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_root(monkeypatch, root)
    yield root


def _entry(text: str, ref: str | None = None, tags: list[str] | None = None) -> MemoryEntry:
    return create_entry(text=text, source_ref=ref, source="index", tags=tags or [])


# ---------- session I/O ----------


def test_load_session_missing_returns_none(repo: Path):
    assert load_session() is None


def test_save_and_load_roundtrip(repo: Path):
    s = AskSession(started_at="2026-05-09T00:00:00+00:00")
    s.seen_refs.append("a.md#x")
    s.seen_tags.append("hello")
    save_session(s)
    loaded = load_session()
    assert loaded is not None
    assert loaded.seen_refs == ["a.md#x"]
    assert loaded.seen_tags == ["hello"]


def test_reset_session_removes_file(repo: Path):
    save_session(AskSession(started_at="2026-05-09T00:00:00+00:00"))
    assert (repo / ".agmem" / SESSION_FILENAME).exists()
    assert reset_session() is True
    assert not (repo / ".agmem" / SESSION_FILENAME).exists()


def test_reset_session_when_missing_returns_false(repo: Path):
    assert reset_session() is False


def test_load_session_handles_corrupt_file(repo: Path):
    (repo / ".agmem" / SESSION_FILENAME).write_text("not valid json {{")
    assert load_session() is None


# ---------- staleness ----------


def test_session_with_no_queries_is_stale():
    s = AskSession(started_at="2026-05-09T00:00:00+00:00")
    assert is_session_stale(s) is True


def test_recent_session_is_fresh():
    s = AskSession(started_at="2026-05-09T00:00:00+00:00")
    s.queries.append(type("Q", (), {"q": "x", "ts": datetime.now(timezone.utc).isoformat(), "returned_refs": []})())
    # Use the dataclass directly for type safety
    from agmem.ask import AskQuery
    s.queries = [AskQuery(q="x", ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), returned_refs=[])]
    assert is_session_stale(s) is False


def test_old_session_is_stale():
    from agmem.ask import AskQuery
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    s = AskSession(started_at=old_ts)
    s.queries = [AskQuery(q="x", ts=old_ts, returned_refs=[])]
    assert is_session_stale(s, max_minutes=30) is True


# ---------- rerank logic ----------


def test_rerank_demotes_seen_entries():
    e1 = _entry("first", ref="a.md#x")
    e2 = _entry("second", ref="b.md#y")
    results = [(e1, 10.0), (e2, 5.0)]

    s = AskSession(started_at="x", seen_refs=["a.md#x"])
    out = _rerank_for_session(results, s)
    # e1 was 10, demoted to 3; e2 was 5 unchanged → e2 should win
    assert out[0][0] is e2
    assert out[1][0] is e1
    assert out[1][1] == pytest.approx(3.0)


def test_rerank_boosts_siblings_of_seen_files():
    e1 = _entry("seen section", ref="services/foo.md#a")
    e2 = _entry("sibling section", ref="services/foo.md#b")
    e3 = _entry("unrelated", ref="other.md#z")
    # Tied raw scores so the boost decides ordering.
    results = [(e3, 5.0), (e1, 5.0), (e2, 5.0)]

    s = AskSession(started_at="x", seen_refs=["services/foo.md#a"])
    out = _rerank_for_session(results, s)
    refs = [e.source_ref for e, _ in out]
    # e2 (sibling of seen, not yet seen) → 5 * 1.5 = 7.5 → top
    assert refs[0] == "services/foo.md#b"
    # e3 unrelated, untouched at 5 → middle
    assert refs[1] == "other.md#z"
    # e1 (already seen) → 5 * 0.3 = 1.5 → bottom
    assert refs[-1] == "services/foo.md#a"


def test_rerank_no_session_state_passes_through():
    """Empty session → no reordering. Caller is expected to pass already-ranked input."""
    e1 = _entry("a", ref="x.md")
    e2 = _entry("b", ref="y.md")
    sorted_input = [(e2, 2.0), (e1, 1.0)]
    out = _rerank_for_session(sorted_input, AskSession(started_at="x"))
    assert out == sorted_input


# ---------- suggestion collection ----------


def test_sibling_suggestions_picks_other_sections_of_shown_files():
    shown = _entry("seen", ref="services/crawler.md#overview")
    s1 = _entry("kraken", ref="services/crawler.md#kraken-framework")
    s2 = _entry("data flow", ref="services/crawler.md#data-flow")
    far = _entry("unrelated", ref="other.md#x")
    results = [(shown, 10.0), (s1, 5.0), (s2, 4.0), (far, 3.0)]

    out = _collect_sibling_suggestions(results, shown_n=1, seen_refs=set())
    refs = [e.source_ref for e in out]
    assert "services/crawler.md#kraken-framework" in refs
    assert "services/crawler.md#data-flow" in refs
    assert "other.md#x" not in refs


def test_sibling_suggestions_excludes_already_seen():
    shown = _entry("seen", ref="services/crawler.md#overview")
    s1 = _entry("kraken", ref="services/crawler.md#kraken-framework")
    results = [(shown, 10.0), (s1, 5.0)]
    out = _collect_sibling_suggestions(results, shown_n=1, seen_refs={"services/crawler.md#kraken-framework"})
    assert out == []


def test_sibling_suggestions_skips_non_section_entries():
    shown = _entry("seen", ref="services/crawler.md#overview")
    not_section = _entry("file-level", ref="other.md")
    results = [(shown, 10.0), (not_section, 5.0)]
    out = _collect_sibling_suggestions(results, shown_n=1, seen_refs=set())
    assert out == []


def test_tag_suggestions_filters_noise_and_seen():
    shown = _entry("a", tags=["index", "content", "py", "fastapi"])
    pool1 = _entry("b", tags=["index", "fastapi", "redis", "celery"])
    pool2 = _entry("c", tags=["index", "redis", "kafka"])
    results = [(shown, 10.0), (pool1, 5.0), (pool2, 4.0)]

    out = _collect_tag_suggestions(results, shown_n=1, seen_tags=set())
    keys = dict(out)
    assert "redis" in keys and keys["redis"] == 2
    assert "celery" in keys
    assert "kafka" in keys
    # noise filtered
    assert "index" not in keys
    assert "content" not in keys
    assert "py" not in keys
    # already in shown entry's tags → excluded
    assert "fastapi" not in keys


def test_tag_suggestions_respects_seen_tags_from_prior_queries():
    shown = _entry("a", tags=["index", "fastapi"])
    pool = _entry("b", tags=["index", "redis", "celery"])
    out = _collect_tag_suggestions([(shown, 10), (pool, 5)], shown_n=1, seen_tags={"redis"})
    keys = dict(out)
    assert "redis" not in keys
    assert "celery" in keys


# ---------- end-to-end run_ask ----------


def test_run_ask_creates_session_on_first_call(repo: Path):
    append_entry(create_entry("Bomber webhook delivery service", source_ref="services/bomber.md", kind="fact"))
    result = run_ask("bomber webhook")
    assert result.is_new_session is True
    assert len(result.top) >= 1
    s = load_session()
    assert s is not None
    assert len(s.queries) == 1
    assert s.queries[0].q == "bomber webhook"


def test_run_ask_continues_existing_session(repo: Path):
    append_entry(create_entry("Bomber service", source_ref="services/bomber.md", kind="fact"))
    append_entry(create_entry("Crawler service", source_ref="services/crawler.md", kind="fact"))
    run_ask("bomber")
    result = run_ask("crawler")
    assert result.is_new_session is False
    assert len(result.session.queries) == 2
    # seen_refs accumulated across both queries
    assert any("bomber" in r for r in result.session.seen_refs)
    assert any("crawler" in r for r in result.session.seen_refs)


def test_run_ask_new_flag_starts_fresh(repo: Path):
    append_entry(create_entry("Bomber service", source_ref="services/bomber.md", kind="fact"))
    run_ask("bomber")
    result = run_ask("bomber", new_session=True)
    assert result.is_new_session is True
    assert len(result.session.queries) == 1


def test_run_ask_demotes_already_seen_on_followup(repo: Path):
    """Without session continuity, the same entry would surface again. With it,
    a sibling outranks the already-seen one."""
    append_entry(create_entry(
        'File `services/crawler.md` — Markdown doc — "crawler", 4 sections.',
        source_ref="services/crawler.md", kind="fact",
    ))
    append_entry(create_entry(
        'Section "Overview" of `services/crawler.md`. The crawler scrapes data from payroll providers.',
        source_ref="services/crawler.md#overview", kind="fact",
    ))
    append_entry(create_entry(
        'Section "Kraken Framework" of `services/crawler.md`. Kraken: Portal -> Datasource -> Parser pattern for crawlers.',
        source_ref="services/crawler.md#kraken-framework", kind="fact",
    ))
    # First ask surfaces overview; followup should NOT return overview again at #1.
    first = run_ask("crawler", top_n=1)
    assert first.top
    seen_ref_first = first.top[0][0].source_ref

    second = run_ask("crawler", top_n=2)
    second_refs = [e.source_ref for e, _ in second.top]
    # The first-shown ref is demoted; it shouldn't lead the followup.
    assert second_refs[0] != seen_ref_first


def test_run_ask_returns_empty_gracefully(repo: Path):
    result = run_ask("anything")
    assert result.top == []
    assert result.error is None


def test_run_ask_session_persists_to_disk(repo: Path):
    append_entry(create_entry("Bomber service", source_ref="services/bomber.md", kind="fact"))
    run_ask("bomber")
    raw = json.loads((repo / ".agmem" / SESSION_FILENAME).read_text())
    assert raw["queries"][0]["q"] == "bomber"
    assert "services/bomber.md" in raw["seen_refs"]


def test_run_ask_tag_filter_restricts_results(repo: Path):
    """``run_ask(query, tag=X)`` should only return entries tagged X — even if
    other entries match the query better by BM25."""
    append_entry(create_entry(
        text="Mytruv account ID is 443110314663",
        source_ref="notes/mytruv.md", kind="fact",
        source="manual", tags=["mytruv", "aws"],
    ))
    append_entry(create_entry(
        text="Mytruv-related Truv-prod terraform shares modules from terraform/modules/aws/",
        source_ref="notes/modules.md", kind="fact",
        source="manual", tags=["modules"],
    ))
    # Query matches both, but only the first is tagged 'mytruv'.
    result = run_ask("mytruv terraform", tag="mytruv")
    returned = [e.id for e, _ in result.top]
    assert len(returned) == 1
    assert result.top[0][0].tags == ["mytruv", "aws"]


def test_run_ask_no_tag_returns_unfiltered(repo: Path):
    """Sanity check: without ``tag=``, both entries are eligible."""
    append_entry(create_entry(text="foo bar baz", tags=["x"]))
    append_entry(create_entry(text="foo bar qux", tags=["y"]))
    result = run_ask("foo bar")
    assert len(result.top) == 2
