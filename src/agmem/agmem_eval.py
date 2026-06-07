"""Track A real-workload eval: measure agmem retrieval quality from agent-diff session logs.

Extracts (query, gold_files) pairs from recorded Claude Code sessions and scores
them against the agmem index, producing Hit@K, Recall@K, and MRR metrics.
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .search import search_filtered
from .store import MemoryEntry

AGENT_DIFF_RUNS_DIR = Path.home() / ".agent-diff" / "runs"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Paths agmem prints in its `context` output: `… ref: src/foo.py …` lines and
# any backtick-quoted path tokens. Used to compute the drift-free follow-rate.
_OUTPUT_REF_RE = re.compile(r'ref:\s*([^\s·)]+)')
_OUTPUT_BACKTICK_RE = re.compile(r'`([^`]+)`')

_QUERY_RE = re.compile(
    r'agmem\s+context\s+'
    r'(?:'
    r'"((?:[^"\\]|\\.)*)"'
    r'|'
    r"\'((?:[^\'\\]|\\.)*)\'"
    r')'
)
_TAG_RE = re.compile(r'--tag\s+(\S+)')
_CD_RE = re.compile(r'^\s*cd\s+(\S+)')
_WORKTREE_RE = re.compile(r'\.claude/worktrees/[^/]+/')
_EXCLUDED_DIRS = {'.venv', 'node_modules', '__pycache__', '.tox', 'site-packages', 'dist', 'build'}


@dataclass
class EvalPair:
    run_id: str
    query: str
    cwd: str
    turn: int
    gold_files: set[str]
    window_size: int
    tag: str | None = None
    followed: bool | None = None
    """In-session follow signal (``None`` if the session output wasn't captured,
    e.g. agent-diff logs): did agmem's actual ``context`` output at the time list
    a path that the agent then Read/Edited in the window? This is drift-free —
    it scores what the agent really saw, not a re-query of today's index."""
    followed_soft: bool | None = None
    """Looser variant: a gold file's basename appears anywhere in agmem's output
    text (catches plan-doc references the agent followed to the code)."""
    n_output_refs: int = 0
    """How many distinct paths agmem printed in its output (diagnostic)."""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "query": self.query,
            "cwd": self.cwd,
            "turn": self.turn,
            "gold_files": sorted(self.gold_files),
            "window_size": self.window_size,
            "tag": self.tag,
            "followed": self.followed,
            "followed_soft": self.followed_soft,
            "n_output_refs": self.n_output_refs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EvalPair:
        return cls(
            run_id=d["run_id"],
            query=d["query"],
            cwd=d["cwd"],
            turn=d["turn"],
            gold_files=set(d.get("gold_files", [])),
            window_size=d.get("window_size", 20),
            tag=d.get("tag"),
            followed=d.get("followed"),
            followed_soft=d.get("followed_soft"),
            n_output_refs=d.get("n_output_refs", 0),
        )


@dataclass
class EvalScore:
    pair: EvalPair
    top_k: list[str]
    hit_at: dict[int, bool]
    recall_at: dict[int, float]
    mrr: float
    first_gold_rank: int | None = None
    """1-indexed rank of the first retrieved entry that matches any gold file
    (via direct path match OR content mention). ``None`` if no gold was found
    in any retrieved entry. Diagnostic value: a pair with hit_at[5]=False but
    first_gold_rank=7 reveals "agmem almost surfaced this, just outside K=5"
    — different signal from "never found at all."
    """


