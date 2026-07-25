"""YAML (.yaml/.yml) parser — flattens a document into dotted key-paths.

Each scalar leaf becomes a ``key`` block named by its dotted path
(``storefront.env.FEATURE_INSIGHTS``) with the value as a label, so BM25 can match on
both the key identifiers and the value. Works for any YAML — Helm
``values.yaml``, k8s manifests, CI configs, docker-compose. Multi-document
streams (``---`` separated) are each walked. List items are collapsed: the
parent key is the path and numeric indices are dropped, which keeps the paths
readable and searchable (``spec.containers.image`` rather than
``spec.containers.0.image``).

Naming note: this module is ``agmem.parsers.yaml``; the bare ``import yaml``
below resolves to top-level PyYAML (absolute import), not to this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .types import Block

_MAX_DEPTH = 6
_MAX_BLOCKS = 80
_VALUE_CHARS = 60


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).strip().replace("\n", " ")[:_VALUE_CHARS]


def _walk(node: Any, prefix: str, out: list[tuple[str, str]], depth: int) -> None:
    if depth > _MAX_DEPTH or len(out) >= _MAX_BLOCKS:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if len(out) >= _MAX_BLOCKS:
                return
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                _walk(v, path, out, depth + 1)
            elif isinstance(v, list):
                if any(isinstance(i, (dict, list)) for i in v):
                    _walk(v, path, out, depth + 1)
                else:
                    joined = ", ".join(_stringify(i) for i in v if i is not None)
                    out.append((path, joined))
            else:
                out.append((path, _stringify(v)))
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _walk(item, prefix, out, depth + 1)


def analyze(content: str) -> list[Block]:
    try:
        docs = list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return []
    blocks: list[Block] = []
    for doc in docs:
        if not isinstance(doc, (dict, list)):
            continue
        leaves: list[tuple[str, str]] = []
        _walk(doc, "", leaves, 0)
        # Dedupe within a document (collapsed list items repeat the same path);
        # keep cross-document repeats — separate k8s resources share key names.
        seen: set[str] = set()
        for path, value in leaves:
            if path in seen or len(blocks) >= _MAX_BLOCKS:
                continue
            seen.add(path)
            blocks.append(Block(block_type="key", name=path, labels=[value] if value else []))
    return blocks


def _top_level_keys(blocks: list[Block]) -> list[str]:
    top: list[str] = []
    seen: set[str] = set()
    for b in blocks:
        t = b.name.split(".", 1)[0]
        if t and t not in seen:
            seen.add(t)
            top.append(t)
    return top


def summary(blocks: list[Block]) -> str:
    if not blocks:
        return "YAML file"
    top = _top_level_keys(blocks)
    head = ", ".join(top[:6])
    more = f" +{len(top) - 6} more" if len(top) > 6 else ""
    return f"YAML file — {len(blocks)} keys; top-level: {head}{more}"


def extract_header(content: str) -> str:
    """Pull the leading ``#`` comment block (e.g. Helm values.yaml's
    "Default values for ..." preamble), capped at 200 chars — mirrors the tf
    parser's purpose-prefix extraction."""
    lines: list[str] = []
    for raw in content.splitlines():
        s = raw.strip()
        if not s:
            if lines:
                break
            continue
        if s.startswith("#"):
            cleaned = s.lstrip("#").strip()
            if cleaned:
                lines.append(cleaned)
        else:
            break
    return " ".join(lines)[:200]


def extract_tags(path: str, blocks: list[Block]) -> list[str]:
    tags: set[str] = {"yaml"}
    name = Path(path).name.lower()
    low = path.lower()

    stem = name.rsplit(".", 1)[0]
    if len(stem) > 1:
        tags.add(stem)

    if name in ("values.yaml", "values.yml", "chart.yaml") or "/templates/" in low:
        tags.add("helm")
    if name.startswith("docker-compose") or name in ("compose.yaml", "compose.yml"):
        tags.add("compose")
    if ".github/workflows/" in low:
        tags.update({"ci", "github-actions"})

    for b in blocks:
        if b.name == "kind" and b.labels:
            tags.add(b.labels[0].lower())
            tags.add("k8s")

    for t in _top_level_keys(blocks)[:10]:
        if len(t) > 1:
            tags.add(t.lower())

    return list(tags)
