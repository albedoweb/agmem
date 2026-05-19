"""Tests for testq fixture-based regression suite (D1+D2) and snapshot/diff (Direction 7)."""

import tempfile
from pathlib import Path

import pytest

from agmem.store import append_entry, create_entry
from agmem.testq import (
    Snapshot,
    SnapshotEntry,
    SnapshotQuestion,
    _compute_drift,
    _safe_snapshot_name,
    diff_against_snapshot,
    record_snapshot,
    run_testq,
    snapshots_dir,
)


def _patch_store(monkeypatch, root: Path) -> None:
    monkeypatch.setattr("agmem.config.find_repo_root", lambda cwd=None: root)
    monkeypatch.setattr("agmem.store.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: root / ".agmem" / "memories.jsonl",
    )
    monkeypatch.setattr("agmem.testq.config.agmem_dir", lambda cwd=None: root / ".agmem")
    monkeypatch.setattr("agmem.testq.config.find_repo_root", lambda cwd=None: root)
    (root / ".agmem").mkdir(exist_ok=True)


@pytest.fixture
def repo(monkeypatch):
    root = Path(tempfile.mkdtemp())
    _patch_store(monkeypatch, root)

    append_entry(create_entry(
        "Reuse existing terraform modules instead of writing aws_* resources from scratch.",
        kind="rule",
        tags=["terraform", "modules"],
        source_ref="terraform/modules/aws",
    ))
    append_entry(create_entry(
        "S3 module variables: s3_bucket_name, mandatory_tags, kms_deletion_window_in_days",
        kind="fact",
        tags=["terraform", "s3"],
        source_ref="terraform/modules/aws/s3/s3.tf",
    ))
    append_entry(create_entry(
        "Lambda function pattern: use module terraform/modules/aws/lambda",
        kind="pattern",
        tags=["terraform", "lambda"],
        source_ref="terraform/modules/aws/lambda",
    ))
    return root


def _write_fixture(root: Path, content: str) -> None:
    (root / ".agmem" / "testq.yaml").write_text(content)


def test_no_fixture_returns_error(repo: Path):
    result = run_testq()
    assert result.error is not None
    assert "No fixture" in result.error


def test_passing_question(repo: Path):
    _write_fixture(repo, """
- question: "create new s3 bucket"
  top_n: 5
  must_match:
    - kind: rule
    - source_ref_prefix: "terraform/modules/aws/s3"
""")
    result = run_testq()
    assert result.error is None
    assert len(result.passed) == 1
    assert len(result.failed) == 0


def test_failing_question_reports_missing_constraint(repo: Path):
    _write_fixture(repo, """
- question: "set up CI/CD pipeline"
  top_n: 3
  must_match:
    - tag: cicd
    - text_substring: "pipeline"
""")
    result = run_testq()
    assert len(result.failed) == 1
    failure = result.failed[0]
    assert failure.question == "set up CI/CD pipeline"
    assert any("tag" in c or "cicd" in str(c) for c in failure.missing)


def test_text_substring_constraint(repo: Path):
    _write_fixture(repo, """
- question: "s3 module variables"
  top_n: 3
  must_match:
    - text_substring: "kms_deletion_window_in_days"
""")
    result = run_testq()
    assert len(result.passed) == 1


def test_kind_constraint(repo: Path):
    _write_fixture(repo, """
- question: "lambda module"
  top_n: 3
  must_match:
    - kind: pattern
""")
    result = run_testq()
    assert len(result.passed) == 1


def test_invalid_yaml(repo: Path):
    _write_fixture(repo, "this is: not valid: yaml: at: all: ::")
    result = run_testq()
    assert result.error is not None
    assert "YAML" in result.error or "yaml" in result.error.lower()


def test_yaml_must_be_list(repo: Path):
    _write_fixture(repo, "question: foo")
    result = run_testq()
    assert result.error is not None
    assert "list" in result.error.lower()


def test_multiple_questions_mixed(repo: Path):
    _write_fixture(repo, """
- question: "create new s3 bucket"
  top_n: 5
  must_match:
    - kind: rule
- question: "deploy unrelated thing"
  top_n: 2
  must_match:
    - text_substring: "completely unrelated zzzzzz"
""")
    result = run_testq()
    assert len(result.passed) == 1
    assert len(result.failed) == 1
    assert result.total == 2


