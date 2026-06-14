"""Tests for the optional embeddings module — must pass even without
``sentence-transformers`` installed (we mock the model when needed)."""
from __future__ import annotations

import pytest

from agmem.embeddings import _content_hash, _min_max_norm, fuse_scores, is_available


class TestContentHash:
    def test_stable_per_text(self):
        a = _content_hash("hello world")
        b = _content_hash("hello world")
        assert a == b

    def test_different_for_different_text(self):
        assert _content_hash("a") != _content_hash("b")


class TestMinMaxNorm:
    def test_basic(self):
        assert _min_max_norm([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]

    def test_all_equal_collapses_to_05(self):
        assert _min_max_norm([3.0, 3.0, 3.0]) == [0.5, 0.5, 0.5]

    def test_empty(self):
        assert _min_max_norm([]) == []

    def test_negatives(self):
        # min-max maps across full range regardless of sign
        out = _min_max_norm([-10, 0, 10])
        assert out == [0.0, 0.5, 1.0]


class TestFuseScores:
    def test_alpha_zero_returns_bm25_normalized(self):
        # α=0 → all weight on BM25 norm, ignores cosine entirely
        out = fuse_scores([1.0, 2.0, 3.0], [0.0, 0.0, 0.0], alpha=0.0)
        assert out == [0.0, 0.5, 1.0]

    def test_alpha_one_returns_cosine_normalized(self):
        out = fuse_scores([0.0, 0.0, 0.0], [1.0, 2.0, 3.0], alpha=1.0)
        assert out == [0.0, 0.5, 1.0]

    def test_blend_midway(self):
        # Two flat-normalized inputs blend to the same constant
        out = fuse_scores([1.0, 1.0], [5.0, 5.0], alpha=0.5)
        assert out == [0.5, 0.5]

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            fuse_scores([1.0], [1.0, 2.0], alpha=0.5)


class TestAvailability:
    def test_is_available_is_deterministic(self):
        # Calling twice in a row returns the same bool — no side effects.
        assert is_available() == is_available()


@pytest.mark.skipif(not is_available(), reason="hybrid extras not installed")
class TestEmbedderEndToEnd:
    """Runs only when ``sentence-transformers`` + ``numpy`` are present.
    Verifies the cache layer (hash-keyed, persisted to disk, reused on reload).
    """

    def test_cache_persists_across_instances(self, tmp_path):
        import numpy as np
        from agmem.embeddings import Embedder
        emb = Embedder(cache_dir=tmp_path / "e1")
        texts = ["alpha beta", "gamma delta"]
        v1 = emb.embed_texts(texts)
        assert v1.shape == (2, emb.dim)
        # New instance pointed at the same cache: vectors come from disk.
        emb2 = Embedder(cache_dir=tmp_path / "e1")
        v2 = emb2.embed_texts(texts)
        assert np.allclose(v1, v2)

    def test_normalized_vectors_have_unit_norm(self):
        import numpy as np
        from agmem.embeddings import Embedder
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            emb = Embedder(cache_dir=d)
            v = emb.embed_texts(["test"])
            assert abs(float(np.linalg.norm(v[0])) - 1.0) < 1e-4


class TestSearchHybridIntegration:
    """Mock the embedder so this test runs without the optional extras
    installed. The point is that ``search(hybrid_alpha=0)`` is a no-op (no
    embedder call) and ``hybrid_alpha > 0`` invokes the embedder + fuses."""

    def test_alpha_zero_no_embedder_no_op(self):
        from agmem.search import search
        from agmem.store import MemoryEntry
        entries = [MemoryEntry(id=f"x{i}", ts="t", text=f"alpha beta {i}",
                                source="index") for i in range(5)]
        # alpha=0 → embedder=None is fine; should return BM25 ranking
        results = search("alpha", entries, top_n=3, hybrid_alpha=0.0, embedder=None)
        assert len(results) == 3

    def test_alpha_positive_invokes_embedder_and_fuses(self, monkeypatch):
        """A fake embedder lets us prove ranking changes when hybrid is on."""
        import sys
        # Skip if numpy missing (the mock uses it)
        if "numpy" not in sys.modules:
            pytest.importorskip("numpy")
        import numpy as np
        from agmem.search import search
        from agmem.store import MemoryEntry

        class FakeEmbedder:
            """Returns hand-crafted vectors so the cosine ordering is the
            INVERSE of the BM25 ordering. With α=1, the bottom-BM25 doc
            should rank first."""
            def embed_texts(self, texts):
                # 3 entries: vectors point along different axes.
                # We'll arrange so entries[2] is the cosine-best for the query.
                return np.array(
                    [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                    dtype=np.float32,
                )
            def embed_query(self, q):
                return np.array([0.0, 0.0, 1.0], dtype=np.float32)

        entries = [
            MemoryEntry(id="a", ts="t", text="alpha alpha alpha", source="index"),  # strong BM25
            MemoryEntry(id="b", ts="t", text="alpha beta", source="index"),
            MemoryEntry(id="c", ts="t", text="gamma", source="index"),               # no BM25 match
        ]
        results_bm25 = search("alpha", entries, top_n=3, hybrid_alpha=0.0)
        results_hybrid = search("alpha", entries, top_n=3,
                                hybrid_alpha=1.0, embedder=FakeEmbedder())
        # Pure BM25: entry "a" should be ranked first.
        assert results_bm25[0][0].id == "a"
        # Pure dense: entry "c" should win (its vector matches the query).
        assert results_hybrid[0][0].id == "c"


@pytest.mark.skipif(not is_available(), reason="hybrid extras not installed")
class TestEmbedderDedup:
    """Regression for the orphan-vector-row bug: duplicate texts within a
    single embed_texts call must share one cache row (not be inserted twice
    and then leave _index pointing at only the last duplicate)."""

    def test_duplicate_texts_share_a_row(self, tmp_path):
        from agmem.embeddings import Embedder
        emb = Embedder(cache_dir=tmp_path)
        v = emb.embed_texts(["same text", "same text", "different"])
        assert v.shape[0] == 3  # caller gets the right number of vectors back
        # Cache only stores 2 unique vectors (one per unique hash).
        assert len(emb._index) == 2
        # And the two "same text" rows are identical vectors.
        import numpy as np
        assert np.allclose(v[0], v[1])

    def test_save_then_reload_after_duplicates_round_trips(self, tmp_path):
        from agmem.embeddings import Embedder
        emb = Embedder(cache_dir=tmp_path)
        emb.embed_texts(["a", "a", "b", "a"])
        # Reload from disk — no KeyError, same row count and dim.
        emb2 = Embedder(cache_dir=tmp_path)
        assert emb2._vectors is not None
        assert emb2._vectors.shape[0] == 2  # only 2 unique hashes persisted
        assert len(emb2._index) == 2


class TestSearchRerankIntegration:
    """Cross-encoder rerank wiring — mock the Reranker so this runs without
    the optional extras."""

    def test_rerank_top_k_zero_no_op(self):
        from agmem.search import search
        from agmem.store import MemoryEntry
        entries = [MemoryEntry(id=f"x{i}", ts="t", text=f"alpha beta {i}",
                                source="index") for i in range(5)]
        results = search("alpha", entries, top_n=3, rerank_top_k=0, reranker=None)
        assert len(results) == 3

    def test_rerank_reorders_top_pool(self):
        """A mock reranker that targets a specific doc by text marker forces
        that doc to rank 1, regardless of BM25's order. Test that rerank
        actually reorders, independent of how BM25 sorted the pool."""
        from agmem.search import search
        from agmem.store import MemoryEntry

        class MarkerReranker:
            """Score 10.0 for docs containing 'TARGET', 0.0 otherwise."""
            def score(self, query, docs):
                return [10.0 if "TARGET" in d else 0.0 for d in docs]

        entries = [
            MemoryEntry(id="a", ts="t", text="alpha plain content", source="index"),
            MemoryEntry(id="b", ts="t", text="alpha TARGET marker", source="index"),
            MemoryEntry(id="c", ts="t", text="alpha other content", source="index"),
        ]
        results = search("alpha", entries, top_n=3, rerank_top_k=3,
                        reranker=MarkerReranker())
        assert results[0][0].id == "b"

    def test_rerank_preserves_tail_beyond_top_k(self):
        """Items beyond rank K aren't reranked — keep original order."""
        from agmem.search import search
        from agmem.store import MemoryEntry

        class ConstantReranker:
            def score(self, query, docs):
                return [1.0] * len(docs)  # all same — preserve relative order

        entries = [
            MemoryEntry(id=f"x{i}", ts="t", text=f"alpha {'beta '*i}", source="index")
            for i in range(5)
        ]
        # Rerank only top-2; ranks 2..4 keep original BM25 order.
        results = search("alpha", entries, top_n=5, rerank_top_k=2, reranker=ConstantReranker())
        # Tail (ranks 2-4) must be in the same BM25 order as without rerank.
        bm25 = search("alpha", entries, top_n=5, rerank_top_k=0)
        assert [e.id for e, _ in results[2:]] == [e.id for e, _ in bm25[2:]]


@pytest.mark.skipif(not is_available(), reason="hybrid extras not installed")
class TestRerankerEndToEnd:
    def test_score_returns_one_per_doc(self):
        from agmem.embeddings import Reranker
        r = Reranker()
        scores = r.score("test query", ["doc one", "doc two", "doc three"])
        assert len(scores) == 3
        assert all(isinstance(s, float) for s in scores)
