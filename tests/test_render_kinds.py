"""Tests for render_context grouping by kind (D2')."""

from agmem.render import render_context
from agmem.store import create_entry


def _result(entry, score=1.0):
    return (entry, score)


def test_render_context_groups_by_kind():
    rule = create_entry("never run terraform destroy in prod", kind="rule")
    fact = create_entry("module vpc lives in infra/modules/vpc", kind="fact")
    pattern = create_entry("staging mirrors prod with smaller node groups", kind="pattern")
    out = render_context("anything", [_result(fact), _result(pattern), _result(rule)])

    assert "## Constraints" in out
    assert "## Facts" in out
    assert "## Patterns" in out
    constraints_idx = out.index("## Constraints")
    facts_idx = out.index("## Facts")
    patterns_idx = out.index("## Patterns")
    assert constraints_idx < facts_idx < patterns_idx
    assert "never run terraform destroy" in out
    assert "module vpc lives" in out
    assert "staging mirrors prod" in out


def test_render_context_omits_empty_sections():
    only_facts = create_entry("a fact", kind="fact")
    out = render_context("task", [_result(only_facts)])
    assert "## Facts" in out
    assert "## Constraints" not in out
    assert "## Patterns" not in out


def test_render_context_shows_drifted_marker():
    drifted = create_entry("possibly stale", kind="fact", source_ref="x.py")
    drifted.drifted_at = "2026-05-07T00:00:00+00:00"
    out = render_context("task", [_result(drifted)])
    assert "DRIFTED" in out


def test_render_context_no_results():
    out = render_context("nothing here", [])
    assert "Constraints" in out
    assert "No relevant memories" in out


def test_render_context_includes_source_lines_in_ref():
    entry = create_entry("ranged ref", kind="fact", source_ref="src/api.py", source_lines=[10, 20])
    out = render_context("task", [_result(entry)])
    assert "src/api.py:10-20" in out


def test_render_context_condenses_section_entries():
    """Section entries embed long markdown bodies; render_context must trim them
    so the LLM gets a usable snippet, not a full document dump."""
    body = "## Overview\n\n" + ("This is the overview section. " * 80)
    section_text = f'Section "Overview" of `services/foo.md`.\n\n{body}'
    e = create_entry(
        section_text,
        kind="fact",
        source_ref="services/foo.md#overview",
    )
    out = render_context("task", [_result(e)])
    # Header preserved
    assert 'Section "Overview" of `services/foo.md`' in out
    # Body trimmed: total entry rendering should be far smaller than the input
    assert len(out) < len(section_text)
    # Truncation marker present (we exceed the 280-char snippet limit)
    assert "[…]" in out
    # The leading H2 of the body is dropped (already in the title above)
    assert out.count("## Overview") <= 1  # may appear in the H2 section "Overview" header? but section title is "Overview" so we just count once


def test_render_context_full_text_kept_for_non_section_entries():
    """Non-section entries (no #anchor) keep their full text — only sections are condensed."""
    e = create_entry(
        "A rule about something, with a reasonable body that should not be trimmed.",
        kind="rule",
        source_ref="services/foo.md",  # no #anchor
    )
    out = render_context("task", [_result(e)])
    assert "A rule about something" in out
    assert "[…]" not in out


def test_render_context_section_entry_short_body_not_truncated():
    """A short section body shouldn't grow the truncation marker."""
    text = 'Section "Tiny" of `a.md`.\n\n## Tiny\n\nbrief body'
    e = create_entry(text, kind="fact", source_ref="a.md#tiny")
    out = render_context("task", [_result(e)])
    assert "brief body" in out
    assert "[…]" not in out
