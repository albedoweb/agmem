"""Tests for the UserPromptSubmit hook handler (A2)."""

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import agmem.hook as hook_mod
from agmem.hook import (
    MARKER_OPEN,
    MIN_PROMPT_LEN,
    THROTTLE_TURNS,
    _count_user_turns_since_last_marker,
    run_inject_hook,
)


def _write_transcript(path: Path, lines: list[dict]) -> None:
    with open(path, "w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")


def test_throttle_returns_threshold_when_no_transcript():
    assert _count_user_turns_since_last_marker(None) == THROTTLE_TURNS
    assert _count_user_turns_since_last_marker("/no/such/file") == THROTTLE_TURNS


def test_throttle_counts_user_turns_after_marker(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        {"type": "user", "message": {"role": "user", "content": f"earlier {MARKER_OPEN} ..."}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
        {"type": "user", "message": {"role": "user", "content": "next user turn"}},
        {"type": "user", "message": {"role": "user", "content": "another user turn"}},
    ])
    # 2 user turns happened after the marker
    assert _count_user_turns_since_last_marker(str(transcript)) == 2


def test_throttle_no_marker_means_stale(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        {"type": "user", "message": {"role": "user", "content": "no marker here"}},
    ])
    assert _count_user_turns_since_last_marker(str(transcript)) == THROTTLE_TURNS


def test_inject_short_prompt_skipped(monkeypatch, capsys):
    payload = {"prompt": "hi", "transcript_path": ""}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = run_inject_hook()
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_inject_throttled_skipped(monkeypatch, capsys, tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, [
        {"type": "user", "message": {"role": "user", "content": f"{MARKER_OPEN} present"}},
    ])
    long_prompt = "x" * (MIN_PROMPT_LEN + 5)
    payload = {"prompt": long_prompt, "transcript_path": str(transcript)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    # Even though context would be built, throttle (0 turns since marker) prevents injection.
    rc = run_inject_hook()
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_inject_emits_block_when_stale(monkeypatch, capsys):
    long_prompt = "please help me with something tricky in the repo"
    payload = {"prompt": long_prompt, "transcript_path": ""}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
            stdout = "# Context for: x\n\n## Facts\n- a fact\n"
        return Result()

    monkeypatch.setattr(hook_mod.subprocess, "run", fake_run)

    rc = run_inject_hook()
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    block = parsed["hookSpecificOutput"]["additionalContext"]
    assert block.startswith(MARKER_OPEN)
    assert "## Facts" in block


def test_inject_handles_subprocess_failure(monkeypatch, capsys):
    long_prompt = "long enough prompt to pass min check please"
    payload = {"prompt": long_prompt, "transcript_path": ""}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 1
            stdout = ""
        return Result()

    monkeypatch.setattr(hook_mod.subprocess, "run", fake_run)
    rc = run_inject_hook()
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""
