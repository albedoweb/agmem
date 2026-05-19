from __future__ import annotations

import json
import statistics
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agmem.agmem_eval import (
    EvalPair,
    EvalReport,
    EvalScore,
    _CD_RE,
    _QUERY_RE,
    _TAG_RE,
    _compute_hit_metrics,
    _extract_cd_cwd,
    _get_run_cwd,
    _gold_mentioned_in_text,
    _is_excluded_path,
    _normalize_path,
    _normalize_worktree_path,
    extract_eval_pairs,
    extract_gold_files,
    is_agmem_context_call,
    run_eval,
    score_pair,
)


def _make_event(event: str, tool_name: str | None = None, tool_input: dict | None = None, cwd: str | None = None):
    e = {"event": event}
    if tool_name:
        e["tool_name"] = tool_name
    if tool_input:
        e["tool_input"] = tool_input
    if cwd:
        e["cwd"] = cwd
    return e


def _make_run_started(cwd="/Users/test/repo"):
    return {"event": "run_started", "cwd": cwd}


def _make_bash_cmd(command, description="test"):
    return _make_event("tool_called", tool_name="Bash", tool_input={
        "command": command, "description": description,
    })


def _make_read(path):
    return _make_event("tool_called", tool_name="Read", tool_input={"file_path": path})


def _make_edit(path):
    return _make_event("tool_called", tool_name="Edit", tool_input={
        "file_path": path, "old_string": "a", "new_string": "b",
    })


def _make_write(path):
    return _make_event("tool_called", tool_name="Write", tool_input={
        "file_path": path, "content": "x",
    })


def _make_grep(path, pattern="test"):
    return _make_event("tool_called", tool_name="Grep", tool_input={
        "path": path, "pattern": pattern,
    })


class TestIsAgmemContextCall:
    def test_simple_double_quotes(self):
        e = _make_bash_cmd('agmem context "find the config file" -n 5')
        result = is_agmem_context_call(e)
        assert result == ("find the config file", None)

    def test_single_quotes(self):
        e = _make_bash_cmd("agmem context 'find the config file' -n 5")
        result = is_agmem_context_call(e)
        assert result == ("find the config file", None)

    def test_with_tag(self):
        e = _make_bash_cmd('agmem context "find config" --tag mytruv -n 8')
        result = is_agmem_context_call(e)
        assert result == ("find config", "mytruv")

    def test_piped_output(self):
        e = _make_bash_cmd('agmem context "find config" -n 5 2>&1 | head -100')
        result = is_agmem_context_call(e)
        assert result == ("find config", None)

    def test_with_session(self):
        e = _make_bash_cmd('agmem context "find config" -n 8 --session')
        result = is_agmem_context_call(e)
        assert result == ("find config", None)

    def test_not_agmem_context(self):
        e = _make_bash_cmd("agmem hot && ls")
        assert is_agmem_context_call(e) is None

    def test_not_bash_tool(self):
        e = _make_event("tool_called", tool_name="Read", tool_input={"file_path": "/x"})
        assert is_agmem_context_call(e) is None

    def test_empty_query(self):
        e = _make_bash_cmd('agmem context ""')
        assert is_agmem_context_call(e) is None

    def test_escaped_quotes_in_query(self):
        e = _make_bash_cmd("agmem context \"it's ok\" -n 5")
        result = is_agmem_context_call(e)
        assert result is not None
        assert result[0] == "it's ok"


class TestQueryRegex:
    def test_standard(self):
        m = _QUERY_RE.search('agmem context "hello world" -n 5')
        assert m and m.group(1) == "hello world"

    def test_single_quotes(self):
        m = _QUERY_RE.search("agmem context 'hello world' -n 5")
        assert m and (m.group(1) or m.group(2)) == "hello world"

    def test_multiline_command(self):
        cmd = 'cd /x && \\\n agmem context "hello"'
        m = _QUERY_RE.search(cmd)
        assert m and m.group(1) == "hello"

    def test_no_quotes_no_match(self):
        assert _QUERY_RE.search("agmem context hello") is None


class TestTagRegex:
    def test_extract_tag(self):
        m = _TAG_RE.search('agmem context "x" --tag mytruv -n 8')
        assert m and m.group(1) == "mytruv"

    def test_no_tag(self):
        assert _TAG_RE.search('agmem context "x" -n 5') is None