# ---------- snapshot / diff tests (Direction 7) ----------

def test_safe_snapshot_name():
    assert _safe_snapshot_name("my snapshot") == "my_snapshot"
    assert _safe_snapshot_name("foo/bar") == "foo_bar"
    assert _safe_snapshot_name("good-name_v2.0") == "good-name_v2.0"
    auto = _safe_snapshot_name(None)
    assert len(auto) >= 8  # timestamp


def test_record_snapshot_creates_yaml(repo: Path):
    _write_fixture(repo, """
- question: "create new s3 bucket"
  top_n: 3
  must_match:
    - kind: rule
""")
    path, snap = record_snapshot("baseline-1")
    assert path is not None
    assert path.exists()
    assert isinstance(snap, Snapshot)
    assert snap.name == "baseline-1"
    assert len(snap.questions) == 1
    assert snap.questions[0].question == "create new s3 bucket"
    assert all(r.rank > 0 for r in snap.questions[0].results)


def test_record_snapshot_no_fixture_returns_error(repo: Path):
    path, err = record_snapshot("no-fixture")
    assert path is None
    assert isinstance(err, str)
    assert "fixture" in err.lower()


def test_diff_no_changes_when_memory_static(repo: Path):
    _write_fixture(repo, """
- question: "S3 module variables"
  top_n: 3
""")
    record_snapshot("snap1")
    result = diff_against_snapshot("snap1")
    assert result.error is None
    assert result.changed_count == 0
    assert all(not d.has_changes for d in result.drifts)


def test_diff_detects_added_entry(repo: Path):
    _write_fixture(repo, """
- question: "redis cache cluster"
  top_n: 3
""")
    record_snapshot("before")
    # Add a new entry that matches the query.
    append_entry(create_entry(
        "Redis ElastiCache module: terraform/modules/aws/elasticache",
        kind="fact",
        tags=["redis", "elasticache"],
        source_ref="terraform/modules/aws/elasticache/main.tf",
    ))
    result = diff_against_snapshot("before")
    assert result.error is None
    assert result.changed_count == 1
    drift = result.drifts[0]
    assert any("elasticache" in (e.source_ref or "") for e in drift.added)


def test_diff_picks_latest_snapshot_by_default(repo: Path):
    _write_fixture(repo, """
- question: "lambda module"
  top_n: 3
""")
    record_snapshot("old")
    import time
    time.sleep(0.01)  # ensure mtime difference
    record_snapshot("newer")
    result = diff_against_snapshot()
    assert result.error is None
    assert result.snapshot_name == "newer"


def test_diff_no_snapshot_returns_error(repo: Path):
    _write_fixture(repo, """
- question: "anything"
  top_n: 3
""")
    result = diff_against_snapshot()
    assert result.error is not None
    assert "no snapshot" in result.error.lower()


def test_diff_flags_question_added_to_fixture(repo: Path):
    _write_fixture(repo, """
- question: "old question"
  top_n: 3
""")
    record_snapshot("v1")
    _write_fixture(repo, """
- question: "old question"
  top_n: 3
- question: "brand new question"
  top_n: 3
""")
    result = diff_against_snapshot("v1")
    assert "brand new question" in result.missing_in_snapshot


def test_compute_drift_reorder_only():
    old = [
        SnapshotEntry(id="A", source_ref="a", score=10.0, kind="fact", rank=1),
        SnapshotEntry(id="B", source_ref="b", score=8.0, kind="fact", rank=2),
    ]
    new = [
        SnapshotEntry(id="B", source_ref="b", score=11.0, kind="fact", rank=1),
        SnapshotEntry(id="A", source_ref="a", score=10.0, kind="fact", rank=2),
    ]
    drift = _compute_drift("q", old, new)
    assert drift.dropped == []
    assert drift.added == []
    assert len(drift.reordered) == 2
    assert drift.has_changes is True


def test_snapshots_dir_path(repo: Path):
    assert snapshots_dir().name == "testq-snapshots"
