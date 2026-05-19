"""Deterministic repo indexer: walk files, generate structured memories."""

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import pathspec

from . import config
from .parsers import (
    FileAnalysis,
    ParserConfig,
    analyze_file,
    extract_tags_for_file,
    load as load_parser_config,
    registered_extensions,
)
from .parsers.md import MdSection, split_sections as split_md_sections
from .store import MemoryEntry, read_all_entries, rewrite_entries, stable_id

try:
    import pathspec
except ImportError:
    pathspec = None  # type: ignore[assignment]


INDEX_SOURCE = "index"
INDEX_TAG = "index"
SKIP_DIRS: set[str] = {".git", ".agmem", ".venv", "__pycache__", "node_modules",
                         ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
                         ".idea", ".vscode", "dist", "build", ".next"}
SKIP_DIR_SUFFIXES: tuple[str, ...] = (".egg-info",)

KEY_FILES: dict[str, str] = {
    "pyproject.toml": "Python project config",
    "package.json": "Node.js package manifest",
    "go.mod": "Go module definition",
    "Cargo.toml": "Rust package manifest",
    "Makefile": "Build automation",
    "Dockerfile": "Container image definition",
    "docker-compose.yml": "Docker Compose services",
    "docker-compose.yaml": "Docker Compose services",
    ".env.example": "Environment variables template",
    "README.md": "Project readme",
    "terraform": "Infrastructure as Code (Terraform)",
    "Pulumi.yaml": "Infrastructure as Code (Pulumi)",
}

EXT_LABELS: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript React",
    ".js": "JavaScript",
    ".jsx": "JavaScript React",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".tf": "Terraform",
    ".yaml": "YAML config",
    ".yml": "YAML config",
    ".json": "JSON",
    ".toml": "TOML config",
    ".sql": "SQL",
    ".sh": "Shell script",
    ".md": "Markdown",
    ".css": "CSS",
    ".html": "HTML",
    ".dockerfile": "Dockerfile",
}


def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
    gitignore_path = root / ".gitignore"
    if not gitignore_path.exists():
        return None
    with open(gitignore_path) as f:
        return pathspec.PathSpec.from_lines("gitignore", f)


def _should_skip(path: Path, root: Path, spec: pathspec.PathSpec | None) -> bool:
    parts = path.parts
    for part in parts:
        if part.endswith(SKIP_DIR_SUFFIXES) or part in SKIP_DIRS:
            return True
        if part.startswith(".") and part != ".gitignore":
            return True
    if spec is not None:
        rel = str(path.relative_to(root))
        if spec.match_file(rel):
            return True
    return False


class FileInfo(NamedTuple):
    path: str
    ext: str
    size: int
    directory: str


def _git_head_sha(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None




def _file_sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _dir_sha256(files: list[FileInfo]) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.path):
        h.update(f"{f.path}|{f.size}\n".encode("utf-8"))
    return h.hexdigest()


def _build_entry(
    *,
    text: str,
    tags: list[str],
    source: str,
    source_ref: str,
    source_hash: str | None,
    source_commit: str | None,
    preserve_from: dict[str, MemoryEntry],
) -> MemoryEntry:
    """Build an index entry with a stable id, preserving verification state across reindex.

    If a previous entry exists with the same stable id AND source_hash hasn't changed,
    we copy verified_at over; otherwise we clear drifted_at and let the next verify run set state.
    """
    entry_id = stable_id(source, source_ref)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    verified_at: str | None = None
    drifted_at: str | None = None
    prior = preserve_from.get(entry_id)
    if prior is not None and source_hash is not None and prior.source_hash == source_hash:
        verified_at = prior.verified_at
        drifted_at = prior.drifted_at
    return MemoryEntry(
        id=entry_id,
        ts=ts,
        text=text,
        tags=tags,
        source=source,
        source_ref=source_ref,
        source_hash=source_hash,
        source_commit=source_commit,
        verified_at=verified_at,
        drifted_at=drifted_at,
    )


