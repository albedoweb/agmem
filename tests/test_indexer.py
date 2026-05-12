"""Tests for indexer module."""

import tempfile
from pathlib import Path

from agmem.indexer import _walk_files, _build_memories, run_index, FileInfo


def _make_tree(root: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)


def test_walk_files_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_tree(root, {
            "src/main.py": "print('hello')",
            "src/utils.py": "def foo(): pass",
            "tests/test_main.py": "def test(): pass",
            "README.md": "# Project",
            "pyproject.toml": "[project]\nname='test'",
            ".gitignore": "*.pyc\n",
        })

        files = _walk_files(root)
        paths = [f.path for f in files]
        assert "src/main.py" in paths
        assert "tests/test_main.py" in paths
        assert "README.md" in paths


def test_walk_files_skips_dotfiles():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_tree(root, {
            "src/main.py": "x",
            ".hidden/secret.py": "y",
            "src/.hidden.py": "z",
            ".venv/lib.py": "w",
            "node_modules/pkg/index.js": "q",
        })

        files = _walk_files(root)
        paths = [f.path for f in files]
        assert "src/main.py" in paths
        assert ".hidden/secret.py" not in paths
        assert "src/.hidden.py" not in paths
        assert ".venv/lib.py" not in paths
        assert "node_modules/pkg/index.js" not in paths


def test_walk_files_respects_gitignore():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_tree(root, {
            ".gitignore": "*.log\ndist/\n",
            "src/app.py": "x",
            "debug.log": "y",
            "dist/bundle.js": "z",
            "notes.txt": "w",
        })

        files = _walk_files(root)
        paths = [f.path for f in files]
        assert "src/app.py" in paths
        assert "debug.log" not in paths
        assert "dist/bundle.js" not in paths
        assert ".gitignore" in paths


def test_walk_files_deterministic_order():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_tree(root, {
            "z.py": "z",
            "a.py": "a",
            "m.py": "m",
        })

        first = _walk_files(root)
        second = _walk_files(root)
        assert [f.path for f in first] == [f.path for f in second]
        assert [f.path for f in first] == ["a.py", "m.py", "z.py"]


def test_build_memories():
    files = [
        FileInfo(path="src/main.py", ext=".py", size=100, directory="src"),
        FileInfo(path="src/utils.py", ext=".py", size=200, directory="src"),
        FileInfo(path="tests/test_main.py", ext=".py", size=300, directory="tests"),
        FileInfo(path="README.md", ext=".md", size=50, directory="."),
        FileInfo(path="pyproject.toml", ext=".toml", size=500, directory="."),
    ]

    entries = _build_memories(files, Path("."), commit=None, preserve_from={})
    texts = [e.text for e in entries]

    assert any("src" in t and "2 files" in t and "Python" in t for t in texts)
    assert any("tests" in t and "1 files" in t for t in texts)
    assert any("pyproject.toml" in t and "Python project config" in t for t in texts)
    assert any("README.md" in t and "Project readme" in t for t in texts)
    assert any("indexed:" in t.lower() and "5 files" in t for t in texts)

    for e in entries:
        assert e.source == "index"
        assert "index" in e.tags


def test_build_memories_stable_ids_and_provenance():
    files = [
        FileInfo(path="src/main.py", ext=".py", size=100, directory="src"),
        FileInfo(path="README.md", ext=".md", size=50, directory="."),
    ]

    first = _build_memories(files, Path("."), commit="abcdef0", preserve_from={})
    second = _build_memories(files, Path("."), commit="abcdef0", preserve_from={})

    by_ref_first = {e.source_ref: e for e in first}
    by_ref_second = {e.source_ref: e for e in second}

    # IDs must be deterministic for the same (source, source_ref).
    assert by_ref_first.keys() == by_ref_second.keys()
    for ref, entry in by_ref_first.items():
        assert entry.id == by_ref_second[ref].id
        assert entry.source_commit == "abcdef0"


def test_build_memories_preserves_verified_at_when_hash_matches(tmp_path: Path):
    from agmem.store import MemoryEntry

    files = [FileInfo(path="src/main.py", ext=".py", size=100, directory="src")]
    first = _build_memories(files, tmp_path, commit=None, preserve_from={})
    dir_entry = next(e for e in first if e.source_ref == "src")
    assert dir_entry.source_hash is not None

    prior_map = {dir_entry.id: MemoryEntry(
        id=dir_entry.id,
        ts=dir_entry.ts,
        text=dir_entry.text,
        tags=dir_entry.tags,
        source=dir_entry.source,
        source_ref=dir_entry.source_ref,
        source_hash=dir_entry.source_hash,
        verified_at="2026-05-07T00:00:00+00:00",
    )}
    second = _build_memories(files, tmp_path, commit=None, preserve_from=prior_map)
    refreshed = next(e for e in second if e.source_ref == "src")
    assert refreshed.verified_at == "2026-05-07T00:00:00+00:00"


def test_run_index_replaces_old(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        monkeypatch.setattr(
            "agmem.indexer.config.agmem_dir", lambda cwd=None: root / ".agmem"
        )
        monkeypatch.setattr(
            "agmem.indexer.config.memories_path",
            lambda cwd=None: root / ".agmem" / "memories.jsonl",
        )
        monkeypatch.setattr(
            "agmem.indexer.config.ensure_agmem_dir",
            lambda cwd=None: (root / ".agmem").mkdir(exist_ok=True) or (root / ".agmem"),
        )

        _make_tree(root, {
            "src/app.py": "x",
            "README.md": "# test",
        })

        total1, removed1, files1 = run_index(cwd=str(root))
        total2, removed2, files2 = run_index(cwd=str(root))

        # First run: nothing pre-existing, nothing dropped.
        assert removed1 == 0
        # Second run: stable IDs → idempotent, no entries dropped, same total count.
        assert removed2 == 0
        assert total1 == total2
        assert files1 == files2
        assert files1 == 2


def test_walk_files_skips_egg_info():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_tree(root, {
            "src/app.py": "x",
            "src/__pycache__/app.cpython-313.pyc": "y",
            "build/output.txt": "z",
            "dist/package.tar.gz": "q",
            "something.egg-info/PKG-INFO": "w",
        })

        files = _walk_files(root)
        paths = [f.path for f in files]

        assert "src/app.py" in paths
        assert "something.egg-info/PKG-INFO" not in paths
        assert "__pycache__" not in str(paths)
        assert "build/" not in str(paths)
        assert "dist/" not in str(paths)
