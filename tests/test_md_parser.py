"""Tests for the markdown parser and parsers package config layer."""

from pathlib import Path

from agmem.parsers import analyze_file, extract_tags_for_file, registered_extensions
from agmem.parsers.config import load
from agmem.parsers.md import analyze, extract_tags, split_sections, summary


ADR_DOC = """# ADR-018: Prod Change Audit Pipeline

last_updated: 2026-04-17

## Status
Accepted — Phase 2a complete.

## Context

CloudTrail records every AWS API call. EventBridge integrates with CloudTrail.

### What already exists

- CloudTrail org-wide
- EKS audit logs

## Decision

Use EventBridge + EKS Audit Log Subscription Filters.

```hcl
# This heading shouldn't count: ## Inside code fence
resource "aws_cloudwatch_event_rule" "x" {}
```

## Consequences

Coverage of all prod API changes.
"""

GLOSSARY_DOC = """# Glossary

last_updated: 2026-03-04

## Domain Terms

| Term | Meaning |
|------|---------|
| Bridge | JS SDK |
| Token | Short-lived |
| Link | A connection |
| Task | A job |
| Order | A request |

## Acronyms

| Acronym | Expansion |
|---------|-----------|
| FSD | Feature-Sliced Design |
"""


def test_analyze_extracts_h1_h2_h3():
    blocks = analyze(ADR_DOC)
    titles = [b for b in blocks if b.block_type == "title"]
    sections = [b for b in blocks if b.block_type == "section"]
    subsections = [b for b in blocks if b.block_type == "subsection"]
    assert [t.name for t in titles] == ["ADR-018: Prod Change Audit Pipeline"]
    section_names = [s.name for s in sections]
    assert "Status" in section_names
    assert "Context" in section_names
    assert "Decision" in section_names
    assert "Consequences" in section_names
    assert any(s.name == "What already exists" for s in subsections)


def test_analyze_skips_headings_inside_code_fences():
    blocks = analyze(ADR_DOC)
    section_names = [b.name for b in blocks if b.block_type == "section"]
    assert "Inside code fence" not in section_names


def test_analyze_captures_status_and_last_updated():
    blocks = analyze(ADR_DOC)
    statuses = [b for b in blocks if b.block_type == "status"]
    updated = [b for b in blocks if b.block_type == "last_updated"]
    assert statuses and statuses[0].name.startswith("Accepted")
    assert updated and "2026-04-17" in updated[0].name


def test_table_density_signal():
    blocks = analyze(GLOSSARY_DOC)
    tables = [b for b in blocks if b.block_type == "table"]
    assert tables, "should detect glossary-style table"


def test_frontmatter_skipped():
    doc = """---
title: Some doc
status: draft
---

# Real Title

## Real Section
"""
    blocks = analyze(doc)
    assert any(b.block_type == "title" and b.name == "Real Title" for b in blocks)


def test_summary_includes_title_sections_status():
    blocks = analyze(ADR_DOC)
    s = summary(blocks)
    assert '"ADR-018: Prod Change Audit Pipeline"' in s
    assert "section" in s
    assert "Status" in s


def test_extract_tags_path_and_adr():
    blocks = analyze(ADR_DOC)
    tags = extract_tags("decisions/adr-018-prod-change-audit.md", blocks)
    assert "doc" in tags
    assert "adr" in tags
    assert "adr-018" in tags
    assert "decisions" in tags
    assert "accepted" in tags
    # Title tokens
    assert "prod" in tags or "audit" in tags or "change" in tags
    # Section slugs
    assert "context" in tags or "decision" in tags


def test_analyze_file_dispatches_to_md():
    a = analyze_file("docs/foo.md", "# Hello\n\n## World\n")
    assert a is not None
    assert a.ext == "md"
    assert any(b.block_type == "title" for b in a.blocks)


def test_analyze_file_mdx_alias():
    a = analyze_file("docs/foo.mdx", "# Hello\n\n## World\n")
    assert a is not None
    assert a.ext == "mdx"


def test_analyze_file_returns_none_for_empty_md():
    assert analyze_file("readme.md", "Just paragraph text, no headings.") is None


def test_extract_tags_for_file_md_uses_md_extractor():
    blocks = analyze("# Hi\n\n## Things\n")
    tags = extract_tags_for_file("notes/note.md", blocks)
    assert "doc" in tags
    assert "notes" in tags