@dataclass
class EvalReport:
    pairs: list[EvalPair]
    scores: list[EvalScore]
    ks: list[int]

    @property
    def n_pairs(self) -> int:
        return len(self.scores)

    def coverage(self, k: int) -> float:
        if not self.scores:
            return 0.0
        return sum(1 for s in self.scores if s.hit_at.get(k, False)) / len(self.scores)

    def mean_recall(self, k: int) -> float:
        if not self.scores:
            return 0.0
        return statistics.mean(s.recall_at.get(k, 0.0) for s in self.scores)

    def mean_mrr(self) -> float:
        if not self.scores:
            return 0.0
        return statistics.mean(s.mrr for s in self.scores)

    def follow_rate(self, soft: bool = False) -> tuple[float, int] | None:
        """Drift-free in-session follow rate: fraction of pairs where agmem's
        actual output listed a file the agent then used. Returns (rate, n) over
        pairs that captured output, or None if none did (e.g. agent-diff logs)."""
        vals = [(p.followed_soft if soft else p.followed) for p in self.pairs]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return sum(1 for v in vals if v) / len(vals), len(vals)

    def summary_lines(self) -> list[str]:
        lines = [
            f"Pairs analyzed:   {self.n_pairs}",
        ]
        fr = self.follow_rate(soft=False)
        if fr is not None:
            rate, n = fr
            soft = self.follow_rate(soft=True)
            lines.append(f"Follow rate (in-session, n={n}): {rate:.1%} strict"
                         + (f" / {soft[0]:.1%} soft" if soft else ""))
        for k in sorted(self.ks):
            lines.append(
                f"Coverage (Hit@{k}): {self.coverage(k):.1%}"
            )
        for k in sorted(self.ks):
            lines.append(
                f"Mean Recall@{k}:    {self.mean_recall(k):.2f}"
            )
        lines.append(
            f"Mean MRR:         {self.mean_mrr():.2f}"
        )
        return lines

    def to_csv_rows(self) -> list[dict]:
        rows = []
        for s in self.scores:
            row = {
                "run_id": s.pair.run_id,
                "query": s.pair.query,
                "cwd": s.pair.cwd,
                "tag": s.pair.tag or "",
                "turn": s.pair.turn,
                "window_size": s.pair.window_size,
                "n_gold": len(s.pair.gold_files),
                "n_top": len(s.top_k),
                "mrr": round(s.mrr, 4),
                "first_gold_rank": s.first_gold_rank if s.first_gold_rank is not None else "",
                "followed": "" if s.pair.followed is None else s.pair.followed,
                "followed_soft": "" if s.pair.followed_soft is None else s.pair.followed_soft,
            }
            for k in sorted(self.ks):
                row[f"hit_at_{k}"] = s.hit_at.get(k, False)
                row[f"recall_at_{k}"] = round(s.recall_at.get(k, 0.0), 4)
            row["gold_files"] = "; ".join(sorted(s.pair.gold_files))
            row["top_k_files"] = "; ".join(s.top_k[:10])
            rows.append(row)
        return rows

    def to_dict(self) -> dict:
        fr = self.follow_rate(soft=False)
        fr_soft = self.follow_rate(soft=True)
        return {
            "ks": sorted(self.ks),
            "n_pairs": self.n_pairs,
            "coverage": {str(k): self.coverage(k) for k in self.ks},
            "mean_recall": {str(k): self.mean_recall(k) for k in self.ks},
            "mean_mrr": self.mean_mrr(),
            "follow_rate": (fr[0] if fr else None),
            "follow_rate_soft": (fr_soft[0] if fr_soft else None),
            "follow_rate_n": (fr[1] if fr else 0),
            "scores": [
                {
                    "run_id": s.pair.run_id,
                    "query": s.pair.query,
                    "cwd": s.pair.cwd,
                    "tag": s.pair.tag,
                    "turn": s.pair.turn,
                    "window_size": s.pair.window_size,
                    "gold_files": sorted(s.pair.gold_files),
                    "top_k": s.top_k,
                    "hit_at": {str(k): v for k, v in s.hit_at.items()},
                    "recall_at": {str(k): v for k, v in s.recall_at.items()},
                    "mrr": s.mrr,
                    "first_gold_rank": s.first_gold_rank,
                    "followed": s.pair.followed,
                    "followed_soft": s.pair.followed_soft,
                    "n_output_refs": s.pair.n_output_refs,
                }
                for s in self.scores
            ],
        }


