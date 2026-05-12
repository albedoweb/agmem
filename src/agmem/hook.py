"""UserPromptSubmit hook: auto-inject `<agmem-context>` block into user prompts.

Throttles: only injects if the last N user turns have no agmem-context marker.
Read Claude Code hook payload from stdin, emit hookSpecificOutput.additionalContext on stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MARKER_OPEN = "<agmem-context>"
MARKER_CLOSE = "</agmem-context>"
THROTTLE_TURNS = 5
MIN_PROMPT_LEN = 30
CONTEXT_LIMIT = 8
CONTEXT_TIMEOUT_SEC = 5


def _count_user_turns_since_last_marker(transcript_path: str | None) -> int:
    """Return how many user turns happened after the last `<agmem-context>` marker.

    If no marker is found, returns THROTTLE_TURNS so the caller treats it as stale and injects.
    """
    if not transcript_path:
        return THROTTLE_TURNS
    path = Path(transcript_path)
    if not path.exists():
        return THROTTLE_TURNS
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return THROTTLE_TURNS

    user_turns = 0
    for line in reversed(lines):
        if not line.strip():
            continue
        if MARKER_OPEN in line:
            return user_turns
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "user":
            msg = obj.get("message") or {}
            if msg.get("role") == "user":
                user_turns += 1
    return THROTTLE_TURNS


def _build_context_block(prompt: str, cwd: str | None) -> str | None:
    try:
        result = subprocess.run(
            ["agmem", "context", prompt, "-n", str(CONTEXT_LIMIT)],
            capture_output=True,
            text=True,
            timeout=CONTEXT_TIMEOUT_SEC,
            cwd=cwd,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    body = result.stdout.strip()
    if not body:
        return None
    return f"{MARKER_OPEN}\n{body}\n{MARKER_CLOSE}"


def run_inject_hook() -> int:
    """Read hook payload from stdin, optionally emit additionalContext.

    Returns process exit code.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    prompt = (payload.get("prompt") or "").strip()
    if len(prompt) < MIN_PROMPT_LEN:
        return 0

    transcript_path = payload.get("transcript_path")
    if _count_user_turns_since_last_marker(transcript_path) < THROTTLE_TURNS:
        return 0

    cwd = payload.get("cwd")
    block = _build_context_block(prompt, cwd)
    if not block:
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": block,
        }
    }
    sys.stdout.write(json.dumps(output))
    return 0
