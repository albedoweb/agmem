"""Indexer-level tests for markdown section splitting (L2)."""

import os
import tempfile
from pathlib import Path

import pytest

from agmem.indexer import run_index
from agmem.store import read_all_entries


def _patch(monkeypatch, root: Path):
    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr(
        "agmem.config.agmem_dir",
        lambda cwd=None: root / ".agmem",
    )
    monkeypatch.setattr(
        "agmem.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    (root / ".agmem").mkdir(exist_ok=True)


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch(monkeypatch, root)
    yield root


def test_long_md_splits_into_sections(repo: Path):
    """A markdown file with >= 4 H2 sections and > 1500 bytes should yield
    one master entry plus one entry per H2 section."""
    body = "# Bomber\n\n"
    for i, name in enumerate(["Overview", "Stack", "Processing Flow", "Auth Methods", "Failure Modes"]):
        body += f"## {name}\n\n"
        body += f"This is the {name.lower()} section. " * 30 + "\n\n"

    (repo / "services").mkdir()
    (repo / "services" / "bomber.md").write_text(body, encoding="utf-8")

    run_index(str(repo))
    entries = read_all_entries(str(repo))

    bomber_entries = [e for e in entries if e.source_ref and "bomber.md" in e.source_ref]
    refs = [e.source_ref for e in bomber_entries]
    # Master + 5 sections
    assert len([e for e in bomber_entries if e.source_ref == "services/bomber.md"]) == 1
    section_refs = [r for r in refs if r and "#" in r]
    assert len(section_refs) == 5
    # Section slugs
    slugs = [r.split("#", 1)[1] for r in section_refs]
    assert "overview" in slugs
    assert "processing-flow" in slugs


def test_short_md_stays_single_entry(repo: Path):
    """A small file under the threshold stays as a single entry."""
    body = "# Tiny\n\n## A\n\nshort\n\n## B\n\nshort\n"
    (repo / "tiny.md").write_text(body, encoding="utf-8")

    run_index(str(repo))
    entries = read_all_entries(str(repo))
    tiny = [e for e in entries if e.source_ref and "tiny.md" in (e.source_ref or "")]
    # Only one entry; no #anchor refs
    assert all(e.source_ref == "tiny.md" for e in tiny if e.source_ref)
    assert not any("#" in (e.source_ref or "") for e in tiny)


def test_section_entry_text_carries_body_content(repo: Path):
    """The whole point of L2 — section entries embed real body text so BM25
    can match on words that only appear inside a section."""
    body = (
        "# Crawler\n\n"
        "## Overview\n\nThe crawler logs into providers and scrapes data.\n\n"
        "## Kraken Framework\n\n"
        "Kraken is a declarative framework: Portal -> Datasource -> Parser. "
        + ("Detailed kraken text. " * 30)
        + "\n\n"
        "## Celery Queues\n\nWe use celery with Redis broker.\n\n"
        "## Proxy Architecture\n\n"
        + ("Proxies route traffic via residential IPs. " * 30)
    )
    (repo / "crawler.md").write_text(body, encoding="utf-8")

    run_index(str(repo))
    entries = read_all_entries(str(repo))

    kraken = next(
        (e for e in entries if e.source_ref == "crawler.md#kraken-framework"),
        None,
    )
    assert kraken is not None
    assert "Portal" in kraken.text and "Datasource" in kraken.text


def test_section_entries_get_stable_ids(repo: Path):
    """Same file content → same stable IDs after reindex (verified_at preserved)."""
    body = "# T\n\n"
    for name in ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]:
        body += f"## {name}\n\n" + (f"Body of {name}. " * 30) + "\n\n"
    (repo / "doc.md").write_text(body, encoding="utf-8")

    run_index(str(repo))
    first = {e.source_ref: e.id for e in read_all_entries(str(repo))
             if e.source_ref and e.source_ref.startswith("doc.md")}

    run_index(str(repo))
    second = {e.source_ref: e.id for e in read_all_entries(str(repo))
              if e.source_ref and e.source_ref.startswith("doc.md")}

    assert first == second
    assert len(first) >= 6  # master + 5 sections


def test_section_entries_have_section_tag(repo: Path):
    body = "# T\n\n"
    for name in ["A", "B", "C", "D"]:
        body += f"## {name}\n\n" + (f"body of {name}. " * 50) + "\n\n"
    (repo / "doc.md").write_text(body, encoding="utf-8")
    run_index(str(repo))
    entries = read_all_entries(str(repo))
    sections = [e for e in entries if e.source_ref and "#" in (e.source_ref or "")]
    assert sections, "expected section entries"
    for e in sections:
        assert "section" in e.tags
    masters = [e for e in entries if e.source_ref == "doc.md"]
    assert masters and "section-master" in masters[0].tags