def _load_jsonl(path: Path) -> list[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _get_run_cwd(events: list[dict]) -> str:
    for e in events:
        if e.get("event") == "run_started":
            return e.get("cwd", os.getcwd())
    return os.getcwd()


def is_agmem_context_call(event: dict) -> tuple[str, str | None] | None:
    """Return (query, tag) if event is ``agmem context "..."``, else None."""
    if event.get("tool_name") != "Bash":
        return None
    command = event.get("tool_input", {}).get("command", "")
    m = _QUERY_RE.search(command)
    if not m:
        return None
    query = (m.group(1) or m.group(2) or "").strip()
    if not query:
        return None
    tag = None
    tag_m = _TAG_RE.search(command)
    if tag_m:
        tag = tag_m.group(1)
    return query, tag


def _extract_cd_cwd(command: str, fallback_cwd: str) -> str:
    """If command starts with ``cd /path &&``, return that path, else fallback."""
    m = _CD_RE.match(command)
    if not m:
        return fallback_cwd
    cd_path = Path(m.group(1))
    if cd_path.is_absolute():
        if cd_path.exists():
            return str(cd_path.resolve())
        return str(cd_path)
    return str((Path(fallback_cwd) / cd_path).resolve())


def _normalize_path(abs_path: str, repo_root: str) -> str | None:
    try:
        return str(Path(abs_path).relative_to(repo_root))
    except ValueError:
        return None


def _is_excluded_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    return any(p in _EXCLUDED_DIRS for p in parts)


def _normalize_worktree_path(rel_path: str) -> str:
    return _WORKTREE_RE.sub("", rel_path)


def extract_gold_files(
    events: list[dict],
    start_idx: int,
    window: int,
    cwd: str,
) -> set[str]:
    """Capture file paths from Read/Edit/Write tool calls in the window after start_idx.

    Normalizes worktree paths (strips ``.claude/worktrees/<name>/``) and excludes
    vendored directories (``.venv/``, ``node_modules/``, etc.).
    """
    gold: set[str] = set()
    tool_call_count = 0
    for e in events[start_idx + 1 :]:
        if e.get("event") != "tool_called":
            continue
        tool_call_count += 1
        if tool_call_count > window:
            break
        tool = e.get("tool_name", "")
        ti = e.get("tool_input", {})
        if not isinstance(ti, dict):
            continue
        file_path = None
        if tool in ("Read", "Edit", "Write"):
            file_path = ti.get("file_path")
        elif tool == "Grep":
            file_path = ti.get("path")
        if file_path and isinstance(file_path, str):
            rel = _normalize_path(file_path, cwd)
            if not rel or rel.startswith(".."):
                continue
            rel = _normalize_worktree_path(rel)
            if _is_excluded_path(rel):
                continue
            gold.add(rel)
    return gold


def _discover_run_files(
    run_ids: list[str] | None,
    since_str: str | None,
) -> list[Path]:
    runs_dir = AGENT_DIFF_RUNS_DIR
    if not runs_dir.exists():
        return []

    if run_ids:
        result = []
        for rid in run_ids:
            candidate = runs_dir / rid / "events.jsonl"
            if candidate.exists():
                result.append(candidate)
        return result

    since: datetime | None = None
    if since_str:
        since_str = since_str.strip()
        try:
            if since_str.endswith("d"):
                days = int(since_str[:-1])
                since = datetime.now(timezone.utc) - timedelta(days=days)
        except (ValueError, TypeError):
            pass

    result = []
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        jsonl = entry / "events.jsonl"
        if not jsonl.exists():
            continue
        if since:
            try:
                mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc)
                if mtime < since:
                    continue
            except OSError:
                continue
        result.append(jsonl)
    return result


def _agmem_output_refs(text: str) -> set[str]:
    """Extract the repo-relative paths agmem printed in a ``context`` result:
    ``ref: <path>`` markers plus backtick-quoted path-looking tokens. Worktree
    prefixes are stripped and ``#section`` anchors dropped so the refs line up
    with gold file paths."""
    if not text:
        return set()
    paths: set[str] = set()
    for m in _OUTPUT_REF_RE.finditer(text):
        p = m.group(1).split("#", 1)[0].strip()
        if p:
            paths.add(_normalize_worktree_path(p))
    for m in _OUTPUT_BACKTICK_RE.finditer(text):
        tok = m.group(1).strip()
        if " " in tok:
            continue
        if "/" in tok or re.search(r"\.\w{1,4}$", tok):
            paths.add(_normalize_worktree_path(tok.split("#", 1)[0]))
    return {p for p in paths if p}


