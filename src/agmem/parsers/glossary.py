"""Glossary-driven alias extraction.

Scans markdown files that look like glossaries (by filename, by section header,
or by a dense run of two-column tables) and pulls candidate ``term -> synonym``
pairs from each row. The first column is the term; the head of the second
column contributes a few significant tokens as candidate synonyms.

The output is a draft. Users curate it via ``.agmem/aliases.yaml``; the
auto-generated file is ``.agmem/aliases.auto.yaml`` (search loads both).
"""

from __future__ import annotations

import re

# Tokens that trade off too low signal-to-noise to act as aliases. These are
# either very generic English ("thing", "way") or near-universal in tech docs
# ("system", "framework") so they'd cause false matches everywhere.
_GENERIC: set[str] = {
    "thing", "type", "kind", "way", "name", "value", "item", "items",
    "system", "systems", "framework", "platform", "tool", "tools",
    "library", "module", "code", "data", "file", "files", "list",
    "set", "based", "via", "etc", "also", "still", "default", "main",
    "primary", "secondary", "common", "typical", "important", "specific",
    "general", "other", "another", "same", "different", "various",
    "internal", "external", "public", "private", "shared",
    "open", "close", "open-source", "standard", "custom",
    "service", "services",  # too common in service docs
}

_MINI_STOP: set[str] = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "from", "with", "by", "as", "is", "are", "was", "were", "be",
    "been", "being", "has", "have", "had", "do", "does", "did", "this",
    "that", "these", "those", "it", "its", "into", "out", "across", "per",
    "if", "than", "when", "while", "until", "after", "before",
}

# Markdown table row: ``| col1 | col2 |``. Allow trailing whitespace.
_TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$")
# Divider that separates header from body in a markdown table.
_TABLE_DIVIDER = re.compile(r"^\s*\|[\s|:-]+\|\s*$")
_FENCE = re.compile(r"^\s*```")

_GLOSSARY_PATH_HINTS: tuple[str, ...] = (
    "glossary", "dictionary", "terms", "vocabulary", "acronyms",
)
_GLOSSARY_HEADER_RE = re.compile(
    r"^#{1,3}\s+("
    r"glossary|domain[\s_-]+terms?|acronyms?|terminology|"
    r"system[\s_-]+names?|common[\s_-]+terms?|abbreviations?"
    r")\s*:?\s*$",
    re.IGNORECASE,
)


def is_glossary_file(path: str, content: str) -> bool:
    """Return True if the file path or top-of-file headers suggest a glossary."""
    pl = path.lower()
    for hint in _GLOSSARY_PATH_HINTS:
        if hint in pl:
            return True
    for line in content.splitlines()[:80]:
        if _GLOSSARY_HEADER_RE.match(line):
            return True
    return False


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[*_`\[\]()]", "", s)
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]+", "", s)
    return s.strip("-")


_HEAD_BREAK = re.compile(r"\.|;|\(|—|\s--\s|\s-\s")


def _significant_tokens(meaning: str, max_n: int = 4) -> list[str]:
    """Pull up to ``max_n`` content tokens from the head of ``meaning``.

    The "head" is the prefix before the first sentence-end or aside marker
    so noisy material like trailing context, nested paren'd metadata, or a
    second sentence doesn't dilute the alias set.
    """
    cut = _HEAD_BREAK.search(meaning)
    head = meaning[: cut.start()] if cut else meaning
    head = re.sub(r"[*_`\[\]]", " ", head)
    raw = re.findall(r"[A-Za-z][A-Za-z0-9_-]+", head.lower())
    out: list[str] = []
    for t in raw:
        if len(t) < 4:
            continue
        if t in _MINI_STOP or t in _GENERIC:
            continue
        if t not in out:
            out.append(t)
        if len(out) >= max_n:
            break
    return out


def extract_aliases(content: str) -> dict[str, list[str]]:
    """Return ``{term-slug: [alias-slugs]}`` from glossary-style tables in ``content``.

    Only rows that follow a header divider (``| --- | --- |``) are accepted;
    bare two-column lines with no divider are treated as prose, not tables.
    Code fences are skipped.
    """
    result: dict[str, list[str]] = {}
    in_code = False
    in_table_body = False

    for line in content.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
            in_table_body = False
            continue
        if in_code:
            continue

        if _TABLE_DIVIDER.match(line):
            in_table_body = True
            continue

        if not line.lstrip().startswith("|"):
            in_table_body = False
            continue

        if not in_table_body:
            continue  # likely the header row, skip until divider

        m = _TABLE_ROW.match(line)
        if not m:
            continue

        term, meaning = m.group(1).strip(), m.group(2).strip()
        if not term or not meaning:
            continue
        if not re.search(r"[A-Za-z]", term):
            continue

        term_slug = _slug(term)
        if not term_slug or len(term_slug) > 40:
            continue

        synonyms = _significant_tokens(meaning, max_n=4)
        synonyms = [s for s in synonyms if s != term_slug]
        if not synonyms:
            continue

        existing = result.setdefault(term_slug, [])
        for s in synonyms:
            if s not in existing:
                existing.append(s)

    return result
