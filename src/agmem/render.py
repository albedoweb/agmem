"""Markdown and JSON rendering for recall/context output."""

import json
import re
from datetime import datetime

from .store import MemoryEntry

# Section entries embed their full markdown body in ``text``. For ``context``
# output (fed to an LLM), we keep just a tight snippet so the budget is sane.
# ``recall`` is for human/debugging use and renders the full body.
_CONTEXT_SECTION_BODY_LIMIT = 280

_LEADING_SECTION_PREFIX = re.compile(
    r'^Section "([^"]+)" of `([^`]+)`\.\s*'
    r'(?:Subsections:[^.]*\.\s*)?',
)


def _format_ref(entry: MemoryEntry) -> str | None:
    if not entry.source_ref:
        return None
    if entry.source_lines and len(entry.source_lines) == 2:
        return f"{entry.source_ref}:{entry.source_lines[0]}-{entry.source_lines[1]}"
    return entry.source_ref


def _format_entry_md(index: int, entry: MemoryEntry, score: float | None = None) -> str:
    ts = datetime.fromisoformat(entry.ts)
    date_str = ts.strftime("%Y-%m-%d")
    lines = [
        f"{index}. {entry.text}",
        f"   - id: {entry.id}",
        f"   - kind: {entry.kind}",
    ]
    if entry.tags:
        lines.append(f"   - tags: {', '.join(entry.tags)}")
    lines.append(f"   - source: {entry.source}")
    ref = _format_ref(entry)
    if ref:
        lines.append(f"   - source_ref: {ref}")
    if entry.source_commit:
        lines.append(f"   - commit: {entry.source_commit[:12]}")
    if entry.drifted_at:
        lines.append(f"   - drifted: {entry.drifted_at[:10]}")
    lines.append(f"   - {date_str}")
    if score is not None:
        lines.append(f"   - score: {score:.4f}")
    return "\n".join(lines)


def render_recall(
    query: str,
    results: list[tuple[MemoryEntry, float]],
    json_mode: bool = False,
) -> str:
    if json_mode:
        output = []
        for entry, score in results:
            d = entry.to_dict()
            d["score"] = round(score, 4)
            output.append(d)
        return json.dumps(output, ensure_ascii=False, indent=2)

    if not results:
        return f"# Recall: {query}\n\nNo memories found.\n"

    lines = [f"# Recall: {query}", ""]
    for i, (entry, score) in enumerate(results, 1):
        lines.append(_format_entry_md(i, entry, score))
        lines.append("")
    return "\n".join(lines)


KIND_SECTION_TITLES: dict[str, str] = {
    "rule": "Constraints",
    "fact": "Facts",
    "pattern": "Patterns",
}
KIND_RENDER_ORDER: tuple[str, ...] = ("rule", "fact", "pattern")


def _condense_section_text(text: str) -> str:
    """For section entries, drop the boilerplate header and trim the body.

    Section ``text`` looks like::

        Section "Foo" of `path`. Subsections: a; b.

        ## Foo

        body...

    We keep the first line (title + path) plus a trimmed snippet of the body,
    so the LLM sees what the section is about without the full markdown dump.
    """
    m = _LEADING_SECTION_PREFIX.match(text)
    if not m:
        return text
    title, path = m.group(1), m.group(2)
    body = text[m.end():]
    # Strip the body's own H2/H3 heading line (it duplicates the title above).
    body = re.sub(r"^\s*#{2,3}\s+.+\n+", "", body, count=1)
    body = body.strip()
    if len(body) > _CONTEXT_SECTION_BODY_LIMIT:
        body = body[:_CONTEXT_SECTION_BODY_LIMIT].rstrip() + " […]"
    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return f'Section "{title}" of `{path}`.'
    return f'Section "{title}" of `{path}` — {body}'


def _format_context_entry(entry: MemoryEntry) -> list[str]:
    ts = datetime.fromisoformat(entry.ts)
    date_str = ts.strftime("%Y-%m-%d")
    is_section = bool(entry.source_ref) and "#" in (entry.source_ref or "")
    text = _condense_section_text(entry.text) if is_section else entry.text
    out = [f"- {text}"]
    meta_bits = [f"{entry.source}", date_str]
    ref = _format_ref(entry)
    if ref:
        meta_bits.append(f"ref: {ref}")
    if entry.source_commit:
        meta_bits.append(f"commit: {entry.source_commit[:12]}")
    if entry.drifted_at:
        meta_bits.append("DRIFTED")
    out.append(f"  ({' · '.join(meta_bits)})")
    return out


def render_context(
    task: str,
    results: list[tuple[MemoryEntry, float]],
    json_mode: bool = False,
) -> str:
    if json_mode:
        output = []
        for entry, score in results:
            d = entry.to_dict()
            d["score"] = round(score, 4)
            output.append(d)
        return json.dumps(output, ensure_ascii=False, indent=2)

    if not results:
        return (
            f"# Context for: {task}\n\n## Constraints\n\nNo relevant memories found.\n\n"
            f"## Hint\n\nConsider adding context with `agmem remember` for future sessions.\n"
        )

    grouped: dict[str, list[MemoryEntry]] = {k: [] for k in KIND_RENDER_ORDER}
    for entry, _ in results:
        grouped.setdefault(entry.kind, []).append(entry)

    lines = [f"# Context for: {task}", ""]
    rendered_any = False
    for kind in KIND_RENDER_ORDER:
        items = grouped.get(kind, [])
        if not items:
            continue
        rendered_any = True
        lines.append(f"## {KIND_SECTION_TITLES[kind]}")
        lines.append("")
        for entry in items:
            lines.extend(_format_context_entry(entry))
        lines.append("")

    if not rendered_any:
        lines.append("No relevant memories found.")
        lines.append("")

    lines.extend([
        "## Hint",
        "",
        "Constraints are project rules — do not contradict without explicit user override.",
        "Facts and patterns are observations — verify before acting on them.",
        "",
    ])
    return "\n".join(lines)