def _claude_result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _load_claude_session(path: Path) -> list[dict]:
    """Reshape a Claude Code native session log into the agent-diff event shape
    (``event``/``tool_name``/``tool_input``/``timestamp``) so the same extraction
    path works for both. Each tool_use event also carries its native ``cwd`` and
    a ``_result`` field with the tool's captured output text (matched back via
    ``tool_use_id``), enabling the drift-free follow-rate."""
    try:
        with open(path) as f:
            raw = [line for line in f if line.strip()]
    except OSError:
        return []
    events: list[dict] = []
    by_id: dict[str, dict] = {}
    first_cwd: str | None = None
    for line in raw:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = o.get("type")
        cwd = o.get("cwd", "")
        ts = o.get("timestamp", "")
        content = o.get("message", {}).get("content")
        if t == "assistant" and isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                if first_cwd is None and cwd:
                    first_cwd = cwd
                ev = {
                    "event": "tool_called",
                    "tool_name": b.get("name", ""),
                    "tool_input": b.get("input", {}) or {},
                    "timestamp": ts,
                    "cwd": cwd,
                    "_result": "",
                }
                events.append(ev)
                bid = b.get("id")
                if bid:
                    by_id[bid] = ev
        elif t == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tuid = b.get("tool_use_id")
                    target = by_id.get(tuid) if isinstance(tuid, str) else None
                    if target is not None:
                        target["_result"] = _claude_result_text(b)
    if first_cwd:
        events.insert(0, {"event": "run_started", "cwd": first_cwd})
    return events


def _discover_claude_files(since_str: str | None) -> list[Path]:
    if not CLAUDE_PROJECTS_DIR.exists():
        return []
    since: datetime | None = None
    if since_str:
        since_str = since_str.strip()
        try:
            if since_str.endswith("d"):
                since = datetime.now(timezone.utc) - timedelta(days=int(since_str[:-1]))
        except (ValueError, TypeError):
            pass
    result = []
    for path in sorted(CLAUDE_PROJECTS_DIR.rglob("*.jsonl")):
        if since:
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if mtime < since:
                    continue
            except OSError:
                continue
        result.append(path)
    return result


def _discover_sessions(
    run_ids: list[str] | None,
    since: str | None,
    source: str,
) -> list[tuple[str, list[dict], bool]]:
    """Resolve the requested source(s) to ``(run_id, events, has_output)`` tuples.

    ``source`` is one of ``auto`` | ``claude`` | ``agent-diff``. ``auto`` prefers
    Claude native logs when present (they're a superset that still records, unlike
    agent-diff) and otherwise falls back to agent-diff. ``has_output`` is True for
    Claude sessions, which carry tool output for the follow-rate.
    """
    use_claude = source == "claude"
    use_agent_diff = source == "agent-diff"
    if source == "auto":
        if not run_ids and _discover_claude_files(None):
            use_claude = True
        else:
            use_agent_diff = True

    sessions: list[tuple[str, list[dict], bool]] = []
    if use_agent_diff:
        for jsonl_path in _discover_run_files(run_ids, since):
            events = _load_jsonl(jsonl_path)
            if events:
                sessions.append((jsonl_path.parent.name, events, False))
    if use_claude:
        for path in _discover_claude_files(since):
            events = _load_claude_session(path)
            if events:
                sessions.append((path.stem, events, True))
    return sessions


def _pairs_from_events(
    events: list[dict],
    run_id: str,
    cwd_filter: str | None,
    window_turns: int,
    has_output: bool,
) -> list[EvalPair]:
    """Walk one session's events and yield (query, gold) pairs, computing the
    in-session follow-rate when the session captured tool output."""
    pairs: list[EvalPair] = []
    run_cwd = _get_run_cwd(events)
    seen_queries: set[str] = set()
    for idx, e in enumerate(events):
        if e.get("event") != "tool_called":
            continue
        parsed = is_agmem_context_call(e)
        if not parsed:
            continue
        query, tag = parsed
        cmd = e.get("tool_input", {}).get("command", "")
        base_cwd = e.get("cwd") or run_cwd
        effective_cwd = _extract_cd_cwd(cmd, base_cwd)

        if cwd_filter and effective_cwd != cwd_filter:
            continue
        if query in seen_queries:
            continue
        seen_queries.add(query)
        if len(query) < 10:
            continue

        gold = extract_gold_files(events, idx, window_turns, effective_cwd)
        if not gold:
            continue

        followed: bool | None = None
        followed_soft: bool | None = None
        n_refs = 0
        if has_output:
            out_text = e.get("_result", "") or ""
            out_refs = _agmem_output_refs(out_text)
            n_refs = len(out_refs)
            followed = bool(gold & out_refs)
            followed_soft = followed or any(Path(g).name in out_text for g in gold)

        pairs.append(EvalPair(
            run_id=run_id,
            query=query,
            cwd=effective_cwd,
            turn=idx,
            gold_files=gold,
            window_size=window_turns,
            tag=tag,
            followed=followed,
            followed_soft=followed_soft,
            n_output_refs=n_refs,
        ))
    return pairs


