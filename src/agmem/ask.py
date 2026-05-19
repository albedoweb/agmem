"""Learning-mode wrapper around `context`.

Differences from ``agmem context``:

- Returns a tighter slice (``--top-n`` defaults to 3, not 10).
- Maintains session state in ``.agmem/_ask_session.json`` so follow-up queries
  prefer expanding from already-seen ``source_ref``s and demote duplicates.
- Appends a "Haven't seen yet" section: sibling sections of the same files
  the user already saw, plus tags from the next-best results that weren't
  represented in the top results.

Designed for the recursive hole-filling pattern (Peterson method): pose a
question, drill into a result, ask the next question — without losing track
of what you've already learned.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .render import _format_ref, _condense_section_text
from .search import search_filtered
from .store import MemoryEntry

SESSION_FILENAME = "_ask_session.json"
SESSION_STALE_MINUTES = 30
DEFAULT_TOP_N = 3
SUGGESTION_POOL = 12  # how many results below the top we scan for suggestions

_SIBLING_BOOST = 1.5
_SEEN_DEMOTE = 0.3
# Tags that appear on every index entry — useless as "haven't seen" hints.
_NOISE_TAGS: frozenset[str] = frozenset({
    "index", "content", "section", "section-master", "key-file", "directory",
    "summary", "doc", "md", "mdx", "py", "tf",
})


@dataclass
class AskQuery:
    q: str
    ts: str
    returned_refs: list[str]


@dataclass
class AskSession:
    started_at: str
    queries: list[AskQuery] = field(default_factory=list)
    seen_refs: list[str] = field(default_factory=list)
    seen_tags: list[str] = field(default_factory=list)


@dataclass
class AskResult:
    query: str
    session: AskSession
    is_new_session: bool
    top: list[tuple[MemoryEntry, float]]
    sibling_suggestions: list[MemoryEntry]
    tag_suggestions: list[tuple[str, int]]
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_path(cwd: str | None = None) -> Path:
    return config.agmem_dir(cwd) / SESSION_FILENAME


def load_session(cwd: str | None = None) -> AskSession | None:
    path = _session_path(cwd)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    queries = [
        AskQuery(
            q=str(q.get("q", "")),
            ts=str(q.get("ts", "")),
            returned_refs=list(q.get("returned_refs", []) or []),
        )
        for q in data.get("queries", []) or []
        if isinstance(q, dict)
    ]
    return AskSession(
        started_at=str(data.get("started_at", _now())),
        queries=queries,
        seen_refs=list(data.get("seen_refs", []) or []),
        seen_tags=list(data.get("seen_tags", []) or []),
    )


def save_session(session: AskSession, cwd: str | None = None) -> None:
    config.ensure_agmem_dir(cwd)
    path = _session_path(cwd)
    path.write_text(
        json.dumps(asdict(session), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def reset_session(cwd: str | None = None) -> bool:
    path = _session_path(cwd)
    if path.exists():
        path.unlink()
        return True
    return False


def is_session_stale(session: AskSession, max_minutes: int = SESSION_STALE_MINUTES) -> bool:
    if not session.queries:
        return True
    last = session.queries[-1].ts
    if not last:
        return True
    try:
        ts = datetime.fromisoformat(last)
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - ts
    return age.total_seconds() > max_minutes * 60


def _file_of(ref: str | None) -> str:
    if not ref:
        return ""
    return ref.split("#", 1)[0]


def _rerank_for_session(
    results: list[tuple[MemoryEntry, float]],
    session: AskSession,
) -> list[tuple[MemoryEntry, float]]:
    """Push already-seen entries down, boost siblings of seen entries."""
    if not session.seen_refs:
        return results
    seen = set(session.seen_refs)
    seen_files = {_file_of(r) for r in seen if r}
    rescored: list[tuple[MemoryEntry, float]] = []
    for entry, score in results:
        ref = entry.source_ref or ""
        if ref in seen:
            rescored.append((entry, score * _SEEN_DEMOTE))
        elif ref and _file_of(ref) in seen_files and ref not in seen:
            rescored.append((entry, score * _SIBLING_BOOST))
        else:
            rescored.append((entry, score))
    return sorted(rescored, key=lambda x: x[1], reverse=True)


def _collect_sibling_suggestions(
    results: list[tuple[MemoryEntry, float]],
    shown_n: int,
    seen_refs: set[str],
) -> list[MemoryEntry]:
    """Entries below the top that are *other sections* of files we just showed."""
    shown_files: set[str] = set()
    for entry, _ in results[:shown_n]:
        if entry.source_ref:
            shown_files.add(_file_of(entry.source_ref))
    out: list[MemoryEntry] = []
    seen_section_files: set[str] = set()
    for entry, _ in results[shown_n: shown_n + SUGGESTION_POOL]:
        ref = entry.source_ref or ""
        if not ref or ref in seen_refs:
            continue
        if "#" not in ref:
            continue
        f = _file_of(ref)
        if f not in shown_files:
            continue
        if ref in seen_section_files:
            continue
        seen_section_files.add(ref)
        out.append(entry)
        if len(out) >= 4:
            break
    return out


def _collect_tag_suggestions(
    results: list[tuple[MemoryEntry, float]],
    shown_n: int,
    seen_tags: set[str],
) -> list[tuple[str, int]]:
    """Tags from the suggestion pool that weren't already covered by shown entries."""
    shown_tags: set[str] = set()
    for entry, _ in results[:shown_n]:
        for t in entry.tags:
            shown_tags.add(t.lower())
    counts: dict[str, int] = {}
    for entry, _ in results[shown_n: shown_n + SUGGESTION_POOL]:
        for raw_tag in entry.tags:
            t = raw_tag.lower()
            if t in shown_tags or t in seen_tags or t in _NOISE_TAGS:
                continue
            if len(t) < 3 or len(t) > 30:
                continue
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]


