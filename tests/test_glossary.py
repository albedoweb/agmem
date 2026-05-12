"""Tests for parsers.glossary alias extraction."""

from agmem.parsers.glossary import extract_aliases, is_glossary_file


GLOSSARY_DOC = """# Glossary

last_updated: 2026-03-04

## Domain Terms

| Term | Meaning |
|------|---------|
| Bridge | JavaScript SDK embedded in customer apps to launch verification widget |
| Bridge Token | Short-lived token authorizing a widget session |
| Link | A connection between a user and a payroll/bank provider |
| Order | A verification request that may contain multiple SubOrders |
| VOE | Verification of Employment |
| VOIE | Verification of Income and Employment |

## System Names

| Name | What It Is |
|------|------------|
| Citadel | Legacy brand name (pre-Truv). Still used in repo names |
| Bomber | Webhook delivery service (Go). Name refers to bulk HTTP delivery |
| Steward | Slack-based deploy bot (truvhq/steward). Manages approval workflows |

## Acronyms

| Acronym | Expansion |
|---------|-----------|
| MCP | Model Context Protocol — open standard for LLM tool integration |
| FSD | Feature-Sliced Design (dashboard_frontend architecture) |
| IRSA | IAM Roles for Service Accounts |
"""


def test_is_glossary_file_by_filename():
    assert is_glossary_file("docs/glossary.md", "")
    assert is_glossary_file("internal/terms.md", "")
    assert is_glossary_file("acronyms.md", "")
    assert is_glossary_file("dictionary/index.md", "")


def test_is_glossary_file_by_header():
    assert is_glossary_file("README.md", "# Project\n\n## Glossary\n\n| Term | Meaning |\n")
    assert is_glossary_file("foo.md", "## Domain Terms\n")
    assert is_glossary_file("foo.md", "## Acronyms\n")


def test_is_glossary_file_negative():
    assert not is_glossary_file("services/crawler.md", "# crawler\n\n## Overview\n\nDoes things.\n")


def test_extract_aliases_pulls_first_few_tokens():
    out = extract_aliases(GLOSSARY_DOC)
    assert "bridge" in out
    # 'JavaScript SDK embedded in customer apps' → first content tokens
    assert "javascript" in out["bridge"]
    assert "widget" in out["bridge"] or "embedded" in out["bridge"]


def test_extract_aliases_acronym_expansion():
    out = extract_aliases(GLOSSARY_DOC)
    # MCP slug becomes "mcp"
    assert "mcp" in out
    # Expansion 'Model Context Protocol' → tokens
    assert "model" in out["mcp"]
    assert "context" in out["mcp"]
    assert "protocol" in out["mcp"]


def test_extract_aliases_compound_term_slug():
    out = extract_aliases(GLOSSARY_DOC)
    assert "bridge-token" in out
    assert "voie" in out


def test_extract_aliases_skips_self_alias():
    out = extract_aliases("""
| Foo | Foo bar baz |
|-----|-------------|
| Foo | Foo something else |
""")
    # 'foo' shouldn't appear as its own synonym
    if "foo" in out:
        assert "foo" not in out["foo"]


def test_extract_aliases_drops_generic_words():
    out = extract_aliases("""
| Term | Meaning |
|------|---------|
| Bomber | Webhook delivery service (Go) |
""")
    # 'service' is in our generic block-list
    assert "service" not in out.get("bomber", [])


def test_extract_aliases_ignores_code_fence_tables():
    content = """
## Domain Terms

```markdown
| Term | Meaning |
|------|---------|
| Foo | bar baz |
```
"""
    assert extract_aliases(content) == {}


def test_extract_aliases_requires_divider():
    """A bare two-column line without a `| --- | --- |` divider above shouldn't
    be misread as a table row (avoids false positives on inline pipes in prose)."""
    content = "Some prose | with a pipe | in it.\n"
    assert extract_aliases(content) == {}


def test_extract_aliases_handles_truv_glossary_shape():
    """End-to-end check on the real shape of truv-context's glossary.md."""
    out = extract_aliases(GLOSSARY_DOC)
    # Expect concept extraction for the well-known terms
    assert "voe" in out
    assert "employment" in out["voe"]
    assert "bomber" in out
    assert any(s in out["bomber"] for s in ("webhook", "delivery", "bulk"))
    assert "steward" in out
    assert any(s in out["steward"] for s in ("slack-based", "deploy", "approval"))
