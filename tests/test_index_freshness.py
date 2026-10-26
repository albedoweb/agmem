"""Incremental indexing and watcher recovery must agree with current source."""

import subprocess
import pytest

from agmem.indexer import apply_paths, run_index, run_update
from agmem.search import search_filtered
from agmem.store import append_entry, create_entry, read_all_entries
from agmem.watch import RepoWatcher


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=root, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".agmem/\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src/one.py").write_text("def original():\n    pass\n")
    (tmp_path / "src/two.py").write_text("def sibling():\n    pass\n")
    (tmp_path / "docs/guide.md").write_text(_document())
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    run_index(str(tmp_path))
    return tmp_path


def _document():
    return "# Guide\n" + "".join(
        f"\n## Topic {i}\n" + f"quartzlegacy{i} configuration details. " * 30 + "\n"
        for i in range(4)
    )


def _refresh(root, mode, modified, deleted):
    if mode == "paths":
        return apply_paths(modified, deleted, cwd=str(root))
    _git(root, "add", "-A")
    return run_update("HEAD", cwd=str(root))


def _indexed(root):
    # The repository-wide aggregate is intentionally not updated incrementally.
    return sorted(
        (e.source_ref, e.text, tuple(e.tags), e.source_hash)
        for e in read_all_entries(str(root))
        if e.source == "index" and "summary" not in e.tags
    )


@pytest.mark.parametrize("mode", ["paths", "git"])
@pytest.mark.parametrize("change", ["delete", "rename", "shrink", "remove-section"])
def test_markdown_refresh_matches_full_index(repo, mode, change):
    original = repo / "docs/guide.md"
    modified, deleted = [], []
    manual = create_entry("Keep this manually recorded rule", kind="rule",
                          source_ref="docs/guide.md#topic-0")
    append_entry(manual, str(repo))
    unrelated = next(e for e in read_all_entries(str(repo)) if e.source_ref == "src/two.py")
    if change == "delete":
        original.unlink()
        deleted = ["docs/guide.md"]
    elif change == "rename":
        original.rename(repo / "docs/moved.md")
        modified, deleted = ["docs/moved.md"], ["docs/guide.md"]
    elif change == "shrink":
        original.write_text("# Guide\n\nNew short content.\n")
        modified = ["docs/guide.md"]
    else:
        original.write_text(_document().replace("## Topic 0", "## Replacement")
                            .replace("quartzlegacy0", "quartznew"))
        modified = ["docs/guide.md"]

    _refresh(repo, mode, modified, deleted)
    entries = read_all_entries(str(repo))
    assert next(e for e in entries if e.id == manual.id).to_dict() == manual.to_dict()
    assert next(e for e in entries if e.id == unrelated.id).to_dict() == unrelated.to_dict()
    assert not any(e.source == "index" and e.source_ref == "docs/guide.md#topic-0"
                   for e in entries)
    hits = search_filtered("quartzlegacy0", cwd=str(repo))
    if change != "rename":
        assert not any("quartzlegacy0" in e.text for e, _ in hits)
    incremental = _indexed(repo)
    run_index(str(repo))
    assert incremental == _indexed(repo)


@pytest.mark.parametrize("mode", ["paths", "git"])
@pytest.mark.parametrize("change", ["edit", "delete-last", "unparseable"])
def test_directory_and_file_refresh_matches_full_index(repo, mode, change):
    if change == "edit":
        (repo / "src/one.py").write_text("def renamed_function():\n    return 42\n")
        modified, deleted = ["src/one.py"], []
    elif change == "delete-last":
        (repo / "src/one.py").unlink()
        (repo / "src/two.py").unlink()
        (repo / "src").rmdir()
        modified, deleted = [], ["src/one.py", "src/two.py"]
    else:
        (repo / "src/one.py").write_text("# All symbols removed\n")
        modified, deleted = ["src/one.py"], []
    _refresh(repo, mode, modified, deleted)
    incremental = _indexed(repo)
    run_index(str(repo))
    assert incremental == _indexed(repo)


def test_watch_restart_reconciles_offline_changes(repo):
    watcher = RepoWatcher(str(repo))
    watcher.bootstrap()
    (repo / "src/one.py").write_text("def after_restart():\n    return 42\n")
    (repo / "docs/guide.md").unlink()
    (repo / "src/new.py").write_text("def created_offline():\n    pass\n")
    restarted = RepoWatcher(str(repo))
    restarted.bootstrap()
    entries = read_all_entries(str(repo))
    assert any("after_restart" in e.text for e in entries)
    assert any("created_offline" in e.text for e in entries)
    assert not any((e.source_ref or "").startswith("docs/guide.md") for e in entries)
    assert restarted.tick()["events"] == 0
    recovered = _indexed(repo)
    run_index(str(repo))
    assert recovered == _indexed(repo)


def test_watch_retries_failed_tick(repo, monkeypatch):
    import agmem.watch as watch

    watcher = RepoWatcher(str(repo))
    watcher.bootstrap()
    (repo / "src/new.py").write_text("def retry_success():\n    pass\n")
    real_apply = watch.apply_paths

    def fail(*args, **kwargs):
        raise OSError("temporary write failure")

    monkeypatch.setattr(watch, "apply_paths", fail)
    with pytest.raises(OSError):
        watcher.tick()
    monkeypatch.setattr(watch, "apply_paths", real_apply)
    watcher.tick()
    assert any("retry_success" in e.text for e in read_all_entries(str(repo)))


def test_watch_detects_changes_during_startup_reindex(repo, monkeypatch):
    import agmem.watch as watch

    real_index = watch.run_index

    def index_then_edit(*args, **kwargs):
        result = real_index(*args, **kwargs)
        (repo / "src/during.py").write_text("def during_startup():\n    pass\n")
        return result

    monkeypatch.setattr(watch, "run_index", index_then_edit)
    watcher = RepoWatcher(str(repo))
    watcher.bootstrap()
    watcher.tick()
    assert any("during_startup" in e.text for e in read_all_entries(str(repo)))


def test_stale_delete_event_does_not_remove_recreated_file(repo):
    from agmem.watch import apply_queue_once, enqueue

    enqueue(str(repo), "src/one.py", "deleted")
    (repo / "src/one.py").write_text("def recreated():\n    pass\n")
    apply_queue_once(str(repo))
    assert any("recreated" in e.text for e in read_all_entries(str(repo)))
    incremental = _indexed(repo)
    run_index(str(repo))
    assert incremental == _indexed(repo)