def extract_eval_pairs(
    run_ids: list[str] | None = None,
    since: str | None = None,
    cwd_filter: str | None = None,
    window_turns: int = 20,
    source: str = "agent-diff",
) -> list[EvalPair]:
    """Extract (query, gold_files) pairs from recorded sessions.

    ``source`` selects the log backend: ``agent-diff`` (default, back-compat),
    ``claude`` (Claude Code native logs at ``~/.claude/projects``), or ``auto``
    (Claude if present, else agent-diff). Claude sessions additionally populate
    the per-pair ``followed`` / ``followed_soft`` fields.
    """
    pairs: list[EvalPair] = []
    for run_id, events, has_output in _discover_sessions(run_ids, since, source):
        pairs.extend(_pairs_from_events(events, run_id, cwd_filter, window_turns, has_output))
    return pairs


def _gold_mentioned_in_text(gold_file: str, text: str) -> bool:
    """Check if a gold file path or its basename is mentioned in retrieved entry text.
    
    This captures the common pattern where agmem returns a plan doc that
    references code files — the agent reads the plan, follows the reference,
    and edits the code. Strict path-match would miss this; content-mention
    recognizes it as a hit.
    """
    if gold_file in text:
        return True
    basename = Path(gold_file).name
    if basename != gold_file and basename in text:
        return True
    return False


def _compute_hit_metrics(
    results: list[tuple[MemoryEntry, float]],
    gold_files: set[str],
    ks: list[int],
) -> tuple[dict[int, bool], dict[int, float], float, int | None]:
    """Compute Hit@K, Recall@K, MRR, and first_gold_rank with content-mention soft matching.

    Returns:
        hit_at:           {k: bool}
        recall_at:        {k: float}
        mrr:              float (0.0 if no hit)
        first_gold_rank:  1-indexed rank of first hit, or None if no hit found

    1. Strict path match: top-K ``source_ref`` directly matches a gold file.
    2. Soft content match: gold file or its basename appears in top-K entry text.
    
    Both count equally — agmem helped the agent find the file either way.
    """
    hit_at: dict[int, bool] = {}
    recall_at: dict[int, float] = {}

    for k in ks:
        top_entries = [e for e, _ in results[:k]]
        top_paths = {e.source_ref for e in top_entries if e.source_ref}

        # Strict path match
        matched = top_paths & gold_files
        remaining = gold_files - matched

        # Soft content match for remaining gold files
        if remaining:
            all_text = "\n".join(e.text for e in top_entries)
            soft_matched = {gf for gf in remaining if _gold_mentioned_in_text(gf, all_text)}
            matched |= soft_matched

        hit_at[k] = len(matched) > 0
        recall_at[k] = len(matched) / len(gold_files) if gold_files else 0.0

    # First-hit rank (1-indexed) — used for both MRR and the diagnostic field.
    # Walks results in order; stops at the first entry that path-matches OR
    # content-mentions any gold file.
    first_gold_rank: int | None = None
    for i, (entry, _) in enumerate(results, start=1):
        top_path = entry.source_ref
        if top_path and top_path in gold_files:
            first_gold_rank = i
            break
        if any(_gold_mentioned_in_text(gf, entry.text) for gf in gold_files):
            first_gold_rank = i
            break

    mrr = 1.0 / first_gold_rank if first_gold_rank is not None else 0.0

    return hit_at, recall_at, mrr, first_gold_rank


