"""Convert LongMemEval haystack sessions to agmem MemoryEntry objects.

Each session becomes one entry. Sessions are structured as parallel arrays:
``haystack_session_ids``, ``haystack_dates``, and ``haystack_sessions``,
where each session is a list of JSON-serialised turn dicts.

Note on ``source_ref``: in LongMemEval every gold session_id starts with the
prefix ``answer_`` while distractors carry ``sharegpt_`` / ``ultrachat_``
prefixes. Putting the raw session_id into ``source_ref`` would inject the
literal token ``answer`` into the BM25 corpus of gold entries only (×3 from
the path weight + ×2 from basename), creating a one-token leak. We use an
opaque positional id (``s000``, ``s001``, …) in ``source_ref`` instead;
``entry.id`` keeps the original session_id so the bench can still match
against ``answer_session_ids``.
"""

from __future__ import annotations

import json as _json

from agmem.store import MemoryEntry


def session_to_entry(
    session_turns: list,
    session_id: str,
    session_date: str,
    question_id: str,
    position: int,
) -> MemoryEntry:
    body_lines: list[str] = []
    for turn_str in session_turns:
        try:
            turn = _json.loads(turn_str) if isinstance(turn_str, str) else turn_str
        except (_json.JSONDecodeError, TypeError):
            continue
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        body_lines.append(f"{role}: {content}")

    return MemoryEntry(
        id=session_id,
        ts=session_date or "2024-01-01T00:00:00Z",
        text="\n".join(body_lines),
        source="index",
        source_ref=f"lme/{question_id}/s{position:03d}",
        tags=[],
        kind="fact",
    )


def question_to_corpus(question: dict) -> list[MemoryEntry]:
    qid = question["question_id"]
    session_ids = question.get("haystack_session_ids", [])
    session_dates = question.get("haystack_dates", [])
    session_turns_list = question.get("haystack_sessions", [])

    entries = []
    for i, (sid, sdate, sturns) in enumerate(zip(session_ids, session_dates, session_turns_list)):
        entries.append(session_to_entry(sturns, sid, sdate, qid, i))
    return entries