def _walk_files(root: Path, scope: Path | None = None) -> list[FileInfo]:
    """Walk the tree under `scope` (defaults to root). Paths in FileInfo are root-relative."""
    spec = _load_gitignore(root)
    files: list[FileInfo] = []
    walk_root = scope if scope is not None else root
    if not walk_root.exists():
        return files

    for dirpath, dirnames, filenames in os.walk(walk_root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if _should_skip(fpath, root, spec):
                continue
            rel = str(fpath.relative_to(root))
            ext = fpath.suffix.lower()
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0
            files.append(FileInfo(path=rel, ext=ext, size=size,
                                   directory=str(Path(rel).parent)))
    return files


# Splitting threshold: a markdown file has to be both reasonably long
# AND structured (>= N H2 sections) before we slice it. Short docs stay
# as a single entry — splitting them would just multiply tiny records.
_MD_SPLIT_MIN_SECTIONS = 4
_MD_SPLIT_MIN_BYTES = 1500
# Section bodies bigger than this are truncated to keep individual JSONL
# lines reasonable. The cap is high enough that the section's substance
# is preserved; only ADRs with multi-page sections trip it.
_MD_SECTION_MAX_BYTES = 4000


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[...truncated...]"


def _build_md_section_entries(
    f: FileInfo,
    content: str,
    analysis: FileAnalysis,
    base_tags: list[str],
    prefix: str,
    commit: str | None,
    preserve_from: dict[str, MemoryEntry],
) -> list[MemoryEntry] | None:
    """Return one master overview entry plus one entry per H2 section.

    Returns ``None`` when the file is too short or too flat to bother splitting,
    so the caller falls back to the standard single-entry path.
    """
    preamble, sections = split_md_sections(content)
    if len(sections) < _MD_SPLIT_MIN_SECTIONS or len(content) < _MD_SPLIT_MIN_BYTES:
        return None

    out: list[MemoryEntry] = []
    section_titles = "; ".join(s.title for s in sections[:25])
    overflow = f" + {len(sections) - 25} more" if len(sections) > 25 else ""
    out.append(_build_entry(
        text=(
            f"{prefix}File `{f.path}` — {analysis.summary}. "
            f"Sections: {section_titles}{overflow}."
        ),
        tags=base_tags + ["section-master"],
        source=INDEX_SOURCE,
        source_ref=f.path,
        source_hash=hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest(),
        source_commit=commit,
        preserve_from=preserve_from,
    ))

    for section in sections:
        section_ref = f"{f.path}#{section.slug}"
        body = _truncate(section.content, _MD_SECTION_MAX_BYTES)
        sub_hint = ""
        if section.subsection_titles:
            sub_hint = (
                " Subsections: "
                + "; ".join(section.subsection_titles[:10])
                + "."
            )
        text = (
            f"Section \"{section.title}\" of `{f.path}`.{sub_hint}\n\n{body}"
        )
        section_tag_tokens = [
            t for t in re.split(r"[\W_]+", section.title.lower())
            if t and len(t) > 2
        ]
        tags = base_tags + ["section"] + section_tag_tokens[:6]
        section_hash = hashlib.sha256(
            section.content.encode("utf-8", errors="replace"),
        ).hexdigest()
        out.append(_build_entry(
            text=text,
            tags=tags,
            source=INDEX_SOURCE,
            source_ref=section_ref,
            source_hash=section_hash,
            source_commit=commit,
            preserve_from=preserve_from,
        ))

    return out


def _build_memories(
    files: list[FileInfo],
    root: Path,
    commit: str | None,
    preserve_from: dict[str, MemoryEntry],
    *,
    include_summary: bool = True,
    parser_config: ParserConfig | None = None,
) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    cfg = parser_config or ParserConfig()
    key_files_map = {**KEY_FILES, **cfg.key_files}
    ext_labels_map = {**EXT_LABELS, **cfg.extension_labels}
    content_exts = set(registered_extensions(cfg))

    # Pass 1: analyze content for files we have a parser for. Collect by path so
    # the key-file pass can skip these (a file is one source_ref → one entry).
    analyses: dict[str, tuple[FileInfo, FileAnalysis, str]] = {}
    for f in files:
        ext = f.ext.lstrip(".")
        if ext not in content_exts:
            continue
        try:
            content = (root / f.path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        analysis = analyze_file(f.path, content, cfg)
        if analysis is None or not analysis.blocks:
            continue
        analyses[f.path] = (f, analysis, content)

    dirs: dict[str, list[FileInfo]] = {}
    for f in files:
        dirs.setdefault(f.directory, []).append(f)

    for directory in sorted(dirs):
        dir_files = dirs[directory]
        ext_counts: dict[str, int] = {}
        total_size = 0
        key_file_labels: list[str] = []

        for f in dir_files:
            label = ext_labels_map.get(f.ext, "file")
            ext_counts[label] = ext_counts.get(label, 0) + 1
            total_size += f.size

            fname = Path(f.path).name
            if fname in key_files_map:
                key_file_labels.append(f"{fname} ({key_files_map[fname]})")

        label = directory if directory else "repo root"
        parts: list[str] = [f"Directory `{label}` contains {len(dir_files)} files."]
        if ext_counts:
            breakdown = ", ".join(f"{c}x {e}" for e, c in sorted(ext_counts.items()))
            parts.append(f"Types: {breakdown}.")
        if total_size > 0:
            if total_size >= 1024 * 1024:
                parts.append(f"Total size: {total_size / (1024 * 1024):.1f}MB.")
            elif total_size >= 1024:
                parts.append(f"Total size: {total_size / 1024:.1f}KB.")
            else:
                parts.append(f"Total size: {total_size}B.")
        if key_file_labels:
            parts.append("Key files: " + "; ".join(key_file_labels) + ".")

        tags = ["index", "directory"]
        if directory:
            for part in Path(directory).parts:
                tags.append(part.lower())

        source_ref = directory if directory else "."
        entries.append(_build_entry(
            text=" ".join(parts),
            tags=tags,
            source=INDEX_SOURCE,
            source_ref=source_ref,
            source_hash=_dir_sha256(dir_files),
            source_commit=commit,
            preserve_from=preserve_from,
        ))

    # Pass 2: key-file entries only for files that did NOT get analyzed.
    for f in files:
        fname = Path(f.path).name
        if fname not in key_files_map:
            continue
        if f.path in analyses:
            continue
        label = ext_labels_map.get(f.ext, "file")
        entries.append(_build_entry(
            text=f"File `{f.path}` — {label}, {f.size}B. {key_files_map[fname]}.",
            tags=["index", "key-file", label.lower()],
            source=INDEX_SOURCE,
            source_ref=f.path,
            source_hash=_file_sha256(root / f.path),
            source_commit=commit,
            preserve_from=preserve_from,
        ))

    # Pass 3: content entries.
    # Long markdown docs are split: one master overview + one entry per H2 section,
    # so retrieval can pinpoint the relevant section instead of returning the whole doc.
    for path in sorted(analyses):
        f, analysis, content = analyses[path]
        ext = f.ext.lstrip(".")
        fname = Path(path).name
        is_key = fname in key_files_map
        base_tags = ["index", "content", ext] + extract_tags_for_file(path, analysis.blocks, cfg)
        if is_key:
            base_tags.append("key-file")
        prefix = f"{key_files_map[fname]}. " if is_key else ""

        section_entries = _build_md_section_entries(
            f, content, analysis, base_tags, prefix, commit, preserve_from,
        ) if ext in ("md", "mdx") else None

        if section_entries:
            entries.extend(section_entries)
            continue

        block_list = "; ".join(b.full_name for b in analysis.blocks[:20])
        suffix = f" + {len(analysis.blocks) - 20} more" if len(analysis.blocks) > 20 else ""
        header = f"Purpose: {analysis.header_comment} " if analysis.header_comment else ""
        entries.append(_build_entry(
            text=f"{prefix}{header}File `{path}` — {analysis.summary}. Items: {block_list}{suffix}.",
            tags=base_tags,
            source=INDEX_SOURCE,
            source_ref=path,
            source_hash=hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest(),
            source_commit=commit,
            preserve_from=preserve_from,
        ))

    if include_summary:
        entries.append(_build_entry(
            text=f"Repository indexed: {len(files)} files in {len(dirs)} directories.",
            tags=["index", "summary"],
            source=INDEX_SOURCE,
            source_ref=".",
            source_hash=None,
            source_commit=commit,
            preserve_from=preserve_from,
        ))

    return entries


def _ref_in_scope(ref: str | None, scope_rel: str) -> bool:
    """True iff a memory entry's ``source_ref`` falls under ``scope_rel``.

    Scope matching ignores section anchors (``foo.md#section``) so all
    section-level entries for a file get refreshed together. Directory
    summary entries (``source_ref == scope_rel``) also match.
    """
    if not ref:
        return False
    base = ref.split("#", 1)[0]
    if base == scope_rel:
        return True
    return base.startswith(scope_rel.rstrip("/") + "/")


def run_index(
    cwd: str | None = None,
    scope: str | None = None,
) -> tuple[int, int, int]:
    """Index files under the repo root into memory.

    With ``scope=<subpath>`` the walk is limited to that subpath and existing
    index entries for files OUTSIDE the scope are preserved. Useful when a
    workspace-level ``.agmem/`` wants to index, e.g., only ``plans/`` from a
    multi-repo workspace without re-walking each sub-repo's source tree.
    """
    root = Path(cwd or os.getcwd()).resolve()

    scope_path: Path | None = None
    scope_rel: str | None = None
    if scope:
        candidate = (root / scope).resolve()
        try:
            scope_rel = str(candidate.relative_to(root))
        except ValueError:
            raise ValueError(f"--scope {scope!r} must be inside the agmem root {root}") from None
        scope_path = candidate

    files = _walk_files(root, scope=scope_path)
    commit = _git_head_sha(root)

    existing = read_all_entries(cwd, include_deleted=True)
    preserve_from: dict[str, MemoryEntry] = {
        e.id: e for e in existing if e.source == INDEX_SOURCE
    }

    parser_cfg = load_parser_config(config.agmem_dir(cwd))
    new_entries = _build_memories(
        files, root, commit, preserve_from, parser_config=parser_cfg,
    )

    if scope_rel:
        # Scoped reindex: keep manual entries AND index entries outside the scope.
        kept = [
            e for e in existing
            if e.source != INDEX_SOURCE or not _ref_in_scope(e.source_ref, scope_rel)
        ]
    else:
        kept = [e for e in existing if e.source != INDEX_SOURCE]

    config.ensure_agmem_dir(cwd)
    path = config.memories_path(cwd)
    with open(path, "w") as f:
        for e in kept:
            f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        for e in new_entries:
            f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")

    in_scope_before = sum(
        1 for e in preserve_from.values()
        if not scope_rel or _ref_in_scope(e.source_ref, scope_rel)
    )
    removed = in_scope_before - len(new_entries)
    return len(new_entries), max(removed, 0), len(files)


def _git_diff_name_status(root: Path, since_ref: str) -> list[tuple[str, str]] | None:
    """Return list of (status, path) tuples from `git diff --name-status <ref>...HEAD`.

    Includes uncommitted working-tree changes. Returns None on error.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", f"{since_ref}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        # Renames look like: R100\told\tnew  → treat as delete(old) + add(new)
        if status.startswith("R") and len(parts) >= 3:
            out.append(("D", parts[1]))
            out.append(("A", parts[2]))
        else:
            out.append((status, parts[1]))
    return out


def run_update(since_ref: str = "HEAD~1", cwd: str | None = None) -> dict:
    """Diff-aware partial reindex from `git diff --name-status <since_ref>`.

    For each changed file: re-analyze and upsert by stable id.
    For each deleted file: drop the matching entry.
    For affected directories: rebuild the directory summary.
    Repo summary entry is left untouched.
    """
    root = Path(cwd or os.getcwd()).resolve()
    diff = _git_diff_name_status(root, since_ref)
    if diff is None:
        return {"error": f"git diff failed against {since_ref!r}"}

    spec = _load_gitignore(root)
    deleted_paths: list[str] = []
    surviving_paths: list[str] = []
    modified_count = 0
    added_count = 0
    for status, path in diff:
        full = root / path
        if _should_skip(full, root, spec):
            continue
        if status.startswith("D") or not full.is_file():
            deleted_paths.append(path)
        else:
            surviving_paths.append(path)
            if status.startswith("M"):
                modified_count += 1
            elif status.startswith("A") or status == "??":
                added_count += 1

    if not deleted_paths and not surviving_paths:
        return {
            "since": since_ref,
            "modified": 0,
            "added": 0,
            "deleted": 0,
            "upserted": 0,
            "removed": 0,
        }

    affected_dirs: set[str] = set()
    for p in surviving_paths + deleted_paths:
        d = str(Path(p).parent)
        affected_dirs.add(d if d not in ("", ".") else ".")

    # Walk each affected directory once (non-recursive, to keep dir summary scoped to
    # files directly in that directory, matching the full-index behavior).
    affected_files: list[FileInfo] = []
    for d in affected_dirs:
        scope = root if d == "." else root / d
        if not scope.is_dir():
            continue
        for fi in _walk_files(root, scope=scope):
            if fi.directory == d or (d == "." and fi.directory == "."):
                affected_files.append(fi)

    commit = _git_head_sha(root)
    existing = read_all_entries(cwd, include_deleted=True)
    preserve_from = {e.id: e for e in existing if e.source == INDEX_SOURCE}

    parser_cfg = load_parser_config(config.agmem_dir(cwd))
    new_entries = _build_memories(
        affected_files, root, commit, preserve_from,
        include_summary=False, parser_config=parser_cfg,
    )
    new_ids = {e.id for e in new_entries}

    remove_ids: set[str] = set()
    for p in deleted_paths:
        remove_ids.add(stable_id(INDEX_SOURCE, p))

    final = [e for e in existing if e.id not in new_ids and e.id not in remove_ids]
    final.extend(new_entries)
    rewrite_entries(final, cwd)

    return {
        "since": since_ref,
        "to_commit": commit,
        "modified": modified_count,
        "added": added_count,
        "deleted": len(deleted_paths),
        "upserted": len(new_entries),
        "removed": len(remove_ids),
    }


def apply_paths(
    paths_modified: list[str],
    paths_deleted: list[str],
    cwd: str | None = None,
) -> dict:
    """Reindex a specific set of paths and remove entries for deleted paths.

    Used by ``agmem watch`` to apply queue batches without going through git.
    Mirrors :func:`run_update`'s behavior but takes paths directly instead
    of computing them from ``git diff <since_ref>``.

    Skips paths covered by :func:`_should_skip` (``.agmem/``, ``.git/``,
    ``.gitignore``-matched). Returns counts of upserts / removes / skipped.
    """
    from . import config as _config
    root = _config.find_repo_root(cwd)
    spec = _load_gitignore(root)

    surviving: list[str] = []
    deleted: list[str] = []
    skipped = 0
    for p in paths_modified:
        full = root / p
        if _should_skip(full, root, spec) or not full.is_file():
            skipped += 1
            continue
        surviving.append(p)
    for p in paths_deleted:
        full = root / p
        if _should_skip(full, root, spec):
            skipped += 1
            continue
        deleted.append(p)

    if not surviving and not deleted:
        return {"upserted": 0, "removed": 0, "skipped_ignored": skipped}

    # Build FileInfo entries directly from paths (no directory walk).
    affected_files: list[FileInfo] = []
    for p in surviving:
        full = root / p
        try:
            size = full.stat().st_size
        except OSError:
            continue
        parent = str(Path(p).parent) if Path(p).parent != Path(".") else "."
        affected_files.append(FileInfo(
            path=p,
            ext=full.suffix,
            size=size,
            directory=parent,
        ))

    commit = _git_head_sha(root)
    existing = read_all_entries(cwd, include_deleted=True)
    preserve_from = {e.id: e for e in existing if e.source == INDEX_SOURCE}

    parser_cfg = load_parser_config(config.agmem_dir(cwd))
    new_entries = _build_memories(
        affected_files, root, commit, preserve_from,
        include_summary=False, parser_config=parser_cfg,
    )
    new_ids = {e.id for e in new_entries}

    remove_ids: set[str] = set()
    for p in deleted:
        remove_ids.add(stable_id(INDEX_SOURCE, p))

    final = [e for e in existing if e.id not in new_ids and e.id not in remove_ids]
    final.extend(new_entries)
    rewrite_entries(final, cwd)

    return {
        "upserted": len(new_entries),
        "removed": len(remove_ids),
        "skipped_ignored": skipped,
    }