def score_pair(
    pair: EvalPair,
    ks: list[int] | None = None,
) -> EvalScore:
    """Run agmem context on the pair's query, score against gold_files."""
    if ks is None:
        ks = [3, 5, 10, 20]
    max_k = max(ks)
    results = search_filtered(pair.query, limit=max_k, tag=pair.tag, cwd=pair.cwd)
    top_refs = [e.source_ref for e, _ in results if e.source_ref]
    hit_at, recall_at, mrr, first_gold_rank = _compute_hit_metrics(results, pair.gold_files, ks)
    return EvalScore(
        pair=pair,
        top_k=top_refs,
        hit_at=hit_at,
        recall_at=recall_at,
        mrr=mrr,
        first_gold_rank=first_gold_rank,
    )


def save_pairs(pairs: list[EvalPair], path: Path) -> None:
    with open(path, "w") as f:
        json.dump(
            {
                "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "n_pairs": len(pairs),
                "pairs": [p.to_dict() for p in pairs],
            },
            f, ensure_ascii=False, indent=2,
        )


def load_pairs(path: Path) -> list[EvalPair]:
    with open(path) as f:
        data = json.load(f)
    return [EvalPair.from_dict(p) for p in data.get("pairs", [])]


def run_eval(
    run_ids: list[str] | None = None,
    since: str | None = None,
    cwd_filter: str | None = None,
    window_turns: int = 20,
    ks: list[int] | None = None,
    pairs: list[EvalPair] | None = None,
    source: str = "agent-diff",
) -> EvalReport:
    """End-to-end eval: extract pairs (or use pre-loaded), score, produce report."""
    if ks is None:
        ks = [3, 5, 10, 20]
    if pairs is None:
        pairs = extract_eval_pairs(
            run_ids=run_ids, since=since,
            cwd_filter=cwd_filter, window_turns=window_turns,
            source=source,
        )
    scores = [score_pair(p, ks=ks) for p in pairs]
    return EvalReport(pairs=pairs, scores=scores, ks=ks)


def _build_slice_by_cwd(scores: list[EvalScore], k: int) -> list[tuple[str, int, float, float]]:
    buckets: dict[str, list[EvalScore]] = {}
    for s in scores:
        buckets.setdefault(s.pair.cwd, []).append(s)
    result = []
    for cwd in sorted(buckets):
        bucket = buckets[cwd]
        n = len(bucket)
        hit = sum(1 for s in bucket if s.hit_at.get(k, False)) / n if n else 0.0
        recall = statistics.mean(s.recall_at.get(k, 0.0) for s in bucket) if n else 0.0
        result.append((cwd, n, hit, recall))
    return result


def _build_slice_by_query_length(scores: list[EvalScore], k: int) -> list[tuple[str, int, float, float]]:
    buckets: dict[str, list[EvalScore]] = {
        "short  (1-2 tok)": [],
        "medium (3-5 tok)": [],
        "long   (6+ tok)": [],
    }
    for s in scores:
        n_tok = len(s.pair.query.split())
        if n_tok <= 2:
            key = "short  (1-2 tok)"
        elif n_tok <= 5:
            key = "medium (3-5 tok)"
        else:
            key = "long   (6+ tok)"
        buckets[key].append(s)
    result = []
    for key in ["short  (1-2 tok)", "medium (3-5 tok)", "long   (6+ tok)"]:
        bucket = buckets[key]
        n = len(bucket)
        if n == 0:
            result.append((key, 0, 0.0, 0.0))
        else:
            hit = sum(1 for s in bucket if s.hit_at.get(k, False)) / n
            recall = statistics.mean(s.recall_at.get(k, 0.0) for s in bucket)
            result.append((key, n, hit, recall))
    return result


def _build_slice_by_source_mix(scores: list[EvalScore], k: int) -> list[tuple[str, int, float, float]]:
    manual: list[EvalScore] = []
    index_only: list[EvalScore] = []
    for s in scores:
        has_manual = any(ref for ref in s.top_k if "manual" in ref.lower())
        if has_manual:
            manual.append(s)
        else:
            index_only.append(s)
    result = []
    for label, bucket in [("manual in top-K", manual), ("index only", index_only)]:
        n = len(bucket)
        if n == 0:
            result.append((label, 0, 0.0, 0.0))
        else:
            hit = sum(1 for s in bucket if s.hit_at.get(k, False)) / n
            recall = statistics.mean(s.recall_at.get(k, 0.0) for s in bucket)
            result.append((label, n, hit, recall))
    return result


