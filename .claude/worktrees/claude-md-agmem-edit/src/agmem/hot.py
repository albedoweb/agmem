"""Pre-computed `_hot.md` cache: instant, low-budget context for session start.

Produces `.agmem/_hot.md` with all rules + top-N facts/patterns under a chars budget
(~500 tokens). Designed to be regenerated on every commit (cheap, deterministic) and
read instantly by hooks or by an agent at session start — no BM25 round-trip.

Ranking for facts/patterns: non-drifted first, then by `verified_at` desc, then by `ts` desc.
This surfaces recently-verified entries and de-prioritises drifted ones automatically.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .store import MemoryEntry, read_all_entries

HOT_FILENAME = "_hot.md"
DEFAULT_BUDGET_CHARS = 2000  # ~500 tokens for English text (4 chars/token rule of thumb)
MAX_FACTS = 8
MAX_PATTERNS = 4


def hot_path(cwd: str | None = None) -> Path:
    return config.agmem_dir(cwd) / HOT_FILENAME


def _rank_entries(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Sort: non-drifted before drifted, then verified_at desc, then ts desc."""
    def key(e: MemoryEntry) -> tuple[bool, str, str]:
        return (e.drifted_at is None, e.verified_at or "", e.ts or "")
    return sorted(entries, key=key, reverse=True)


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=2, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def render_hot(
    rules: list[MemoryEntry],
    facts: list[MemoryEntry],
    patterns: list[MemoryEntry],
    *,
    commit: str | None = None,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    project: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Render _hot.md content. Returns (text, stats)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {"rules": 0, "facts": 0, "patterns": 0}

    header_bits = [f"generated={now}"]
    if commit:
        header_bits.append(f"commit={commit[:12]}")
    if project:
        header_bits.append(f"project={project}")
    header = f"<!-- agmem:hot {' '.join(header_bits)} -->\n\n# Project memory snapshot\n"

    parts: list[str] = [header]

    # Rules: always all of them. If they overflow, that's a memory hygiene problem
    # that should be visible — we surface it as an inline warning, not silently truncate.
    if rules:
        parts.append("\n## Constraints\n")
        for r in rules:
            parts.append(f"- {r.text}\n")
            counts["rules"] += 1

    used = sum(len(p) for p in parts)
    if used > budget_chars:
        parts.append(
            f"\n> ⚠ rules section exceeds {budget_chars} chars budget "
            f"({used} actual). Consider consolidating with `agmem review` and "
            f"`agmem forget <id>` for low-value rules.\n"
        )

    # Patterns come before facts: a concrete example makes a rule actionable.
    # Facts then fill remaining budget. Both sections are ranked individually.
    used = sum(len(p) for p in parts)
    if patterns and used < budget_chars:
        section: list[str] = ["\n## Patterns\n"]
        for p in patterns[:MAX_PATTERNS]:
            line = f"- {p.text}\n"
            section_size = sum(len(s) for s in section)
            if used + section_size + len(line) > budget_chars:
                break
            section.append(line)
            counts["patterns"] += 1
        if counts["patterns"] > 0:
            parts.extend(section)

    used = sum(len(p) for p in parts)
    if facts and used < budget_chars:
        section = ["\n## Facts\n"]
        for f in facts[:MAX_FACTS]:
            line = f"- {f.text}\n"
            section_size = sum(len(s) for s in section)
            if used + section_size + len(line) > budget_chars:
                break
            section.append(line)
            counts["facts"] += 1
        if counts["facts"] > 0:
            parts.extend(section)

    text = "".join(parts)
    return text, {"chars": len(text), **counts}


def run_refresh(
    *,
    cwd: str | None = None,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> dict:
    entries = read_all_entries(cwd)
    rules = _rank_entries([e for e in entries if e.kind == "rule"])
    facts = _rank_entries([e for e in entries if e.kind == "fact" and e.source != "index"])
    patterns = _rank_entries([e for e in entries if e.kind == "pattern"])

    repo_root = config.find_repo_root(cwd)
    commit = _git_head(repo_root)

    cfg = config.read_config(cwd)
    project = cfg.get("project") if isinstance(cfg, dict) else None

    text, stats = render_hot(
        rules, facts, patterns,
        commit=commit,
        budget_chars=budget_chars,
        project=project if isinstance(project, str) and project else None,
    )

    config.ensure_agmem_dir(cwd)
    path = hot_path(cwd)
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "stats": stats}


def read_hot(cwd: str | None = None) -> str | None:
    path = hot_path(cwd)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
