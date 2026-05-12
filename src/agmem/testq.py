"""Retrieval regression suite: assert that questions surface expected memories.

Fixture format (`.agmem/testq.yaml`):

    - question: "create new s3 bucket"
      top_n: 6
      must_match:
        - kind: rule
        - source_ref_prefix: "terraform/modules/aws/s3"
        - text_substring: "Reuse existing terraform modules"
        - tag: "terraform"
        - id: "01HXYZ..."

A question PASSES when every must_match constraint is satisfied by at least one
result in the top-N of `agmem context`. Otherwise it FAILS with the missing
constraints reported.

Snapshots (`.agmem/testq-snapshots/<name>.yaml`) capture full top-N rankings per
question so we can detect retrieval drift across indexer/search changes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config
from .search import search_filtered
from .store import MemoryEntry

DEFAULT_TOP_N = 8
SNAPSHOTS_DIRNAME = "testq-snapshots"


@dataclass
class QuestionFailure:
    question: str
    top_n: int
    missing: list[dict]
    top_results: list[str]  # short refs of what we got back


@dataclass
class TestQResult:
    passed: list[tuple[str, int]] = field(default_factory=list)
    failed: list[QuestionFailure] = field(default_factory=list)
    fixture_path: Path | None = None
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.failed)


def _matches_constraint(entry: MemoryEntry, constraint: dict) -> bool:
    if "text_substring" in constraint:
        if str(constraint["text_substring"]).lower() not in entry.text.lower():
            return False
    if "source_ref" in constraint:
        if (entry.source_ref or "") != constraint["source_ref"]:
            return False
    if "source_ref_prefix" in constraint:
        if not (entry.source_ref or "").startswith(constraint["source_ref_prefix"]):
            return False
    if "tag" in constraint:
        if constraint["tag"] not in entry.tags:
            return False
    if "kind" in constraint:
        if entry.kind != constraint["kind"]:
            return False
    if "id" in constraint:
        if entry.id != constraint["id"]:
            return False
    return True


def fixture_path(cwd: str | None = None) -> Path:
    return config.agmem_dir(cwd) / "testq.yaml"


def snapshots_dir(cwd: str | None = None) -> Path:
    return config.agmem_dir(cwd) / SNAPSHOTS_DIRNAME


def _safe_snapshot_name(name: str | None) -> str:
    if not name:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return cleaned or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


@dataclass
class SnapshotEntry:
    id: str
    source_ref: str | None
    score: float
    kind: str
    rank: int


@dataclass
class SnapshotQuestion:
    question: str
    top_n: int
    results: list[SnapshotEntry]


@dataclass
class Snapshot:
    name: str
    recorded_at: str
    commit: str | None
    questions: list[SnapshotQuestion]


@dataclass
class QuestionDrift:
    question: str
    dropped: list[SnapshotEntry] = field(default_factory=list)
    added: list[SnapshotEntry] = field(default_factory=list)
    reordered: list[tuple[str, int, int]] = field(default_factory=list)  # (id, old_rank, new_rank)
    score_changes: list[tuple[str, float, float]] = field(default_factory=list)  # (id, old, new)

    @property
    def has_changes(self) -> bool:
        return bool(self.dropped or self.added or self.reordered)


@dataclass
class DiffResult:
    snapshot_name: str
    snapshot_path: Path
    snapshot_recorded_at: str
    snapshot_commit: str | None
    drifts: list[QuestionDrift] = field(default_factory=list)
    missing_in_snapshot: list[str] = field(default_factory=list)  # questions in fixture not in snapshot
    missing_in_fixture: list[str] = field(default_factory=list)  # questions in snapshot not in fixture
    error: str | None = None

    @property
    def changed_count(self) -> int:
        return sum(1 for d in self.drifts if d.has_changes)


def _query_snapshot_results(
    question: str,
    top_n: int,
    cwd: str | None,
) -> list[SnapshotEntry]:
    hits = search_filtered(question, limit=top_n, cwd=cwd)
    out: list[SnapshotEntry] = []
    for rank, (entry, score) in enumerate(hits, start=1):
        out.append(SnapshotEntry(
            id=entry.id,
            source_ref=entry.source_ref,
            score=round(float(score), 4),
            kind=entry.kind,
            rank=rank,
        ))
    return out


def _git_head_short(cwd: str | None) -> str | None:
    import subprocess
    repo = config.find_repo_root(cwd)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=2, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def record_snapshot(
    name: str | None = None,
    *,
    cwd: str | None = None,
) -> tuple[Path, Snapshot] | tuple[None, str]:
    """Capture top-N for each fixture question into .agmem/testq-snapshots/<name>.yaml.

    Returns (path, snapshot) on success, or (None, error_message) on failure.
    """
    fp = fixture_path(cwd)
    if not fp.exists():
        return None, f"No fixture at {fp}"
    try:
        raw = yaml.safe_load(fp.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML: {exc}"
    if not isinstance(raw, list):
        return None, "testq.yaml must be a YAML list"

    questions: list[SnapshotQuestion] = []
    for raw_q in raw:
        if not isinstance(raw_q, dict) or "question" not in raw_q:
            continue
        q = str(raw_q["question"])
        top_n = int(raw_q.get("top_n", DEFAULT_TOP_N))
        questions.append(SnapshotQuestion(
            question=q,
            top_n=top_n,
            results=_query_snapshot_results(q, top_n, cwd),
        ))

    safe_name = _safe_snapshot_name(name)
    snap = Snapshot(
        name=safe_name,
        recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        commit=_git_head_short(cwd),
        questions=questions,
    )

    snap_dir = snapshots_dir(cwd)
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{safe_name}.yaml"
    payload = {
        "name": snap.name,
        "recorded_at": snap.recorded_at,
        "commit": snap.commit,
        "questions": [
            {
                "question": q.question,
                "top_n": q.top_n,
                "results": [asdict(r) for r in q.results],
            }
            for q in snap.questions
        ],
    }
    snap_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return snap_path, snap


def _load_snapshot(path: Path) -> Snapshot | str:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return f"Invalid YAML in snapshot: {exc}"
    if not isinstance(raw, dict):
        return "Snapshot must be a YAML mapping"
    questions = []
    for raw_q in raw.get("questions", []) or []:
        if not isinstance(raw_q, dict):
            continue
        results = []
        for r in raw_q.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            results.append(SnapshotEntry(
                id=str(r.get("id", "")),
                source_ref=r.get("source_ref"),
                score=float(r.get("score", 0.0)),
                kind=str(r.get("kind", "fact")),
                rank=int(r.get("rank", 0)),
            ))
        questions.append(SnapshotQuestion(
            question=str(raw_q.get("question", "")),
            top_n=int(raw_q.get("top_n", DEFAULT_TOP_N)),
            results=results,
        ))
    return Snapshot(
        name=str(raw.get("name", path.stem)),
        recorded_at=str(raw.get("recorded_at", "")),
        commit=raw.get("commit"),
        questions=questions,
    )


def _resolve_snapshot_path(name: str | None, cwd: str | None) -> Path | None:
    snap_dir = snapshots_dir(cwd)
    if not snap_dir.exists():
        return None
    if name:
        candidate = snap_dir / f"{name}.yaml"
        if candidate.exists():
            return candidate
        candidate = snap_dir / name
        if candidate.exists():
            return candidate
        return None
    # Default: most recent snapshot.
    snaps = sorted(snap_dir.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    return snaps[0] if snaps else None


def diff_against_snapshot(
    name: str | None = None,
    *,
    cwd: str | None = None,
) -> DiffResult:
    snap_path = _resolve_snapshot_path(name, cwd)
    if snap_path is None:
        result = DiffResult(
            snapshot_name=name or "<latest>",
            snapshot_path=Path(""),
            snapshot_recorded_at="",
            snapshot_commit=None,
            error=f"No snapshot found (looked in {snapshots_dir(cwd)})",
        )
        return result

    loaded = _load_snapshot(snap_path)
    if isinstance(loaded, str):
        return DiffResult(
            snapshot_name=name or snap_path.stem,
            snapshot_path=snap_path,
            snapshot_recorded_at="",
            snapshot_commit=None,
            error=loaded,
        )
    snapshot = loaded

    fp = fixture_path(cwd)
    fixture_questions: list[tuple[str, int]] = []
    if fp.exists():
        try:
            raw = yaml.safe_load(fp.read_text(encoding="utf-8")) or []
            if isinstance(raw, list):
                for raw_q in raw:
                    if isinstance(raw_q, dict) and "question" in raw_q:
                        fixture_questions.append((
                            str(raw_q["question"]),
                            int(raw_q.get("top_n", DEFAULT_TOP_N)),
                        ))
        except yaml.YAMLError:
            pass

    result = DiffResult(
        snapshot_name=snapshot.name,
        snapshot_path=snap_path,
        snapshot_recorded_at=snapshot.recorded_at,
        snapshot_commit=snapshot.commit,
    )

    snap_by_q = {q.question: q for q in snapshot.questions}
    fixture_q_set = {q for q, _ in fixture_questions}

    for question, top_n in fixture_questions:
        if question not in snap_by_q:
            result.missing_in_snapshot.append(question)
            continue
        snap_q = snap_by_q[question]
        current = _query_snapshot_results(question, top_n, cwd)
        drift = _compute_drift(question, snap_q.results, current)
        result.drifts.append(drift)

    for question in snap_by_q:
        if question not in fixture_q_set:
            result.missing_in_fixture.append(question)

    return result


def _compute_drift(
    question: str,
    old_results: list[SnapshotEntry],
    new_results: list[SnapshotEntry],
) -> QuestionDrift:
    drift = QuestionDrift(question=question)
    old_by_id = {r.id: r for r in old_results}
    new_by_id = {r.id: r for r in new_results}

    for old in old_results:
        if old.id not in new_by_id:
            drift.dropped.append(old)
    for new in new_results:
        if new.id not in old_by_id:
            drift.added.append(new)
    for entry_id, old in old_by_id.items():
        match = new_by_id.get(entry_id)
        if match is None:
            continue
        if match.rank != old.rank:
            drift.reordered.append((entry_id, old.rank, match.rank))
        if abs(match.score - old.score) > 0.01:
            drift.score_changes.append((entry_id, old.score, match.score))
    return drift


def run_testq(cwd: str | None = None) -> TestQResult:
    path = fixture_path(cwd)
    result = TestQResult(fixture_path=path)
    if not path.exists():
        result.error = f"No fixture at {path}"
        return result
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        result.error = f"Invalid YAML: {exc}"
        return result
    if not isinstance(raw, list):
        result.error = "testq.yaml must be a YAML list of question objects"
        return result

    for raw_q in raw:
        if not isinstance(raw_q, dict) or "question" not in raw_q:
            continue
        question = str(raw_q["question"])
        top_n = int(raw_q.get("top_n", DEFAULT_TOP_N))
        constraints = raw_q.get("must_match") or []

        hits = search_filtered(question, limit=top_n, cwd=cwd)
        entries = [e for e, _ in hits]
        missing = [c for c in constraints if not any(_matches_constraint(e, c) for e in entries)]

        if missing:
            result.failed.append(QuestionFailure(
                question=question,
                top_n=top_n,
                missing=missing,
                top_results=[(e.source_ref or e.id[:8]) for e in entries[:5]],
            ))
        else:
            result.passed.append((question, top_n))

    return result