def test_registered_extensions_includes_builtins():
    exts = registered_extensions()
    assert "tf" in exts
    assert "py" in exts
    assert "md" in exts
    assert "mdx" in exts


# ---------- external config (.agmem/parsers.yaml) ----------


def test_config_missing_returns_empty(tmp_path: Path):
    cfg = load(tmp_path)
    assert cfg.disabled_parsers == set()
    assert cfg.key_files == {}
    assert cfg.error is None


def test_config_disabled_parsers(tmp_path: Path):
    (tmp_path / "parsers.yaml").write_text(
        "disabled_parsers: [md, py]\n"
    )
    cfg = load(tmp_path)
    assert cfg.disabled_parsers == {"md", "py"}


def test_config_disable_excludes_from_registered_extensions(tmp_path: Path):
    (tmp_path / "parsers.yaml").write_text("disabled_parsers: [md]\n")
    cfg = load(tmp_path)
    exts = registered_extensions(cfg)
    assert "md" not in exts
    assert "tf" in exts


def test_config_disable_makes_analyze_file_skip(tmp_path: Path):
    (tmp_path / "parsers.yaml").write_text("disabled_parsers: [md]\n")
    cfg = load(tmp_path)
    assert analyze_file("a.md", "# Hi\n## There\n", cfg) is None


def test_config_key_files_extends(tmp_path: Path):
    (tmp_path / "parsers.yaml").write_text(
        "key_files:\n  Procfile: Heroku procfile\n"
    )
    cfg = load(tmp_path)
    assert cfg.key_files == {"Procfile": "Heroku procfile"}


def test_config_extension_labels_normalizes_dot(tmp_path: Path):
    (tmp_path / "parsers.yaml").write_text(
        'extension_labels:\n  graphql: GraphQL schema\n  ".proto": Protobuf\n'
    )
    cfg = load(tmp_path)
    assert cfg.extension_labels == {".graphql": "GraphQL schema", ".proto": "Protobuf"}


def test_config_invalid_yaml_records_error(tmp_path: Path):
    (tmp_path / "parsers.yaml").write_text("not: valid: yaml: ::: {{")
    cfg = load(tmp_path)
    assert cfg.error is not None


def test_config_top_level_must_be_mapping(tmp_path: Path):
    (tmp_path / "parsers.yaml").write_text("- just a list\n")
    cfg = load(tmp_path)
    assert cfg.error is not None
    assert "mapping" in cfg.error.lower()


# ---------- split_sections (L2) ----------


def test_split_sections_basic_h2_split():
    doc = """# Title

intro paragraph

## Overview

overview body

## Stack

stack body

### Subitem

sub body
"""
    preamble, sections = split_sections(doc)
    assert "intro paragraph" in preamble
    assert [s.title for s in sections] == ["Overview", "Stack"]
    assert "overview body" in sections[0].content
    assert "stack body" in sections[1].content
    assert "Subitem" in sections[1].subsection_titles


def test_split_sections_skips_frontmatter():
    doc = """---
title: foo
---

# Real

## Section A

body
"""
    preamble, sections = split_sections(doc)
    assert "title: foo" not in preamble
    assert "# Real" in preamble
    assert sections[0].title == "Section A"


def test_split_sections_ignores_headings_in_code_fence():
    doc = """# Title

## Real Section

```
## Fake heading inside code
```

body after fence
"""
    _, sections = split_sections(doc)
    assert [s.title for s in sections] == ["Real Section"]
    assert "Fake heading inside code" in sections[0].content


def test_split_sections_dedupes_repeated_slugs():
    doc = """# T

## Same

a

## Same

b
"""
    _, sections = split_sections(doc)
    assert sections[0].slug == "same"
    assert sections[1].slug == "same-2"


def test_split_sections_includes_h2_heading_in_content():
    doc = "# Title\n\n## A\n\nbody\n"
    _, sections = split_sections(doc)
    assert sections[0].content.startswith("## A")
    assert "body" in sections[0].content


def test_split_sections_no_h2_returns_no_sections():
    doc = "# Title\n\njust a paragraph"
    preamble, sections = split_sections(doc)
    assert sections == []
    assert "just a paragraph" in preamble
