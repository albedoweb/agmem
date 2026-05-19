"""Back-compat shim — content analysis lives in :mod:`agmem.parsers` now.

Re-exports the previously public symbols so existing imports keep working.
"""

from __future__ import annotations

from .parsers import (
    Block,
    FileAnalysis,
    TfBlock,
    analyze_file,
    extract_tags_for_file,
)
from .parsers.go import analyze as analyze_go
from .parsers.go import extract_tags as _go_extract_tags
from .parsers.md import analyze as analyze_md
from .parsers.py import analyze as analyze_py
from .parsers.py import extract_tags as _py_extract_tags
from .parsers.tf import analyze as analyze_tf
from .parsers.tf import extract_tags as _tf_extract_tags


def extract_tags_from_blocks(blocks: list[Block]) -> list[str]:
    """Pre-refactor signature — tf tag extraction by block list only."""
    return _tf_extract_tags("", blocks)


def extract_py_tags(blocks: list[Block]) -> list[str]:
    """Pre-refactor signature — python tag extraction by block list only."""
    return _py_extract_tags("", blocks)


def extract_go_tags(blocks: list[Block]) -> list[str]:
    """Pre-refactor signature — go tag extraction by block list only."""
    return _go_extract_tags("", blocks)

__all__ = [
    "Block",
    "FileAnalysis",
    "TfBlock",
    "analyze_file",
    "analyze_go",
    "analyze_md",
    "analyze_py",
    "analyze_tf",
    "extract_go_tags",
    "extract_py_tags",
    "extract_tags_for_file",
    "extract_tags_from_blocks",
]