def run_ask(
    query: str,
    *,
    top_n: int = DEFAULT_TOP_N,
    new_session: bool = False,
    cwd: str | None = None,
    tag: str | None = None,
    mmr_enabled: bool = False,
    mmr_lambda: float = 0.7,
) -> AskResult:
    """Execute a learning-mode retrieval and update session state.

    ``tag`` restricts the search to entries carrying that tag — same semantics
    as ``agmem context --tag X``. Session state (seen refs / tags) still
    updates, so follow-up queries in the same session keep their progressive
    learning behavior.
    """
    session = load_session(cwd)
    is_new = False
    if session is None or new_session or is_session_stale(session):
        session = AskSession(started_at=_now())
        is_new = True

    raw = search_filtered(
        query, limit=max(top_n + SUGGESTION_POOL, 10),
        cwd=cwd, tag=tag,
        mmr_enabled=mmr_enabled, mmr_lambda=mmr_lambda,
    )
    if not raw:
        return AskResult(
            query=query,
            session=session,
            is_new_session=is_new,
            top=[],
            sibling_suggestions=[],
            tag_suggestions=[],
        )

    reranked = _rerank_for_session(raw, session)
    top = reranked[:top_n]
    seen_set = set(session.seen_refs)
    sibling_suggestions = _collect_sibling_suggestions(reranked, len(top), seen_set)
    tag_suggestions = _collect_tag_suggestions(reranked, len(top), set(session.seen_tags))

    returned_refs = [e.source_ref for e, _ in top if e.source_ref]
    session.queries.append(AskQuery(q=query, ts=_now(), returned_refs=returned_refs))
    for ref in returned_refs:
        if ref and ref not in session.seen_refs:
            session.seen_refs.append(ref)
    for entry, _ in top:
        for t in entry.tags:
            tl = t.lower()
            if tl in _NOISE_TAGS:
                continue
            if tl not in session.seen_tags:
                session.seen_tags.append(tl)
    save_session(session, cwd)

    return AskResult(
        query=query,
        session=session,
        is_new_session=is_new,
        top=top,
        sibling_suggestions=sibling_suggestions,
        tag_suggestions=tag_suggestions,
    )


