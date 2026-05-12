"""Shared types for per-extension parsers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    """Generic source-block record used by all parsers."""

    block_type: str
    name: str
    labels: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        if self.labels:
            return f"{self.block_type} {self.name} ({', '.join(self.labels)})"
        return f"{self.block_type} {self.name}"

    @property
    def resource_type(self) -> str:
        if self.block_type in ("resource", "data") and self.labels:
            return self.labels[0]
        if self.block_type == "module":
            return self.name
        return ""


# Back-compat alias — older imports use TfBlock.
TfBlock = Block


@dataclass
class FileAnalysis:
    path: str
    ext: str
    blocks: list[Block] = field(default_factory=list)
    summary: str = ""