def _build_slice_by_extension(scores: list[EvalScore], k: int) -> list[tuple[str, int, float, float]]:
    buckets: dict[str, list[EvalScore]] = {}
    for s in scores:
        for gf in s.pair.gold_files:
            ext = Path(gf).suffix or "(none)"
            if ext.startswith("."):
                ext = ext[1:]
            buckets.setdefault(ext, []).append(s)
    result = []
    for ext in sorted(buckets, key=lambda x: -len(buckets[x])):
        bucket = buckets[ext]
        n = len(bucket)
        hit = sum(1 for s in bucket if s.hit_at.get(k, False)) / n if n else 0.0
        recall = statistics.mean(s.recall_at.get(k, 0.0) for s in bucket) if n else 0.0
        result.append((f".{ext}", n, hit, recall))
    return result


def format_report(report: EvalReport, k_headline: int = 5) -> str:
    """Render the eval report as markdown text."""
    now = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"agmem real-workload eval — {now}",
        "=" * 70,
    ]
    lines.extend(report.summary_lines())

    slices_cwd = _build_slice_by_cwd(report.scores, k_headline)
    if slices_cwd:
        lines.append("")
        lines.append("By cwd:")
        for cwd, n, hit, recall in slices_cwd:
            lines.append(f"  {cwd}  n={n}  Hit@{k_headline}={hit:.0%}  R@{k_headline}={recall:.2f}")

    slices_len = _build_slice_by_query_length(report.scores, k_headline)
    if slices_len:
        lines.append("")
        lines.append("By query length:")
        for label, n, hit, recall in slices_len:
            lines.append(f"  {label}  n={n}  Hit@{k_headline}={hit:.0%}  R@{k_headline}={recall:.2f}")

    slices_src = _build_slice_by_source_mix(report.scores, k_headline)
    if slices_src:
        lines.append("")
        lines.append("By source mix in top-K:")
        for label, n, hit, recall in slices_src:
            lines.append(f"  {label}  n={n}  Hit@{k_headline}={hit:.0%}  R@{k_headline}={recall:.2f}")

    slices_ext = _build_slice_by_extension(report.scores, k_headline)
    if slices_ext:
        lines.append("")
        lines.append("By gold file extension:")
        for label, n, hit, recall in slices_ext[:8]:
            lines.append(f"  {label}  n={n}  Hit@{k_headline}={hit:.0%}  R@{k_headline}={recall:.2f}")

    return "\n".join(lines)


def write_csv(report: EvalReport, path: Path) -> None:
    rows = report.to_csv_rows()
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(report: EvalReport, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)


