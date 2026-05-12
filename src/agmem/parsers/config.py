"""Optional user-supplied parser overrides loaded from .agmem/parsers.yaml.

Schema (all keys optional):

    disabled_parsers: [md, py]            # skip these built-in parsers
    key_files:                            # extend KEY_FILES
      Procfile: "Heroku procfile"
    extension_labels:                     # extend EXT_LABELS
      ".graphql": "GraphQL schema"
    parsers:                              # declarative regex parsers (future-friendly slot)
      ext: ...

Unknown keys are ignored. Invalid YAML is silently dropped (with a warning printed
by the caller) so a broken config never blocks indexing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ParserConfig:
    disabled_parsers: set[str] = field(default_factory=set)
    key_files: dict[str, str] = field(default_factory=dict)
    extension_labels: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def load(agmem_dir: Path) -> ParserConfig:
    path = agmem_dir / "parsers.yaml"
    if not path.exists():
        return ParserConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        return ParserConfig(error=f"failed to parse {path}: {e}")
    if data is None:
        return ParserConfig()
    if not isinstance(data, dict):
        return ParserConfig(error=f"{path}: top-level must be a mapping")

    disabled = set()
    raw_disabled = data.get("disabled_parsers") or []
    if isinstance(raw_disabled, list):
        for item in raw_disabled:
            if isinstance(item, str):
                disabled.add(item.lower().lstrip("."))

    key_files: dict[str, str] = {}
    raw_kf = data.get("key_files") or {}
    if isinstance(raw_kf, dict):
        for name, desc in raw_kf.items():
            if isinstance(name, str) and isinstance(desc, str):
                key_files[name] = desc

    ext_labels: dict[str, str] = {}
    raw_ext = data.get("extension_labels") or {}
    if isinstance(raw_ext, dict):
        for ext, label in raw_ext.items():
            if isinstance(ext, str) and isinstance(label, str):
                key = ext if ext.startswith(".") else f".{ext}"
                ext_labels[key.lower()] = label

    return ParserConfig(
        disabled_parsers=disabled,
        key_files=key_files,
        extension_labels=ext_labels,
        raw=data,
    )