class TestCdRegex:
    def test_extract_cd(self):
        m = _CD_RE.match("cd /Users/test/repo && agmem context 'x'")
        assert m and m.group(1) == "/Users/test/repo"

    def test_no_cd(self):
        assert _CD_RE.match("agmem context 'x'") is None


class TestExtractCdCwd:
    def test_with_cd_prefix(self):
        result = _extract_cd_cwd("cd /tmp && agmem context 'x'", "/fallback")
        assert Path(result) == Path("/tmp").resolve()

    def test_without_cd_prefix(self):
        result = _extract_cd_cwd("agmem context 'x'", "/fallback")
        assert result == "/fallback"

    def test_relative_cd(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        result = _extract_cd_cwd(f"cd sub && agmem context 'x'", str(tmp_path))
        assert Path(result) == sub.resolve()


class TestGetRunCwd:
    def test_extracts_from_run_started(self):
        events = [
            _make_run_started("/Users/test/repo"),
            _make_bash_cmd("ls"),
        ]
        assert _get_run_cwd(events) == "/Users/test/repo"

    def test_fallback_to_pwd(self):
        events = [_make_bash_cmd("ls")]
        import os
        assert _get_run_cwd(events) == os.getcwd()


class TestNormalizePath:
    def test_path_under_root(self):
        assert _normalize_path("/Users/test/repo/src/main.py", "/Users/test/repo") == "src/main.py"

    def test_path_outside_root(self):
        assert _normalize_path("/other/file.py", "/Users/test/repo") is None

    def test_path_equals_root(self):
        assert _normalize_path("/Users/test/repo", "/Users/test/repo") == "."


class TestExtractGoldFiles:
    def test_reads_in_window(self):
        events = [
            _make_bash_cmd("agmem context 'x'"),
            _make_read("/repo/a.py"),
            _make_read("/repo/b.py"),
            _make_edit("/repo/c.py"),
            _make_write("/repo/d.py"),
            _make_read("/repo/e.py"),
        ]
        gold = extract_gold_files(events, 0, window=4, cwd="/repo")
        assert gold == {"a.py", "b.py", "c.py", "d.py"}

    def test_respects_window_boundary(self):
        events = [
            _make_bash_cmd("agmem context 'x'"),
            _make_read("/repo/a.py"),
            _make_read("/repo/b.py"),
        ]
        gold = extract_gold_files(events, 0, window=1, cwd="/repo")
        assert gold == {"a.py"}

    def test_excludes_paths_outside_cwd(self):
        events = [
            _make_bash_cmd("agmem context 'x'"),
            _make_read("/other/file.py"),
        ]
        gold = extract_gold_files(events, 0, window=5, cwd="/repo")
        assert gold == set()

    def test_skips_non_tool_called_events(self):
        events = [
            _make_bash_cmd("agmem context 'x'"),
            {"event": "tool_output", "output": "..."},
            _make_read("/repo/a.py"),
        ]
        gold = extract_gold_files(events, 0, window=5, cwd="/repo")
        assert gold == {"a.py"}

    def test_includes_grep_path(self):
        events = [
            _make_bash_cmd("agmem context 'x'"),
            _make_grep("/repo/subdir", pattern="test"),
        ]
        gold = extract_gold_files(events, 0, window=5, cwd="/repo")
        assert gold == {"subdir"}


class TestExtractEvalPairs:
    def _write_session(self, tmp_dir: Path, events: list[dict]) -> str:
        run_dir = tmp_dir / "cc_test_session"
        run_dir.mkdir(parents=True)
        jsonl = run_dir / "events.jsonl"
        with open(jsonl, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        return str(run_dir)

    def test_single_query_with_gold(self, monkeypatch, tmp_path):
        events = [
            _make_run_started("/repo"),
            _make_bash_cmd('agmem context "find config" -n 5'),
            _make_read("/repo/src/config.py"),
            _make_edit("/repo/src/config.py"),
            _make_read("/repo/src/main.py"),
        ]
        self._write_session(tmp_path, events)

        from agmem.agmem_eval import AGENT_DIFF_RUNS_DIR
        monkeypatch.setattr("agmem.agmem_eval.AGENT_DIFF_RUNS_DIR", tmp_path)

        pairs = extract_eval_pairs()
        assert len(pairs) == 1
        p = pairs[0]
        assert p.query == "find config"
        assert "src/config.py" in p.gold_files
        assert "src/main.py" in p.gold_files

    def test_filters_short_queries(self, monkeypatch, tmp_path):
        events = [
            _make_run_started("/repo"),
            _make_bash_cmd('agmem context "hi"'),
            _make_read("/repo/a.py"),
        ]
        self._write_session(tmp_path, events)
        monkeypatch.setattr("agmem.agmem_eval.AGENT_DIFF_RUNS_DIR", tmp_path)

        pairs = extract_eval_pairs()
        assert len(pairs) == 0

    def test_filters_duplicate_queries(self, monkeypatch, tmp_path):
        events = [
            _make_run_started("/repo"),
            _make_bash_cmd('agmem context "find the config file"'),
            _make_read("/repo/a.py"),
            _make_bash_cmd('agmem context "find the config file"'),
            _make_read("/repo/b.py"),
        ]
        self._write_session(tmp_path, events)
        monkeypatch.setattr("agmem.agmem_eval.AGENT_DIFF_RUNS_DIR", tmp_path)

        pairs = extract_eval_pairs()
        assert len(pairs) == 1

    def test_empty_gold_skipped(self, monkeypatch, tmp_path):
        events = [
            _make_run_started("/repo"),
            _make_bash_cmd('agmem context "find the config file"'),
        ]
        self._write_session(tmp_path, events)
        monkeypatch.setattr("agmem.agmem_eval.AGENT_DIFF_RUNS_DIR", tmp_path)

        pairs = extract_eval_pairs()
        assert len(pairs) == 0

    def test_tag_extraction(self, monkeypatch, tmp_path):
        events = [
            _make_run_started("/repo"),
            _make_bash_cmd('agmem context "find config" --tag mytruv'),
            _make_read("/repo/src/a.py"),
        ]
        self._write_session(tmp_path, events)
        monkeypatch.setattr("agmem.agmem_eval.AGENT_DIFF_RUNS_DIR", tmp_path)

        pairs = extract_eval_pairs()
        assert len(pairs) == 1
        assert pairs[0].tag == "mytruv"

    def test_cwd_filter(self, monkeypatch, tmp_path):
        events = [
            _make_run_started("/repo_a"),
            _make_bash_cmd('agmem context "find config a"'),
            _make_read("/repo_a/a.py"),
        ]
        run_dir_b = tmp_path / "cc_session_b"
        run_dir_b.mkdir(parents=True)
        events_b = [
            _make_run_started("/repo_b"),
            _make_bash_cmd('agmem context "find config b"'),
            _make_read("/repo_b/b.py"),
        ]
        with open(run_dir_b / "events.jsonl", "w") as f:
            for e in events_b:
                f.write(json.dumps(e) + "\n")
        self._write_session(tmp_path, events)

        from agmem.agmem_eval import AGENT_DIFF_RUNS_DIR
        monkeypatch.setattr("agmem.agmem_eval.AGENT_DIFF_RUNS_DIR", tmp_path)

        pairs = extract_eval_pairs(cwd_filter="/repo_b")
        assert len(pairs) == 1
        assert pairs[0].cwd == "/repo_b"

    def test_cd_prefix_overrides_run_cwd(self, monkeypatch, tmp_path):
        events = [
            _make_run_started("/repo_main"),
            _make_bash_cmd("cd /repo_sub && agmem context 'find config' -n 5"),
            _make_read("/repo_sub/src/a.py"),
        ]
        self._write_session(tmp_path, events)
        monkeypatch.setattr("agmem.agmem_eval.AGENT_DIFF_RUNS_DIR", tmp_path)

        pairs = extract_eval_pairs()
        assert len(pairs) == 1
        assert pairs[0].cwd == "/repo_sub"


class TestScorePair:
    def test_hit_and_mrr(self, monkeypatch):
        from agmem.store import MemoryEntry

        pair = EvalPair(
            run_id="test", query="find config",
            cwd="/repo", turn=0,
            gold_files={"src/config.py", "src/main.py"},
            window_size=20,
        )

        entries = [
            MemoryEntry(id="1", ts="2024-01-01", text="Config file", source_ref="README.md", source="index"),
            MemoryEntry(id="2", ts="2024-01-01", text="Main entry", source_ref="src/main.py", source="index"),
            MemoryEntry(id="3", ts="2024-01-01", text="Config", source_ref="src/config.py", source="index"),
            MemoryEntry(id="4", ts="2024-01-01", text="Utils", source_ref="src/utils.py", source="index"),
        ]

        def mock_search_filtered(query, limit=10, tag=None, cwd=None, kind_boost=None, source_boost=None):
            results = [(e, 1.0) for e in entries[:limit]]
            return results

        monkeypatch.setattr("agmem.agmem_eval.search_filtered", mock_search_filtered)

        s = score_pair(pair)
        assert s.top_k[:4] == ["README.md", "src/main.py", "src/config.py", "src/utils.py"]
        assert s.hit_at[3] is True
        assert s.hit_at[5] is True
        assert s.recall_at[3] == 1.0   # both gold files are in top-3
        assert s.recall_at[5] == 1.0
        assert s.mrr == 0.5

    def test_no_hit(self, monkeypatch):
        from agmem.store import MemoryEntry

        pair = EvalPair(
            run_id="test", query="find config",
            cwd="/repo", turn=0,
            gold_files={"src/secret.py"},
            window_size=20,
        )

        def mock_search_filtered(query, limit=10, tag=None, cwd=None, kind_boost=None, source_boost=None):
            return [(MemoryEntry(id="1", ts="x", text="Stuff", source_ref="README.md", source="index"), 0.5)]

        monkeypatch.setattr("agmem.agmem_eval.search_filtered", mock_search_filtered)

        s = score_pair(pair)
        assert s.hit_at[5] is False
        assert s.recall_at[5] == 0.0
        assert s.mrr == 0.0


class TestEvalReport:
    def test_metrics(self):
        pairs = [
            EvalPair(run_id="a", query="q1", cwd="/r", turn=0, gold_files={"f1.py", "f2.py"}, window_size=20),
            EvalPair(run_id="b", query="q2", cwd="/r", turn=0, gold_files={"f3.py"}, window_size=20),
        ]
        scores = [
            EvalScore(pair=pairs[0], top_k=["f1.py", "f4.py"], hit_at={5: True}, recall_at={5: 0.5}, mrr=1.0),
            EvalScore(pair=pairs[1], top_k=["f4.py", "f5.py"], hit_at={5: False}, recall_at={5: 0.0}, mrr=0.0),
        ]
        report = EvalReport(pairs=pairs, scores=scores, ks=[5])
        assert report.n_pairs == 2
        assert report.coverage(5) == 0.5
        assert report.mean_recall(5) == 0.25
        assert report.mean_mrr() == 0.5

    def test_to_csv_rows(self):
        pair = EvalPair(run_id="a", query="q1", cwd="/r", turn=0, gold_files={"f1.py"}, window_size=20, tag="mytruv")
        score = EvalScore(pair=pair, top_k=["f1.py", "f2.py"], hit_at={5: True}, recall_at={5: 1.0}, mrr=1.0)
        report = EvalReport(pairs=[pair], scores=[score], ks=[5])
        rows = report.to_csv_rows()
        assert len(rows) == 1
        assert rows[0]["run_id"] == "a"
        assert rows[0]["tag"] == "mytruv"
        assert rows[0]["hit_at_5"] is True
        assert rows[0]["recall_at_5"] == 1.0
        assert "f1.py" in rows[0]["gold_files"]

    def test_to_dict(self):
        pair = EvalPair(run_id="a", query="q1", cwd="/r", turn=0, gold_files={"f1.py"}, window_size=20)
        score = EvalScore(pair=pair, top_k=["f1.py"], hit_at={5: True}, recall_at={5: 1.0}, mrr=1.0)
        report = EvalReport(pairs=[pair], scores=[score], ks=[5])
        d = report.to_dict()
        assert d["n_pairs"] == 1
        assert d["mean_mrr"] == 1.0
        assert len(d["scores"]) == 1


class TestGoldContentMention:
    def test_soft_match_full_path(self):
        from agmem.store import MemoryEntry

        gold = {"src/services.py"}
        entry = MemoryEntry(id="1", ts="x", text="See src/services.py for details", source_ref="plans/plan.md", source="index")
        hit, recall, mrr, _rank = _compute_hit_metrics([(entry, 1.0)], gold, [5])
        assert hit[5] is True
        assert recall[5] == 1.0
        assert mrr == 1.0

    def test_soft_match_basename(self):
        from agmem.store import MemoryEntry

        gold = {"src/subdir/services.py"}
        entry = MemoryEntry(id="1", ts="x", text="Implement in services.py", source_ref="plans/plan.md", source="index")
        hit, recall, mrr, _rank = _compute_hit_metrics([(entry, 1.0)], gold, [5])
        assert hit[5] is True
        assert recall[5] == 1.0

    def test_no_false_match_on_common_name(self):
        from agmem.store import MemoryEntry

        gold = {"src/special.py"}
        entry = MemoryEntry(id="1", ts="x", text="General utilities", source_ref="README.md", source="index")
        hit, recall, mrr, _rank = _compute_hit_metrics([(entry, 1.0)], gold, [5])
        assert hit[5] is False
        assert recall[5] == 0.0

    def test_strict_match_still_works(self):
        from agmem.store import MemoryEntry

        gold = {"src/config.py"}
        entry = MemoryEntry(id="1", ts="x", text="Config stuff", source_ref="src/config.py", source="index")
        hit, recall, mrr, _rank = _compute_hit_metrics([(entry, 1.0)], gold, [5])
        assert hit[5] is True

    def test_mrr_with_content_mention(self):
        from agmem.store import MemoryEntry

        gold = {"src/target.py"}
        e1 = MemoryEntry(id="1", ts="x", text="Other stuff", source_ref="README.md", source="index")
        e2 = MemoryEntry(id="2", ts="x", text="See src/target.py for the fix", source_ref="plans/plan.md", source="index")
        hit, recall, mrr, _rank = _compute_hit_metrics([(e1, 1.0), (e2, 0.5)], gold, [5])
        assert hit[5] is True
        assert mrr == 1 / 2

    def test_partial_recall_with_soft_match(self):
        from agmem.store import MemoryEntry

        gold = {"src/a.py", "src/b.py"}
        entry = MemoryEntry(id="1", ts="x", text="Edit src/a.py for this", source_ref="plans/plan.md", source="index")
        hit, recall, mrr, _rank = _compute_hit_metrics([(entry, 1.0)], gold, [5])
        assert hit[5] is True
        assert recall[5] == 0.5


class TestFirstGoldRank:
    """The diagnostic field that surfaces 'almost surfaced vs never found'."""

    def test_rank_1_on_direct_path_match(self):
        from agmem.store import MemoryEntry

        gold = {"src/x.py"}
        e1 = MemoryEntry(id="1", ts="x", text="t", source_ref="src/x.py", source="index")
        _h, _r, mrr, rank = _compute_hit_metrics([(e1, 1.0)], gold, [5])
        assert rank == 1
        assert mrr == 1.0

    def test_rank_position_on_content_mention(self):
        from agmem.store import MemoryEntry

        gold = {"src/target.py"}
        e1 = MemoryEntry(id="1", ts="x", text="unrelated", source_ref="a.md", source="index")
        e2 = MemoryEntry(id="2", ts="x", text="other", source_ref="b.md", source="index")
        e3 = MemoryEntry(id="3", ts="x", text="See src/target.py here", source_ref="c.md", source="index")
        _h, _r, mrr, rank = _compute_hit_metrics([(e1, 1.0), (e2, 0.5), (e3, 0.1)], gold, [5])
        assert rank == 3
        assert mrr == 1 / 3

    def test_rank_none_when_no_match(self):
        from agmem.store import MemoryEntry

        gold = {"src/never.py"}
        e1 = MemoryEntry(id="1", ts="x", text="something else", source_ref="a.md", source="index")
        _h, _r, mrr, rank = _compute_hit_metrics([(e1, 1.0)], gold, [5])
        assert rank is None
        assert mrr == 0.0

    def test_score_pair_propagates_rank_to_evalscore(self):
        # End-to-end check that EvalScore exposes the field
        from agmem.agmem_eval import EvalPair, EvalScore
        pair = EvalPair(
            run_id="x", query="q", cwd="/", turn=1, gold_files={"src/x.py"}, window_size=20,
        )
        score = EvalScore(
            pair=pair, top_k=["src/x.py"], hit_at={5: True}, recall_at={5: 1.0}, mrr=1.0,
            first_gold_rank=1,
        )
        assert score.first_gold_rank == 1

    def test_to_dict_includes_field(self):
        from agmem.agmem_eval import EvalPair, EvalReport, EvalScore
        pair = EvalPair(
            run_id="x", query="q", cwd="/", turn=1, gold_files={"src/x.py"}, window_size=20,
        )
        score = EvalScore(
            pair=pair, top_k=["src/x.py"], hit_at={5: True}, recall_at={5: 1.0}, mrr=1.0,
            first_gold_rank=2,
        )
        report = EvalReport(pairs=[pair], scores=[score], ks=[5])
        d = report.to_dict()
        assert d["scores"][0]["first_gold_rank"] == 2

    def test_to_csv_rows_includes_field(self):
        from agmem.agmem_eval import EvalPair, EvalReport, EvalScore
        pair = EvalPair(
            run_id="x", query="q", cwd="/", turn=1, gold_files={"src/x.py"}, window_size=20,
        )
        score = EvalScore(
            pair=pair, top_k=["src/x.py"], hit_at={5: True}, recall_at={5: 1.0}, mrr=1.0,
            first_gold_rank=None,
        )
        report = EvalReport(pairs=[pair], scores=[score], ks=[5])
        rows = report.to_csv_rows()
        # None → empty string for CSV friendliness
        assert rows[0]["first_gold_rank"] == ""


class TestPathNormalization:
    def test_worktree_path_stripped(self):
        assert _normalize_worktree_path(".claude/worktrees/my-branch/src/app.py") == "src/app.py"

    def test_deep_worktree_path(self):
        assert _normalize_worktree_path("src/.claude/worktrees/foo/bar/baz.py") == "src/bar/baz.py"

    def test_no_worktree_unchanged(self):
        assert _normalize_worktree_path("src/app.py") == "src/app.py"

    def test_multiple_worktree_components(self):
        assert _normalize_worktree_path(
            ".claude/worktrees/a/src/.claude/worktrees/b/app.py"
        ) == "src/app.py"

    def test_is_excluded_venv(self):
        assert _is_excluded_path(".venv/lib/site-packages/pkg.py") is True

    def test_is_excluded_node_modules(self):
        assert _is_excluded_path("node_modules/react/index.js") is True

    def test_is_excluded_pycache(self):
        assert _is_excluded_path("src/__pycache__/module.pyc") is True

    def test_normal_path_not_excluded(self):
        assert _is_excluded_path("src/my_app/services.py") is False

    def test_extract_gold_excludes_venv(self):
        events = [
            _make_bash_cmd("agmem context 'x'"),
            _make_read("/repo/.venv/lib/pkg.py"),
            _make_read("/repo/src/app.py"),
        ]
        gold = extract_gold_files(events, 0, window=5, cwd="/repo")
        assert gold == {"src/app.py"}
        assert ".venv/lib/pkg.py" not in gold

    def test_extract_gold_normalizes_worktree(self):
        events = [
            _make_bash_cmd("agmem context 'x'"),
            _make_edit("/repo/.claude/worktrees/my-branch/terraform/modules/s3/main.tf"),
        ]
        gold = extract_gold_files(events, 0, window=5, cwd="/repo")
        assert gold == {"terraform/modules/s3/main.tf"}

    def test_gold_mentioned_in_text_full_path(self):
        assert _gold_mentioned_in_text("src/services.py", "Implement in src/services.py") is True

    def test_gold_mentioned_in_text_basename(self):
        assert _gold_mentioned_in_text("src/subdir/services.py", "Edit services.py to add retry") is True

    def test_gold_mentioned_in_text_no_match(self):
        assert _gold_mentioned_in_text("src/secret.py", "General utilities and helpers") is False
