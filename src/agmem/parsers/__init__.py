"""Per-extension content parsers, plus a small registry for lookup by extension.

A parser module exposes three callables:

    analyze(content: str) -> list[Block]
    summary(blocks: list[Block]) -> str
    extract_tags(path: str, blocks: list[Block]) -> list[str]

Built-in parsers live in this package: ``tf``, ``py``, ``md``. Users can disable
specific parsers via ``.agmem/parsers.yaml``.
"""

from __future__ import annotations

from typing import Protocol

from . import md, py, tf
from .config import ParserConfig, load
from .types import Block, FileAnalysis, TfBlock

__all__ = [
    "Block",
    "FileAnalysis",
    "ParserConfig",
    "TfBlock",
    "analyze_file",
    "extract_tags_for_file",
    "load",
    "registered_extensions",
]


class _ParserModule(Protocol):
    def analyze(self, content: str) -> list[Block]: ...
    def summary(self, blocks: list[Block]) -> str: ...
    def extract_tags(self, path: str, blocks: list[Block]) -> list[str]: ...


_BUILTIN: dict[str, _ParserModule] = {
    "tf": tf,
    "py": py,
    "md": md,
    "mdx": md,
}


def _ext(path: str) -> str:
    return path.rsplit(".", 1)[-1].lower() if "." in path else ""


def _resolve(ext: str, config: ParserConfig | None) -> _ParserModule | None:
    if not ext:
        return None
    if config and ext in config.disabled_parsers:
        return None
    return _BUILTIN.get(ext)


def registered_extensions(config: ParserConfig | None = None) -> list[str]:
    """Return the list of file extensions an active parser is registered for."""
    disabled = config.disabled_parsers if config else set()
    return [ext for ext in _BUILTIN if ext not in disabled]


def analyze_file(
    path: str, content: str, config: ParserConfig | None = None,
) -> FileAnalysis | None:
    ext = _ext(path)
    parser = _resolve(ext, config)
    if parser is None:
        return None
    blocks = parser.analyze(content)
    if not blocks:
        return None
    return FileAnalysis(
        path=path,
        ext=ext,
        blocks=blocks,
        summary=parser.summary(blocks),
    )


def extract_tags_for_file(
    path: str, blocks: list[Block], config: ParserConfig | None = None,
) -> list[str]:
    ext = _ext(path)
    parser = _resolve(ext, config)
    if parser is None:
        return []
    return parser.extract_tags(path, blocks)
