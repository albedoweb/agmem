"""Python (.py) parser — top-level classes, functions, FastAPI-style routes."""

from __future__ import annotations

import re

from .types import Block

_PY_CLASS = re.compile(r'^class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:')
_PY_FUNC = re.compile(r'^(async\s+)?def\s+(\w+)\s*\(')
_PY_ROUTE = re.compile(
    r'^\s*@(?:router|app|sub_router|api_router)'
    r'\s*\.\s*(?P<method>get|post|put|patch|delete|head|options)'
    r'\s*\(\s*["\'](?P<path>[^"\']+)["\']'
)
# Triple-quoted docstring opener, optionally with a string prefix like r/b/u.
_PY_DOC_OPEN = re.compile(r'^\s*(?:[rRbBuU]{1,2})?("""|\'\'\')(.*)$')
_DOC_MAX_CHARS = 80


def _next_docstring(lines: list[str], start_idx: int, max_lookahead: int = 3) -> str:
    """Return the first line of the docstring opening at or near ``start_idx``,
    or empty string if there's no docstring. Stops at the first non-blank
    non-docstring line (so we don't accidentally match a far-down literal)."""
    for j in range(start_idx, min(start_idx + max_lookahead, len(lines))):
        raw = lines[j]
        if not raw.strip():
            continue
        m = _PY_DOC_OPEN.match(raw)
        if not m:
            return ""
        opener, rest = m.group(1), m.group(2)
        close_idx = rest.find(opener)
        if close_idx >= 0:                       # one-line docstring: """foo"""
            doc = rest[:close_idx].strip()
        else:                                    # multi-line: take the opening line
            doc = rest.strip()
            # If the opener is on its own line, the *next* non-blank line is
            # often the summary (PEP 257 multi-line convention).
            if not doc:
                for k in range(j + 1, min(j + 4, len(lines))):
                    s = lines[k].strip()
                    if s and s != opener:
                        doc = s
                        break
        return doc[:_DOC_MAX_CHARS]
    return ""


def analyze(content: str) -> list[Block]:
    blocks: list[Block] = []
    pending_route: tuple[str, str] | None = None
    lines = content.splitlines()

    for i, line in enumerate(lines):
        route_match = _PY_ROUTE.match(line)
        if route_match:
            pending_route = (
                route_match.group("method").upper(),
                route_match.group("path"),
            )
            continue

        func_match = _PY_FUNC.match(line)
        if func_match:
            name = func_match.group(2)
            doc = _next_docstring(lines, i + 1)
            if pending_route is not None:
                method, path = pending_route
                blocks.append(Block(
                    block_type="route",
                    name=name,
                    labels=[method, path],
                    doc=doc,
                ))
                pending_route = None
            else:
                blocks.append(Block(block_type="function", name=name, doc=doc))
            continue

        class_match = _PY_CLASS.match(line)
        if class_match:
            name = class_match.group(1)
            bases_str = (class_match.group(2) or "").strip()
            if bases_str:
                bases_raw = [b.strip() for b in bases_str.split(",") if b.strip()]
                bases = []
                for b in bases_raw:
                    b = re.sub(r"\[.*\]", "", b)
                    if "=" in b:
                        b = b.split("=", 1)[1].strip()
                    if b:
                        bases.append(b)
            else:
                bases = []
            doc = _next_docstring(lines, i + 1)
            blocks.append(Block(
                block_type="class",
                name=name,
                labels=bases,
                doc=doc,
            ))
            pending_route = None
            continue

        if line.strip() and not line.lstrip().startswith("@"):
            pending_route = None

    return blocks


def summary(blocks: list[Block]) -> str:
    kind_counts: dict[str, int] = {}
    for b in blocks:
        kind_counts[b.block_type] = kind_counts.get(b.block_type, 0) + 1
    bits: list[str] = []
    for kind in ("route", "class", "function"):
        n = kind_counts.get(kind, 0)
        if n:
            bits.append(f"{n} {kind}{'s' if n != 1 else ''}")
    return "Python file with " + ", ".join(bits) if bits else "Python file"


def extract_tags(path: str, blocks: list[Block]) -> list[str]:  # noqa: ARG001
    tags: set[str] = set()
    for b in blocks:
        tags.add(b.block_type)
        if b.block_type == "route" and len(b.labels) == 2:
            method, route_path = b.labels
            tags.add(method.lower())
            tags.add("api")
            for part in route_path.split("/"):
                cleaned = part.strip("{}").lower()
                if cleaned and len(cleaned) > 1 and not cleaned.startswith("v"):
                    tags.add(cleaned)
                if cleaned.startswith("v") and cleaned[1:].isdigit():
                    tags.add(cleaned)
        elif b.block_type == "class":
            for base in b.labels:
                t = base.lower().strip()
                if not t:
                    continue
                tags.add(t)
                if t in ("document", "indexed"):
                    tags.add("model")
                    tags.add("mongodb")
                if t in ("basemodel", "rootmodel"):
                    tags.add("schema")
                    tags.add("pydantic")
                if t == "strenum":
                    tags.add("enum")
        elif b.block_type == "function":
            if b.name.lower().startswith("test_"):
                tags.add("test")
    return list(tags)