def render_haven_seen_tail(result: AskResult) -> str:
    """Markdown fragment listing sibling sections + nearby tags. Empty when
    there's nothing useful to suggest. Designed to be appended to whichever
    main render the caller chose (``render_context`` or ``render_ask``)."""
    if not (result.sibling_suggestions or result.tag_suggestions):
        return ""
    lines: list[str] = ["## Haven't seen yet", ""]
    for entry in result.sibling_suggestions:
        ref = entry.source_ref or ""
        anchor = ref.split("#", 1)[1] if "#" in ref else ref
        preview = anchor.replace("-", " ")
        lines.append(f"- `{ref}` — drill: `agmem context \"{preview}\" --session`")
    if result.tag_suggestions:
        tags_str = ", ".join(f"`{t}` ({n})" for t, n in result.tag_suggestions)
        lines.append(f"- nearby tags: {tags_str}")
    if not result.is_new_session:
        n_files = len({_file_of(r) for r in result.session.seen_refs if r})
        lines.append(f"- session: {len(result.session.queries)} queries · {n_files} files touched")
    lines.append("")
    return "\n".join(lines)


def render_ask(result: AskResult, cwd: str | None = None) -> str:
    """Markdown rendering of an AskResult — a tight, learning-loop-friendly view."""
    lines: list[str] = []
    src = config.agmem_dir(cwd)
    n_q = len(result.session.queries)
    state = "new session" if result.is_new_session else f"session · {n_q} queries"
    lines.append(f"<!-- agmem ask · {state} · {src} -->")
    lines.append(f"# Q: {result.query}")
    lines.append("")

    if not result.top:
        lines.append("No relevant memories found.")
        lines.append("")
        return "\n".join(lines)

    for i, (entry, score) in enumerate(result.top, 1):
        is_section = bool(entry.source_ref) and "#" in (entry.source_ref or "")
        text = _condense_section_text(entry.text) if is_section else entry.text
        lines.append(f"{i}. {text}")
        meta_bits = [entry.kind, entry.source]
        ref = _format_ref(entry)
        if ref:
            meta_bits.append(f"ref: {ref}")
        if entry.source_commit:
            meta_bits.append(f"commit: {entry.source_commit[:12]}")
        meta_bits.append(f"score: {score:.1f}")
        lines.append(f"   ({' · '.join(meta_bits)})")
        lines.append("")

    has_suggestions = result.sibling_suggestions or result.tag_suggestions
    if has_suggestions:
        lines.append("## Haven't seen yet")
        lines.append("")
        for entry in result.sibling_suggestions:
            ref = entry.source_ref or ""
            anchor = ref.split("#", 1)[1] if "#" in ref else ref
            preview = anchor.replace("-", " ")
            lines.append(f"- `{ref}` — drill: `agmem ask \"{preview}\"`")
        if result.tag_suggestions:
            tags_str = ", ".join(f"`{t}` ({n})" for t, n in result.tag_suggestions)
            lines.append(f"- nearby tags: {tags_str}")
        lines.append("")

    if not result.is_new_session:
        last_q = [q.q for q in result.session.queries[-3:-1]]
        if last_q:
            lines.append("## Session so far")
            lines.append("")
            lines.append("Recent queries: " + " · ".join(f'"{q}"' for q in last_q))
            lines.append(f"Files touched: {len({_file_of(r) for r in result.session.seen_refs if r})}")
            lines.append("")

    return "\n".join(lines)


def render_history(session: AskSession | None) -> str:
    if session is None or not session.queries:
        return "No active ask session.\n"
    lines = [f"# Ask session — started {session.started_at}", ""]
    for i, q in enumerate(session.queries, 1):
        lines.append(f"{i}. \"{q.q}\"  ({q.ts})")
        for ref in q.returned_refs:
            lines.append(f"   → {ref}")
    lines.append("")
    lines.append(f"Files touched: {len({_file_of(r) for r in session.seen_refs if r})}")
    lines.append(f"Tags accumulated: {len(session.seen_tags)}")
    return "\n".join(lines) + "\n"
