"""Markdown (.md, .mdx) parser — extract title, sections, ADR-style status, table density.

Also exposes :func:`split_sections` for the indexer, which splits a long markdown
file into one record per H2 section so retrieval can pinpoint the relevant
section instead of returning the whole document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .types import Block

_FRONTMATTER_DELIM = re.compile(r"^---\s*$")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*```")
_STATUS = re.compile(r"^\s*\*{0,2}\s*Status\s*\*{0,2}\s*[:—-]\s*(.+?)\s*$", re.IGNORECASE)
_LAST_UPDATED = re.compile(
    r"^\s*\*{0,2}\s*last[_\s-]updated\s*\*{0,2}\s*[:—-]\s*(.+?)\s*$",
    re.IGNORECASE,
)

_KNOWN_STATUSES = {
    "accepted", "proposed", "rejected", "superseded", "draft",
    "deprecated", "in", "complete", "completed", "active",
}

_MAX_SECTIONS = 30
_MAX_SUBSECTIONS = 15


@dataclass
class MdSection:
    """One H2-bounded slice of a markdown document."""

    title: str  # Heading text (without the leading ``##``)
    slug: str  # URL-safe form, used in ``source_ref#anchor``
    content: str  # Full body including the heading line
    subsection_titles: list[str] = field(default_factory=list)


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[*_`\[\]()]", "", s)
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]+", "", s)
    return s.strip("-")


def split_sections(content: str) -> tuple[str, list[MdSection]]:
    """Split a markdown document into a preamble plus H2 sections.

    The preamble is everything from the start (after frontmatter) up to the
    first H2 — typically the H1 title and any intro paragraph. Each subsequent
    H2 starts a new section that runs until the next H2 (or end of file).
    Headings inside fenced code blocks are ignored.

    Section slugs are deduplicated by suffixing ``-N`` if the same title repeats.
    """
    lines = content.splitlines(keepends=True)
    i = 0
    # Skip YAML frontmatter
    if lines and _FRONTMATTER_DELIM.match(lines[0].rstrip("\n")):
        i = 1
        while i < len(lines) and not _FRONTMATTER_DELIM.match(lines[i].rstrip("\n")):
            i += 1
        if i < len(lines):
            i += 1

    preamble_lines: list[str] = []
    sections: list[MdSection] = []
    current: MdSection | None = None
    in_code = False
    seen_slugs: dict[str, int] = {}

    def _emit(text: str) -> None:
        if current is None:
            preamble_lines.append(text)
        else:
            current.content += text

    for line in lines[i:]:
        stripped = line.rstrip("\n")
        if _FENCE.match(stripped):
            in_code = not in_code
            _emit(line)
            continue

        if not in_code:
            m = _HEADING.match(stripped)
            if m and len(m.group(1)) == 2:
                title = m.group(2).strip().rstrip("#").strip()
                base_slug = _slug(title) or "section"
                count = seen_slugs.get(base_slug, 0)
                seen_slugs[base_slug] = count + 1
                slug = base_slug if count == 0 else f"{base_slug}-{count + 1}"
                current = MdSection(title=title, slug=slug, content=line)
                sections.append(current)
                continue

            if m and len(m.group(1)) == 3 and current is not None:
                sub_title = m.group(2).strip().rstrip("#").strip()
                if sub_title:
                    current.subsection_titles.append(sub_title)

        _emit(line)

    # Sections store accumulated content — strip trailing blank space.
    for s in sections:
        s.content = s.content.rstrip() + "\n"

    return "".join(preamble_lines).rstrip() + ("\n" if preamble_lines else ""), sections


def analyze(content: str) -> list[Block]:
    blocks: list[Block] = []
    lines = content.splitlines()

    # Skip YAML frontmatter delimited by --- on the first line.
    i = 0
    if lines and _FRONTMATTER_DELIM.match(lines[0]):
        i = 1
        while i < len(lines) and not _FRONTMATTER_DELIM.match(lines[i]):
            i += 1
        if i < len(lines):
            i += 1

    h1_seen = False
    table_rows = 0
    in_code_block = False
    section_count = 0
    subsection_count = 0
    awaiting_status_body = False
    status_recorded = False

    for line in lines[i:]:
        if _FENCE.match(line):
            in_code_block = not in_code_block
            awaiting_status_body = False
            continue
        if in_code_block:
            continue

        m = _HEADING.match(line)
        if m:
            depth = len(m.group(1))
            title = m.group(2).strip().rstrip("#").strip()
            awaiting_status_body = False
            if not title:
                continue
            if depth == 1 and not h1_seen:
                blocks.append(Block(block_type="title", name=title))
                h1_seen = True
            elif depth == 2 and section_count < _MAX_SECTIONS:
                blocks.append(Block(block_type="section", name=title))
                section_count += 1
                if not status_recorded and title.lower().rstrip(":") == "status":
                    awaiting_status_body = True
            elif depth == 3 and subsection_count < _MAX_SUBSECTIONS:
                blocks.append(Block(block_type="subsection", name=title))
                subsection_count += 1
            continue

        m = _STATUS.match(line)
        if m:
            blocks.append(Block(block_type="status", name=m.group(1).strip()))
            status_recorded = True
            awaiting_status_body = False
            continue

        m = _LAST_UPDATED.match(line)
        if m:
            blocks.append(Block(block_type="last_updated", name=m.group(1).strip()))
            continue

        if awaiting_status_body and line.strip():
            blocks.append(Block(block_type="status", name=line.strip()))
            status_recorded = True
            awaiting_status_body = False
            continue

        if line.startswith("|") and line.count("|") >= 3:
            table_rows += 1

    if table_rows >= 5:
        blocks.append(Block(block_type="table", name=f"{table_rows} rows"))

    return blocks


def summary(blocks: list[Block]) -> str:
    title = next((b for b in blocks if b.block_type == "title"), None)
    section_count = sum(1 for b in blocks if b.block_type == "section")
    status = next((b for b in blocks if b.block_type == "status"), None)

    parts: list[str] = []
    if title:
        parts.append(f'"{title.name}"')
    if section_count:
        parts.append(f"{section_count} section{'s' if section_count != 1 else ''}")
    if status:
        parts.append(f"Status: {status.name}")

    return "Markdown doc" + (" — " + ", ".join(parts) if parts else "")


_ADR_STEM = re.compile(r"^(adr-\d+)")


def extract_tags(path: str, blocks: list[Block]) -> list[str]:
    tags: set[str] = {"doc"}

    parts = path.lower().split("/")
    for part in parts[:-1]:
        if part:
            tags.add(part)

    fname = parts[-1] if parts else ""
    fname_no_ext = fname.rsplit(".", 1)[0]
    adr_m = _ADR_STEM.match(fname_no_ext)
    if adr_m:
        tags.add("adr")
        tags.add(adr_m.group(1))

    for b in blocks:
        if b.block_type == "title":
            for tok in re.split(r"[\s/_\-:.]+", b.name.lower()):
                if len(tok) > 2 and tok.replace("-", "").isalnum():
                    tags.add(tok)
        elif b.block_type == "section":
            slug = re.sub(r"[\s/_]+", "-", b.name.lower()).strip("-")
            slug = re.sub(r"[^\w\-]", "", slug)
            if slug and 2 < len(slug) <= 30:
                tags.add(slug)
        elif b.block_type == "status":
            first = b.name.lower().split()[0].strip(".,;:!?") if b.name else ""
            if first in _KNOWN_STATUSES:
                tags.add(first)
                if first == "in":
                    tags.add("in-progress")

    return list(tags)
