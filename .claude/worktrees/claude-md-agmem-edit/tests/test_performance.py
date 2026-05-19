"""Performance tests: all commands should complete under 200ms on 1000 entries."""

import json
import tempfile
import time
from pathlib import Path

from agmem.search import search
from agmem.store import MemoryEntry, create_entry, append_entry, read_all_entries


def _make_entries(n: int) -> list[MemoryEntry]:
    entries = []
    for i in range(n):
        tags = []
        if i % 5 == 0:
            tags.append("billing")
        if i % 3 == 0:
            tags.append("auth")
        if i % 7 == 0:
            tags.append("devops")

        text = f"Memory entry number {i}. "
        if "billing" in tags:
            text += f"Billing constraint for item {i}: webhooks must be idempotent. "
        if "auth" in tags:
            text += f"Auth rule {i}: use JWT tokens with 24h expiry. "
        if "devops" in tags:
            text += f"Deployment note {i}: run Docker Compose with Makefile. "
        text += f"Generic project information for record {i}."

        entry = MemoryEntry(
            id=f"01KQX{i:08d}",
            ts=f"2026-01-{(i % 28) + 1:02d}T12:00:00+00:00",
            text=text,
            tags=tags,
            source="manual",
        )
        entries.append(entry)
    return entries


def test_search_performance_1000_entries(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setattr(
        "agmem.store.config.agmem_dir", lambda cwd=None: tmpdir / ".agmem"
    )
    monkeypatch.setattr(
        "agmem.store.config.memories_path",
        lambda cwd=None: tmpdir / ".agmem" / "memories.jsonl",
    )
    (tmpdir / ".agmem").mkdir(exist_ok=True)

    entries = _make_entries(1000)
    for entry in entries:
        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        with open(tmpdir / ".agmem" / "memories.jsonl", "a") as f:
            f.write(line)

    start = time.perf_counter()
    loaded = read_all_entries()
    load_time = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    results = search("billing webhook", loaded, top_n=10)
    search_time = (time.perf_counter() - start) * 1000

    total = load_time + search_time

    assert total < 200, f"Load+search took {total:.1f}ms (load={load_time:.1f}ms, search={search_time:.1f}ms), expected <200ms"
