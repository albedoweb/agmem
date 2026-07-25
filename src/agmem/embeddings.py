"""Optional dense-vector retrieval companion to BM25.

Sits behind an opt-in extra (``pip install agmem[hybrid]``). No import-time
dependency on ``sentence-transformers`` or ``numpy`` — both are loaded lazily,
and ``is_available()`` is the gate every caller checks.

Fusion is **score interpolation** (controlled by ``hybrid_alpha`` ∈ [0, 1]):

- 0.0  → pure BM25 (no embedding work; the module is a no-op)
- 1.0  → pure dense
- ~0.3 → typical hybrid blend

Each scoring side is min-max normalized to [0, 1] before blending so the wide
dynamic range of raw BM25 doesn't dwarf cosine.

On-disk cache keyed by the SHA-256 of each entry's full ``text`` field, stored
under ``.agmem/embeddings/`` as a numpy array + a parallel ID list:

    .agmem/embeddings/vectors.npy   — N × D float32, L2-normalized (so dot = cosine)
    .agmem/embeddings/ids.txt       — N lines, content_hash strings (row order)

Subsequent indexes only embed previously-unseen content; queries themselves
are not cached (queries are usually unique).

Adoption gate (Track A): enable by default only if alpha-sweep shows
**≥3 pp strict Hit@5 lift vs alpha=0** with no slice regression. Otherwise
keeps the feature behind ``--hybrid-alpha`` / ``.agmem/config [hybrid].enabled``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np  # only for type hints; never imported at runtime here

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Cross-encoder model for second-stage reranking. ms-marco-MiniLM-L-12-v2 is
# the standard small CE for general re-ranking (~125 MB, CPU-friendly,
# trained on MS-MARCO passage-ranking). Used in the second pass over top-K
# candidates from BM25+hybrid; scores (query, doc) pairs directly.
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-12-v2"

# Process-wide model cache. Loading models takes 3-5s + 80-125 MB each. A
# single `agmem watch` process touches many cwds, each instantiating its own
# Embedder/Reranker — without this singleton each instance would load its own
# copy. Keyed by model_name so bi-encoder and cross-encoder coexist cleanly.
_MODEL_CACHE: dict = {}


def _load_st_model(model_name: str):
    """Load a SentenceTransformer model. Tries the HF hub cache first
    (offline) so a flaky network or corp DNS blip on a fully-cached model
    doesn't crash the query — huggingface_hub otherwise HEADs the hub even
    for cached models (e.g. adapter_config.json probe). Only if the offline
    load fails (model genuinely not cached) do we hit the network."""
    from sentence_transformers import SentenceTransformer
    try:
        return SentenceTransformer(model_name, device="cpu", local_files_only=True)
    except Exception:
        return SentenceTransformer(model_name, device="cpu")


def _load_ce_model(model_name: str):
    """CrossEncoder counterpart of ``_load_st_model``."""
    from sentence_transformers import CrossEncoder
    try:
        return CrossEncoder(model_name, device="cpu", local_files_only=True)
    except Exception:
        return CrossEncoder(model_name, device="cpu")


def is_available() -> bool:
    """True iff the ``hybrid`` extras (sentence-transformers + numpy) import."""
    try:
        import sentence_transformers  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class Embedder:
    """Lazy-loaded sentence-transformers model + on-disk content-hash cache.

    The model is downloaded and loaded only on first call to ``embed_query`` or
    ``embed_texts``-with-misses, so importing this module is cheap.
    """

    def __init__(self, cache_dir: Path, model_name: str = DEFAULT_MODEL) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self._model = None
        self._index: dict[str, int] = {}         # content_hash → row in self._vectors
        self._vectors = None
        self._dim: int | None = None
        self._load_cache()

    # ----- cache -----
    def _load_cache(self) -> None:
        import numpy as np
        ids_path = self.cache_dir / "ids.txt"
        vec_path = self.cache_dir / "vectors.npy"
        if not (ids_path.exists() and vec_path.exists()):
            return
        try:
            with open(ids_path) as f:
                ids = [line.strip() for line in f if line.strip()]
            arr = np.load(vec_path)
            if arr.shape[0] != len(ids):
                # Corrupt cache — reset rather than risk mis-aligned lookups.
                return
            self._vectors = arr
            self._index = {h: i for i, h in enumerate(ids)}
            self._dim = int(arr.shape[1])
        except (OSError, ValueError):
            self._index = {}
            self._vectors = None

    def _save_cache(self) -> None:
        import numpy as np
        if self._vectors is None or not self._index:
            return
        ids_path = self.cache_dir / "ids.txt"
        vec_path = self.cache_dir / "vectors.npy"
        inv = {i: h for h, i in self._index.items()}
        with open(ids_path, "w") as f:
            for i in range(len(self._index)):
                f.write(inv[i] + "\n")
        np.save(vec_path, self._vectors)

    # ----- model (process-wide singleton per model_name) -----
    @property
    def model(self):
        if self._model is None:
            cached = _MODEL_CACHE.get(self.model_name)
            if cached is None:
                cached = _load_st_model(self.model_name)
                _MODEL_CACHE[self.model_name] = cached
            self._model = cached
        return self._model

    # ----- maintenance: drop orphan rows after a full reindex -----
    def compact(self, active_hashes: set[str]) -> int:
        """Rewrite the cache keeping only rows whose content_hash is in
        ``active_hashes``. Call after a full ``agmem index`` so the cache
        doesn't accumulate orphan vectors from old/replaced entries.

        Returns the number of rows dropped.
        """
        import numpy as np
        if self._vectors is None or not self._index:
            return 0
        keep_pairs = [(h, i) for h, i in self._index.items() if h in active_hashes]
        dropped = len(self._index) - len(keep_pairs)
        if dropped == 0:
            return 0
        if not keep_pairs:
            self._vectors = None
            self._index = {}
            for p in (self.cache_dir / "ids.txt", self.cache_dir / "vectors.npy"):
                try:
                    p.unlink()
                except OSError:
                    pass
            return dropped
        keep_pairs.sort(key=lambda kv: kv[1])             # preserve original row order
        old_rows = [i for _, i in keep_pairs]
        self._vectors = self._vectors[old_rows]
        self._index = {h: new_i for new_i, (h, _) in enumerate(keep_pairs)}
        self._save_cache()
        return dropped

    @property
    def dim(self) -> int:
        if self._dim is None:
            probe = self.model.encode(["dim-probe"], show_progress_bar=False,
                                       normalize_embeddings=True)
            self._dim = int(probe.shape[1])
        return self._dim

    # ----- embedding -----
    def embed_texts(self, texts: list[str]) -> "np.ndarray":
        """Embed entry texts (reuse cache; compute and persist any misses).

        Returns L2-normalized vectors in the same row order as ``texts`` so the
        caller can ``vectors @ query`` for cosine similarity directly.
        """
        import numpy as np
        hashes = [_content_hash(t) for t in texts]
        # Dedup *within this call*: two texts with identical content share a
        # cache row. Without this, the second insert overwrites the index entry
        # for the same hash and leaves an orphan vector row → KeyError on save.
        unique_missing: dict[str, str] = {}
        for h, t in zip(hashes, texts):
            if h not in self._index and h not in unique_missing:
                unique_missing[h] = t
        missing_pairs = list(unique_missing.items())
        if missing_pairs:
            new_texts = [t for _, t in missing_pairs]
            new_vecs = self.model.encode(
                new_texts, show_progress_bar=False, normalize_embeddings=True,
            ).astype(np.float32)
            existing = self._vectors
            if existing is None:
                self._vectors = new_vecs
                start = 0
            else:
                start = int(existing.shape[0])
                self._vectors = np.concatenate([existing, new_vecs], axis=0)
            for offset, (h, _) in enumerate(missing_pairs):
                self._index[h] = start + offset
            self._save_cache()
        vectors = self._vectors
        assert vectors is not None, "embed_texts: vectors should be populated by now"
        rows = [self._index[h] for h in hashes]
        return vectors[rows]

    def embed_query(self, query: str) -> "np.ndarray":
        """One-off query embedding — uncached (queries are usually unique)."""
        import numpy as np
        v = self.model.encode([query], show_progress_bar=False, normalize_embeddings=True)
        return v[0].astype(np.float32)


class Reranker:
    """Cross-encoder for second-stage reranking. Different from ``Embedder``
    (bi-encoder): takes ``(query, doc)`` pairs and outputs a relevance score
    per pair directly, no caching (each query is unique). Slower per call but
    materially more accurate — run only on the top-K pool from the first-stage
    BM25 + dense fusion. Typical: top-20 pool, score in ~100-500 ms on CPU."""

    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL) -> None:
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            cached = _MODEL_CACHE.get(self.model_name)
            if cached is None:
                cached = _load_ce_model(self.model_name)
                _MODEL_CACHE[self.model_name] = cached
            self._model = cached
        return self._model

    def score(self, query: str, docs: list[str]) -> list[float]:
        """Return one relevance score per document, in input order."""
        if not docs:
            return []
        pairs = [(query, d) for d in docs]
        scores = self.model.predict(pairs, show_progress_bar=False)
        # CrossEncoder.predict returns numpy ndarray; convert to plain floats
        # so callers don't need numpy.
        return [float(s) for s in scores]


def _min_max_norm(xs: list[float]) -> list[float]:
    """Map a list to [0, 1] by min-max. All-equal collapses to 0.5."""
    if not xs:
        return xs
    lo, hi = min(xs), max(xs)
    if hi <= lo:
        return [0.5] * len(xs)
    span = hi - lo
    return [(x - lo) / span for x in xs]


def fuse_scores(bm25_scores: list[float], cosine_scores: list[float],
                alpha: float) -> list[float]:
    """Blend ``(1 - α)·norm_bm25 + α·norm_cosine``. Each side min-max normalized
    to [0, 1] so wide BM25 dynamic ranges don't dwarf cosine."""
    if len(bm25_scores) != len(cosine_scores):
        raise ValueError(f"length mismatch: bm25={len(bm25_scores)} cosine={len(cosine_scores)}")
    bn = _min_max_norm(bm25_scores)
    cn = _min_max_norm(cosine_scores)
    return [(1.0 - alpha) * b + alpha * c for b, c in zip(bn, cn)]