def run_sweep(
    param_specs: list[str],
    metric: str = "hit_at_5",
    since: str | None = None,
    cwd_filter: str | None = None,
    pairs: list[EvalPair] | None = None,
) -> dict:
    """Grid-search agmem parameter values across the eval set.

    ``param_specs`` entries are ``"name=val1,val2,..."``. Supported names:
    ``kind_boost.rule``, ``kind_boost.pattern``, ``source_boost.manual``,
    ``source_ref_weight``, ``basename_weight``, ``title_weight``, ``b``.

    Monkey-patches search module constants, re-scores all pairs, and returns
    the best combo + full result grid.
    """
    import itertools

    from . import search

    if pairs is None:
        pairs = extract_eval_pairs(since=since, cwd_filter=cwd_filter)
    if not pairs:
        return {"error": "No eval pairs found.", "n_pairs": 0, "n_combos": 0, "results": []}

    param_grid: dict[str, list[float]] = {}
    params_order: list[str] = []
    for spec in param_specs:
        if "=" not in spec:
            continue
        name, vals_str = spec.split("=", 1)
        name = name.strip()
        vals_str = vals_str.strip()
        try:
            vals = [float(v.strip()) for v in vals_str.split(",")]
        except ValueError:
            return {"error": f"Invalid values for {name}: {vals_str}"}
        param_grid[name] = vals
        params_order.append(name)

    if not param_grid:
        return {"error": "No valid --param specs.", "n_pairs": 0, "n_combos": 0, "results": []}

    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))

    ks_map = {
        "hit_at_3": 3, "hit_at_5": 5, "hit_at_10": 10, "hit_at_20": 20,
        "recall_at_3": 3, "recall_at_5": 5, "recall_at_10": 10, "recall_at_20": 20,
    }
    k_val = ks_map.get(metric, 5)
    is_mrr = metric == "mrr"

    orig_source_ref_weight = search._SOURCE_REF_WEIGHT
    orig_basename_weight = search._BASENAME_WEIGHT
    orig_title_weight = search._TITLE_WEIGHT
    orig_default_kind = dict(search.DEFAULT_KIND_BOOST)
    orig_default_source = dict(search.DEFAULT_SOURCE_BOOST)

    results: list[dict] = []
    best_score = -1.0
    best_params: dict = {}
    best_combo: tuple = ()

    try:
        for combo in combos:
            combo_params: dict[str, float] = dict(zip(keys, combo))

            kind_boost = dict(search.DEFAULT_KIND_BOOST)
            source_boost = dict(search.DEFAULT_SOURCE_BOOST)

            for key, val in combo_params.items():
                if key == "kind_boost.rule":
                    kind_boost["rule"] = val
                elif key == "kind_boost.pattern":
                    kind_boost["pattern"] = val
                elif key == "source_boost.manual":
                    source_boost["manual"] = val
                elif key == "source_ref_weight":
                    search._SOURCE_REF_WEIGHT = int(val)
                elif key == "basename_weight":
                    search._BASENAME_WEIGHT = int(val)
                elif key == "title_weight":
                    search._TITLE_WEIGHT = int(val)

            search.DEFAULT_KIND_BOOST = kind_boost
            search.DEFAULT_SOURCE_BOOST = source_boost

            scores = [_score_pair_monkeypatched(p, ks=[k_val]) for p in pairs]

            if is_mrr:
                agg = statistics.mean(s.mrr for s in scores) if scores else 0.0
            elif metric.startswith("hit_at_"):
                agg = sum(1 for s in scores if s.hit_at.get(k_val, False)) / len(scores) if scores else 0.0
            else:
                agg = statistics.mean(s.recall_at.get(k_val, 0.0) for s in scores) if scores else 0.0

            results.append({
                "params": dict(combo_params),
                "score": round(agg, 4),
            })

            if agg > best_score:
                best_score = agg
                best_params = dict(combo_params)
                best_combo = combo

    finally:
        search._SOURCE_REF_WEIGHT = orig_source_ref_weight
        search._BASENAME_WEIGHT = orig_basename_weight
        search._TITLE_WEIGHT = orig_title_weight
        search.DEFAULT_KIND_BOOST = orig_default_kind
        search.DEFAULT_SOURCE_BOOST = orig_default_source

    if metric.startswith("recall") or is_mrr:
        results.sort(key=lambda r: r["score"], reverse=True)
    else:
        results.sort(key=lambda r: r["score"], reverse=True)

    return {
        "n_pairs": len(pairs),
        "n_combos": len(combos),
        "metric": metric,
        "params_order": params_order,
        "best_score": round(best_score, 4),
        "best_params": best_params,
        "results": results,
    }


def _score_pair_monkeypatched(
    pair: EvalPair,
    ks: list[int] | None = None,
) -> EvalScore:
    """Like score_pair but uses current (patched) search module state."""
    if ks is None:
        ks = [5]
    max_k = max(ks)
    results = search_filtered(pair.query, limit=max_k, tag=pair.tag, cwd=pair.cwd)
    top_refs = [e.source_ref for e, _ in results if e.source_ref]
    hit_at, recall_at, mrr, first_gold_rank = _compute_hit_metrics(results, pair.gold_files, ks)
    return EvalScore(
        pair=pair,
        top_k=top_refs,
        hit_at=hit_at,
        recall_at=recall_at,
        mrr=mrr,
        first_gold_rank=first_gold_rank,
    )
