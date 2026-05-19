"""Tests for the external aliases.yaml layer + multi-field BM25 boosts."""

import tempfile
from pathlib import Path

import pytest

from agmem.aliases import expand_query, load_user_aliases, merge_aliases
from agmem.search import _build_corpus_text, search
from agmem.store import MemoryEntry


# ---------- aliases.yaml loading ----------


def test_load_user_aliases_missing(tmp_path: Path):
    assert load_user_aliases(tmp_path) == {}


def test_load_user_aliases_simple(tmp_path: Path):
    (tmp_path / "aliases.yaml").write_text(
        "core:\n  - citadel-backend\n  - citadel_backend\n"
        "backend: dashboard-backend\n"
    )
    out = load_user_aliases(tmp_path)
    assert out["core"] == ["citadel-backend", "citadel_backend"]
    assert out["backend"] == ["dashboard-backend"]


def test_load_user_aliases_invalid_yaml(tmp_path: Path):
    (tmp_path / "aliases.yaml").write_text("not: valid: yaml: ::: {{")
    assert load_user_aliases(tmp_path) == {}


def test_load_user_aliases_wrong_shape(tmp_path: Path):
    (tmp_path / "aliases.yaml").write_text("- a\n- b\n")
    assert load_user_aliases(tmp_path) == {}


def test_merge_aliases_extends_not_replaces():
    builtin = {"queue": ["sqs"], "redis": ["elasticache"]}
    user = {"queue": ["rabbitmq"], "core": ["citadel-backend"]}
    merged = merge_aliases(builtin, user)
    assert "sqs" in merged["queue"]
    assert "rabbitmq" in merged["queue"]
    assert merged["redis"] == ["elasticache"]
    assert merged["core"] == ["citadel-backend"]


def test_merge_aliases_dedups():
    a = {"x": ["y", "z"]}
    b = {"x": ["y", "w"]}
    assert merge_aliases(a, b)["x"] == ["y", "z", "w"]


def test_expand_query_uses_supplied_aliases():
    aliases = {"core": ["citadel-backend"]}
    out = expand_query("how does core work", aliases=aliases)
    assert "citadel-backend" in out
    assert "core" in out


def test_expand_query_falls_back_to_builtin_when_none():
    out = expand_query("redis cluster")
    assert "elasticache" in out  # built-in alias


# ---------- structural BM25 boosts ----------


def _entry(text: str, source_ref: str | None = None, tags: list[str] | None = None) -> MemoryEntry:
    return MemoryEntry(
        id="01",
        ts="2026-05-09T00:00:00+00:00",
        text=text,
        tags=tags or [],
        source="index",
        source_ref=source_ref,
    )


def test_corpus_text_includes_text():
    e = _entry("Hello world")
    assert "Hello world" in _build_corpus_text(e)


def test_corpus_text_repeats_source_ref_for_boost():
    e = _entry("Some body.", source_ref="services/crawler.md")
    text = _build_corpus_text(e)
    assert text.count("services/crawler.md") >= 3


def test_corpus_text_extracts_basename_for_extra_boost():
    e = _entry("Some body.", source_ref="services/crawler.md")
    text = _build_corpus_text(e)
    # basename "crawler" added on top of repeated source_ref
    assert text.count("crawler") >= 4


def test_corpus_text_skips_readme_basename():
    """README is generic across projects — boosting its filename inflates noise."""
    e = _entry("# Project doc", source_ref="README.md")
    text = _build_corpus_text(e)
    # source_ref appears 3x but bare basename "README" should not be added 2x more
    assert text.lower().count("readme") <= 4


def test_corpus_text_extracts_markdown_title():
    e = _entry(
        'File `services/crawler.md` — Markdown doc — "crawler", 13 sections.',
        source_ref="services/crawler.md",
    )
    text = _build_corpus_text(e)
    # "crawler" appears in: text once, source_ref 3x, basename 2x, title 2x
    assert text.count("crawler") >= 7


def test_filename_boost_lifts_specific_file_over_long_doc():
    """Cardinal end-to-end check: a file whose name matches the query beats
    a long file that just mentions the query word once.

    Noise docs are added so BM25 has a realistic IDF — with a 2-doc corpus
    where both contain the query term, IDF goes negative, which inverts ranking.
    """
    crawler = _entry(
        text='File `services/crawler.md` — Markdown doc — "crawler", 13 sections.',
        source_ref="services/crawler.md",
    )
    long_readme = _entry(
        text=(
            "Project readme. File `README.md` — Markdown doc — \"truv-context\", "
            "15 sections. Items: title truv-context; section What This Is; "
            "section Setup; section How Cross-Repo Access Works; section How "
            "Slash Commands Work; subsection /new-provider — Scaffold a crawler "
            "provider; section Engineering Commands; subsection /debug-task; "
            "section Planning Commands; subsection /recap; section Notes."
        ),
        source_ref="README.md",
    )
    noise = [
        _entry(text=f"Unrelated content {i}.", source_ref=f"docs/{i}.md")
        for i in range(10)
    ]
    results = search("crawler", [crawler, long_readme, *noise], top_n=2)
    assert results[0][0].source_ref == "services/crawler.md"


def test_aliases_lift_aliased_doc_over_unrelated():
    """With the project alias core→citadel-backend, a query for 'core' must
    surface citadel-backend.md ahead of an unrelated doc."""
    backend = _entry(
        text='File `services/citadel-backend.md` — Markdown doc — "citadel_backend", 12 sections.',
        source_ref="services/citadel-backend.md",
    )
    other = _entry(
        text="Random doc that mentions Core in passing once.",
        source_ref="docs/random.md",
    )
    aliases = {"core": ["citadel-backend", "citadel_backend"]}
    results = search("core", [backend, other], top_n=2, aliases=aliases)
    assert results[0][0].source_ref == "services/citadel-backend.md"


def test_search_without_aliases_misses_renamed_concept():
    """Sanity check: without alias support, the same query falls through.
    This documents the value the alias layer provides."""
    backend = _entry(
        text='File `services/citadel-backend.md` — Markdown doc — "citadel_backend", 12 sections.',
        source_ref="services/citadel-backend.md",
    )
    results = search("core", [backend], top_n=1, aliases={})
    # No alias → token "core" doesn't appear in entry → score is 0.
    assert results[0][1] == pytest.approx(0.0)
