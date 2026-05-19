from __future__ import annotations

import pytest

from agmem.search import _mmr_rerank, _path_similarity
from agmem.store import MemoryEntry


def _e(ref: str, score: float) -> MemoryEntry:
    return MemoryEntry(
        id=ref, ts="2026-01-01", text=ref, source_ref=ref, source="index",
    )


def _e_noref(ref_id: str, score: float) -> MemoryEntry:
    return MemoryEntry(
        id=ref_id, ts="2026-01-01", text=ref_id, source_ref=None, source="index",
    )


class TestPathSimilarity:
    def test_same_file_without_section(self):
        a = _e("plans/X.md", 1.0)
        b = _e("plans/X.md", 0.5)
        assert _path_similarity(a, b) == 1.0

    def test_same_file_with_section(self):
        a = _e("plans/X.md", 1.0)
        b = _e("plans/X.md#3-design", 0.5)
        assert _path_similarity(a, b) == 1.0

    def test_different_files(self):
        a = _e("plans/X.md", 1.0)
        b = _e("plans/Y.md", 0.5)
        assert _path_similarity(a, b) == 0.0

    def test_different_sections_same_file(self):
        a = _e("plans/X.md#1-intro", 1.0)
        b = _e("plans/X.md#3-design", 0.5)
        assert _path_similarity(a, b) == 1.0

    def test_none_source_ref(self):
        a = _e_noref("id1", 1.0)
        b = _e("plans/X.md", 0.5)
        assert _path_similarity(a, b) == 0.0

    def test_both_none_source_ref(self):
        a = _e_noref("id1", 1.0)
        b = _e_noref("id2", 0.5)
        assert _path_similarity(a, b) == 0.0

    def test_empty_source_ref(self):
        a = MemoryEntry(id="a", ts="x", text="a", source_ref="", source="index")
        b = MemoryEntry(id="b", ts="x", text="b", source_ref="", source="index")
        assert _path_similarity(a, b) == 0.0

    def test_directory_paths(self):
        a = _e("src/subdir/services.py", 1.0)
        b = _e("src/subdir/services.py", 0.5)
        assert _path_similarity(a, b) == 1.0

    def test_same_basename_different_path(self):
        a = _e("src/services.py", 1.0)
        b = _e("tests/services.py", 0.5)
        assert _path_similarity(a, b) == 0.0


class TestMmrRerank:
    def test_single_entry_unchanged(self):
        ranked = [(_e("a.md", 1.0), 1.0)]
        result = _mmr_rerank(ranked, top_k=3)
        assert len(result) == 1
        assert result[0][0].source_ref == "a.md"

    def test_all_different_files_identity(self):
        ranked = [
            (_e("a.md", 1.0), 1.0),
            (_e("b.md", 0.8), 0.8),
            (_e("c.md", 0.6), 0.6),
        ]
        result = _mmr_rerank(ranked, top_k=3)
        refs = [e.source_ref for e, _ in result]
        assert refs == ["a.md", "b.md", "c.md"]

    def test_same_file_clustering_reduced(self):
        ranked = [
            (_e("plans/X.md", 1.0), 1.0),
            (_e("plans/X.md#2", 0.9), 0.9),
            (_e("plans/X.md#3", 0.8), 0.8),
            (_e("plans/X.md#4", 0.7), 0.7),
            (_e("plans/Y.md", 0.5), 0.5),
        ]
        result = _mmr_rerank(ranked, top_k=3)
        refs = [e.source_ref for e, _ in result]
        assert refs[0] == "plans/X.md"
        assert "plans/Y.md" in refs[1:]

    def test_top_one_always_preserved(self):
        ranked = [
            (_e("plans/X.md#3", 1.0), 1.0),
            (_e("plans/X.md", 0.9), 0.9),
            (_e("plans/Y.md", 0.1), 0.1),
        ]
        result = _mmr_rerank(ranked, top_k=5)
        assert result[0][0].source_ref == "plans/X.md#3"

    def test_lambda_1_pure_relevance(self):
        ranked = [
            (_e("plans/X.md", 1.0), 1.0),
            (_e("plans/X.md#2", 0.9), 0.9),
            (_e("plans/X.md#3", 0.8), 0.8),
            (_e("plans/Y.md", 0.5), 0.5),
        ]
        result = _mmr_rerank(ranked, top_k=3, lambda_=1.0)
        refs = [e.source_ref for e, _ in result]
        assert refs == ["plans/X.md", "plans/X.md#2", "plans/X.md#3"]

    def test_lambda_0_pure_diversity_pushes_same_file_down(self):
        ranked = [
            (_e("plans/X.md", 1.0), 1.0),
            (_e("plans/X.md#2", 0.9), 0.9),
            (_e("plans/X.md#3", 0.8), 0.8),
            (_e("plans/Y.md", 0.1), 0.1),
            (_e("plans/Z.md", 0.05), 0.05),
        ]
        result = _mmr_rerank(ranked, top_k=3, lambda_=0.0)
        refs = [e.source_ref for e, _ in result]
        assert refs[0] == "plans/X.md"
        assert refs[1] == "plans/Y.md"
        assert refs[2] == "plans/Z.md"

    def test_pool_size_respected(self):
        ranked = [
            (_e(f"doc{i}.md", float(100 - i)), float(100 - i))
            for i in range(100)
        ]
        result = _mmr_rerank(ranked, top_k=5)
        assert len(result) == 5

    def test_top_k_greater_than_input(self):
        ranked = [
            (_e("a.md", 1.0), 1.0),
            (_e("b.md", 0.8), 0.8),
        ]
        result = _mmr_rerank(ranked, top_k=5)
        assert len(result) == 2

    def test_top_k_zero(self):
        ranked = [(_e("a.md", 1.0), 1.0)]
        result = _mmr_rerank(ranked, top_k=0)
        assert result == []

    def test_empty_input(self):
        result = _mmr_rerank([], top_k=5)
        assert result == []

    def test_mixed_same_and_different_files(self):
        ranked = [
            (_e("plans/A.md", 1.0), 1.0),
            (_e("plans/A.md#2", 0.9), 0.9),
            (_e("plans/B.md", 0.8), 0.8),
            (_e("src/app.py", 0.7), 0.7),
        ]
        result = _mmr_rerank(ranked, top_k=4)
        refs = [e.source_ref for e, _ in result]
        assert refs[0] == "plans/A.md"
        assert "plans/B.md" in refs
        assert "src/app.py" in refs

    def test_same_file_limit_two_in_top_k(self):
        ranked = [
            (_e("plans/X.md", 1.0), 1.0),
            (_e("plans/X.md#2", 0.8), 0.8),
            (_e("plans/X.md#3", 0.6), 0.6),
            (_e("plans/X.md#4", 0.4), 0.4),
            (_e("plans/Y.md", 0.39), 0.39),
            (_e("plans/Z.md", 0.38), 0.38),
        ]
        result = _mmr_rerank(ranked, top_k=5, lambda_=0.7)
        refs = [e.source_ref for e, _ in result]
        x_count = sum(1 for r in refs if r and r.startswith("plans/X"))
        assert x_count <= 3

    def test_none_source_ref_entries_get_zero_similarity(self):
        a = _e_noref("id1", 1.0)
        b = _e_noref("id2", 0.8)
        ranked = [(a, 1.0), (b, 0.8)]
        result = _mmr_rerank(ranked, top_k=2)
        assert len(result) == 2
        assert result[0][0].id == "id1"
        assert result[1][0].id == "id2"
