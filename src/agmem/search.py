"""BM25 search over memory entries.

Scoring is multi-field: ``text`` is the body, while ``source_ref`` (path) and
the markdown title (if present) are repeated in the BM25 corpus to give them
a structural boost. This means a query that matches a filename or H1 title
ranks above a long doc that merely mentions the same word in passing.

After BM25 ranking, results can be reranked with Maximal Marginal Relevance
(MMR) to surface diverse documents instead of clustering multiple sections of
the same file. MMR is OFF by default — it measured eval-neutral on the Track A
golden set — but stays available behind the ``.agmem`` config (``mmr.enabled``);
``--no-mmr`` force-disables it per query.
"""

import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from . import config
from .aliases import (
    ALIASES,
    expand_query,
    load_user_aliases,
    merge_aliases,
)
from .store import MemoryEntry

# Meta-vocabulary that describes WHAT you're doing to the code (verbs) or
# generic wrappers around a task (nouns), not WHICH code you're doing it to.
# Queries composed entirely of these tokens match half the repo → BM25 can't
# discriminate → top-K is noise. Detected by ``is_low_signal_query`` and
# surfaced as a CLI warning so the agent rewrites around identifiers.
META_QUERY_TOKENS: set[str] = {
    # meta-verbs: describe the activity, not the target
    "review", "reviewing", "check", "checking", "refactor", "refactoring",
    "investigate", "investigating", "debug", "debugging", "fix", "fixing",
    "look", "looking", "understand", "understanding", "audit", "auditing",
    "examine", "examining", "explore", "exploring", "inspect", "inspecting",
    "read", "reading", "learn", "learning", "analyze", "analyzing",
    "help", "helping", "implement", "implementing",
    # meta-nouns: describe the wrapper, not the content
    "pull", "request", "pr", "prs", "code", "codebase", "repo", "repository",
    "changes", "diff", "issue", "issues", "bug", "bugs", "problem", "problems",
    "task", "tasks", "thing", "things", "stuff", "part", "parts", "way",
    "feature", "features",
    # very-common near-stopwords that survive STOP_WORDS filter
    "new", "old", "existing", "current", "latest", "recent",
}


STOP_WORDS: set[str] = {
    # Articles, prepositions, conjunctions
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "had", "he", "her", "him", "his", "i", "if", "in", "into",
    "is", "it", "its", "me", "my", "no", "not", "of", "on", "or", "so",
    "she", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "to", "us", "was", "we", "were", "will", "with",
    "you", "your", "yours",
    # Wh-words and common interrogative verbs (filter natural-language scaffolding)
    "how", "what", "when", "where", "which", "who", "whom", "whose", "why",
    "do", "does", "did", "doing", "done",
    # Generic action verbs that match nearly any doc and add no signal
    "use", "uses", "used", "using",
    "make", "makes", "made", "making",
    "work", "works", "worked", "working",
    "get", "gets", "got", "getting",
    "see", "sees", "seen", "saw",
    "go", "goes", "went", "going",
    "can", "could", "should", "would", "may", "might", "must",
    # Filler words common in questions
    "about", "any", "some", "all", "more", "most", "much", "many",
    "very", "just", "also", "too", "now", "only", "still",
}

# Default kind score multipliers: rules surface even when BM25 score is modest,
# because they're meta-instructions that should override the agent's default behavior.
DEFAULT_KIND_BOOST: dict[str, float] = {"rule": 4.0, "pattern": 1.5}

# Default source multipliers. Manual was 2× until 2026-06-06 — the intuition
# was "a human bothered to write it, so it answers more directly." A sweep on
# golden-set v3 killed that: manual entries appeared in top-K for 0 of 77 gold
# pairs (they're facts, not files the agent then reads), so the boost was just
# pulling them into top-5 slots they didn't deserve and displacing the actual
# code/TF/.values files. Dropping to 1.0 lifted Hit@5 +6.5pp and Recall@5
# +0.05 with no slice regression. Re-sweep before changing.
DEFAULT_SOURCE_BOOST: dict[str, float] = {"manual": 1.0}

# Splits on whitespace, punctuation, AND underscores so compound names like
# `aws_s3_bucket` tokenize to ['aws', 's3', 'bucket'] and match queries like "s3 bucket".
_TOKEN_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)

# Light morphological folding so "syncing"↔"sync", "handlers"↔"handler", and
# "categories"↔"category" match. Conservative — not full Porter (over-stems
# names like "deployment" → "deploy"). Applied to both query and corpus tokens
# (so they stay in sync). Toggle via _STEMMING_ENABLED for ablation.
_STEMMING_ENABLED = True


def _stem(token: str) -> str:
    """Conservative suffix folding. Skips short tokens, common false-positive
    endings (-ss/-us/-is/-os), and handles double-consonant verb forms
    (running → run, not "runn")."""
    if len(token) < 4:
        return token
    # plural / 3rd-person -s
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"                # categories → category
    if token.endswith("es") and len(token) > 4 and token[-3] in "sxz":
        return token[:-2]                       # classes → class, boxes → box
    if token.endswith("s") and len(token) > 3 and not token.endswith(("ss", "us", "is", "os")):
        return token[:-1]                       # handlers → handler
    # verb -ing
    if token.endswith("ing") and len(token) > 5:
        stem = token[:-3]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            return stem[:-1]                    # running → run
        return stem                              # syncing → sync
    # verb -ed
    if token.endswith("ed") and len(token) > 4:
        stem = token[:-2]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            return stem[:-1]                    # stopped → stop
        return stem                              # synced → sync
    return token

# Multi-field weights — each segment is repeated this many times in the BM25 corpus,
# effectively giving its tokens a higher term frequency.
_SOURCE_REF_WEIGHT = 3
_BASENAME_WEIGHT = 2
_TITLE_WEIGHT = 2
# Docstrings / godoc summary lines (the [1b] enrich-index field). Sweep on the
# v5 frozen set (2026-06-13) showed weight=1 is the Pareto sweet spot: same
# +2.8pp strict Hit@5 lift as higher values, smallest MRR regression (-0.009 vs
# -0.016 at weight=2), best Hit@10. Higher weights overfit doc-tokens and push
# rank-1 down without further widening the top-5.
_DOC_WEIGHT = 1

# Pulls the H1/title out of indexer-generated text like:
#   File `path` — Markdown doc — "Real Title", 5 sections. ...
_TITLE_RE = re.compile(r'Markdown doc — "([^"]+)"')
# Pulls the trailing `\nDocs: <doc1> | <doc2> | …` segment that the indexer
# attaches to code-file entries. DOTALL because docs may contain newlines.
_DOCS_RE = re.compile(r'\nDocs: (.+)$', re.DOTALL)

# MMR (Maximal Marginal Relevance) reranking defaults. OFF by default —
# measured eval-neutral on the Track A golden set, kept available behind config.
# λ=0.7 balances 70% relevance vs 30% diversity — the empirical sweet spot
# in IR literature. pool_size=20 gives MMR enough candidates to swap in
# for diversity without re-scoring the entire corpus.
DEFAULT_MMR_ENABLED = False
DEFAULT_MMR_LAMBDA = 0.7
DEFAULT_MMR_POOL_SIZE = 20


def _read_mmr_config(cwd: str | None = None) -> tuple[bool, float]:
    """Read MMR settings from ``.agmem/config.yaml``, falling back to defaults.
    
    Returns (enabled, lambda).
    """
    try:
        cfg = config.read_config(cwd)
        mmr = cfg.get("mmr", {}) if isinstance(cfg, dict) else {}
        if isinstance(mmr, dict):
            enabled = mmr.get("enabled", DEFAULT_MMR_ENABLED)
            lambda_ = float(mmr.get("lambda", DEFAULT_MMR_LAMBDA))
            return bool(enabled), lambda_
    except Exception:
        pass
    return DEFAULT_MMR_ENABLED, DEFAULT_MMR_LAMBDA


def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN_SPLIT_RE.split(text.lower())
    kept = [t for t in tokens if t and t not in STOP_WORDS]
    if _STEMMING_ENABLED:
        kept = [_stem(t) for t in kept]
    return kept


def _has_identifier_shape(raw_token: str) -> bool:
    """A token that looks like a code identifier or ticket ID — the kind of
    thing agmem indexes richly. If a query has any of these, BM25 has real
    signal to rank with; otherwise the query is likely generic prose."""
    if not raw_token:
        return False
    if any(c in raw_token for c in "_-./"):        # snake_case, kebab-case, path, module.dotted
        return True
    if len(raw_token) >= 2 and raw_token.isupper(): # ACRONYM (AWS, ALB, RDS)
        return True
    if any(c.isdigit() for c in raw_token) and any(c.isalpha() for c in raw_token):
        return True                                 # s3, ec2, abc123 — mixed alnum
    if (len(raw_token) >= 2 and raw_token[0].isupper()
            and any(c.islower() for c in raw_token[1:])
            and any(c.isupper() for c in raw_token[1:])):
        return True                                 # CamelCase
    return False


def is_low_signal_query(query: str) -> tuple[bool, str]:
    """Detect queries that ask agmem for what the agent is DOING (review,
    refactor, investigate) rather than WHICH code it's doing it to. Such
    queries have no identifier-shape tokens, so BM25 can't discriminate.

    Returns ``(is_low_signal, reason)``. ``reason`` is empty when the query
    passes. Conservative — only warns when we're confident (identifier-free
    AND ≥2 meta-tokens AND query has ≥3 tokens), to avoid false-positive
    spam.

    Not called during search itself: this is a CLI-side pre-flight hint,
    surfaced to the caller so a *human-readable* nudge lands in the terminal
    or agent transcript without changing retrieval semantics.
    """
    raw_tokens = query.split()
    if len(raw_tokens) < 3:
        return (False, "")
    if any(_has_identifier_shape(t.strip(",.;:!?()[]{}\"'")) for t in raw_tokens):
        return (False, "")
    lower = [t.strip(",.;:!?()[]{}\"'").lower() for t in raw_tokens]
    meta_hits = [t for t in lower if t in META_QUERY_TOKENS]
    if len(meta_hits) < 2:
        return (False, "")
    reason = (
        f"query is meta-word only ({', '.join(sorted(set(meta_hits))[:4])}) "
        "with no identifier-shape tokens — BM25 has nothing to rank on. "
        "Rewrite around identifiers from the diff/task: module names, "
        "resource types, function names, ticket IDs (e.g. PROJ-1234, "
        "waf-alb-public, rds_proxy, precompute_income)."
    )
    return (True, reason)


def _build_corpus_text(entry: MemoryEntry) -> str:
    """Concatenate the entry's searchable fields with structural weights baked in.

    Tags are intentionally excluded: long index entries can carry 20+ tags,
    which inflates BM25 scores from sheer mass. Tag filtering is still available
    via ``tag_filter``.
    """
    parts: list[str] = [entry.text]
    if entry.source_ref:
        parts.extend([entry.source_ref] * _SOURCE_REF_WEIGHT)
        basename = Path(entry.source_ref).stem
        if basename and basename.lower() != "readme":
            parts.extend([basename] * _BASENAME_WEIGHT)
    title_match = _TITLE_RE.search(entry.text)
    if title_match:
        parts.extend([title_match.group(1)] * _TITLE_WEIGHT)
    docs_match = _DOCS_RE.search(entry.text)
    if docs_match:
        parts.extend([docs_match.group(1)] * _DOC_WEIGHT)
    return " ".join(parts)


def _resolve_aliases(cwd: str | None) -> dict[str, list[str]]:
    """Built-in ALIASES merged with any user-defined ones from ``.agmem/aliases.yaml``."""
    try:
        agmem = config.agmem_dir(cwd)
    except Exception:
        return ALIASES
    user = load_user_aliases(agmem)
    if not user:
        return ALIASES
    return merge_aliases(ALIASES, user)


def _path_similarity(a: MemoryEntry, b: MemoryEntry) -> float:
    """1.0 if both entries refer to the same source file (path before ``#``),
    else 0.0. Treats section-level entries as duplicates of their parent file.
    """
    if not a.source_ref or not b.source_ref:
        return 0.0
    pa = a.source_ref.split("#", 1)[0]
    pb = b.source_ref.split("#", 1)[0]
    return 1.0 if pa == pb else 0.0


def _mmr_rerank(
    ranked: list[tuple[MemoryEntry, float]],
    *,
    top_k: int,
    lambda_: float = 0.7,
) -> list[tuple[MemoryEntry, float]]:
    """Maximal Marginal Relevance reranking.

    Reorders ``ranked`` (already-scored candidates from BM25) to maximize
    (λ * relevance) − ((1−λ) * max-similarity-to-already-selected).
    Returns top_k results.

    Relevance scores are min-max normalized to [0, 1] before MMR so that
    the diversity penalty (max 1−λ) has meaningful weight against scores
    whose raw BM25 range can be arbitrarily wide. Original scores are
    preserved in the output.

    Rank 1 (the highest-scoring entry) is always kept untouched — MMR only
    selects from rank 2 onward, preserving the best match.
    """
    if not ranked or top_k <= 0:
        return []
    if top_k >= len(ranked):
        return ranked[:top_k]

    # Min-max normalize scores to [0, 1] so diversity penalty isn't dwarfed
    # by wide raw BM25 score gaps.
    raw_scores = [s for _, s in ranked]
    smin, smax = min(raw_scores), max(raw_scores)
    if smax > smin:
        norm = [(e, (s - smin) / (smax - smin)) for e, s in ranked]
    else:
        norm = [(e, 0.5) for e, _ in ranked]

    selected: list[tuple[MemoryEntry, float]] = []
    remaining = list(norm)
    orig_by_id = {e.id: s for e, s in ranked}

    selected.append(remaining.pop(0))

    while remaining and len(selected) < top_k:
        best_idx = 0
        best_score = float("-inf")
        for i, (cand, cand_norm_score) in enumerate(remaining):
            max_sim = max(
                _path_similarity(cand, sel_entry)
                for sel_entry, _ in selected
            )
            mmr_score = lambda_ * cand_norm_score - (1 - lambda_) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        selected.append(remaining.pop(best_idx))

    # Return with original scores intact
    return [
        (entry, orig_by_id.get(entry.id, 0.0))
        for entry, _norm_score in selected
    ]


def _read_hybrid_alpha(cwd: str | None = None) -> float:
    """Read hybrid retrieval setting from ``.agmem/config.yaml``:

    [hybrid]
      enabled: true
      alpha: 0.3

    Returns 0.0 (= disabled, pure BM25) by default."""
    try:
        cfg = config.read_config(cwd)
        h = cfg.get("hybrid") if isinstance(cfg, dict) else None
        if isinstance(h, dict) and h.get("enabled"):
            return float(h.get("alpha", 0.3))
    except Exception:
        pass
    return 0.0


def _read_rerank_top_k(cwd: str | None = None) -> int:
    """Read cross-encoder reranking setting from ``.agmem/config.yaml``:

    [rerank]
      enabled: true
      top_k: 20

    Returns 0 (= disabled) by default. Reranking only kicks in when the
    ``hybrid`` extras are installed (Reranker uses sentence-transformers)."""
    try:
        cfg = config.read_config(cwd)
        r = cfg.get("rerank") if isinstance(cfg, dict) else None
        if isinstance(r, dict) and r.get("enabled"):
            return int(r.get("top_k", 20))
    except Exception:
        pass
    return 0


def search(
    query: str,
    entries: list[MemoryEntry],
    top_n: int = 10,
    tag_filter: str | None = None,
    kind_boost: dict[str, float] | None = None,
    source_boost: dict[str, float] | None = None,
    aliases: dict[str, list[str]] | None = None,
    mmr_enabled: bool = False,
    mmr_lambda: float = 0.7,
    hybrid_alpha: float = 0.0,
    embedder=None,
    rerank_top_k: int = 0,
    reranker=None,
) -> list[tuple[MemoryEntry, float]]:
    """BM25 search with optional hybrid dense fusion and cross-encoder rerank.

    ``hybrid_alpha`` ∈ [0, 1]: 0 = pure BM25 (default, no embedder needed),
    1 = pure dense, in-between blends min-max-normalized BM25 with cosine.
    ``embedder`` is an ``agmem.embeddings.Embedder`` — required iff alpha > 0.

    ``rerank_top_k`` > 0 turns on cross-encoder second-stage reranking over the
    top-K candidates from BM25+hybrid; ``reranker`` is an
    ``agmem.embeddings.Reranker``. Tail beyond top-K is preserved in its
    original order (CE only re-orders the top — cheaper, and uncommon to want
    rank K+1 anyway).
    """
    if not entries:
        return []

    if tag_filter:
        entries = [e for e in entries if tag_filter.lower() in [t.lower() for t in e.tags]]

    if not entries:
        return []

    kb = kind_boost if kind_boost is not None else DEFAULT_KIND_BOOST
    sb = source_boost if source_boost is not None else DEFAULT_SOURCE_BOOST

    corpus = [_tokenize(_build_corpus_text(e)) for e in entries]
    # b=0.85 (vs default 0.75) penalizes long docs harder so short focused
    # entries outrank verbose READMEs that just mention the query word once.
    bm25 = BM25Okapi(corpus, b=0.85)
    expanded_query = expand_query(query, aliases)
    query_tokens = _tokenize(expanded_query)
    raw_scores = bm25.get_scores(query_tokens)
    # Only multiply boosts onto POSITIVE BM25 contributions. Negative scores
    # mean the document is BM25-irrelevant (e.g. query terms appear in every
    # doc of a tiny corpus → IDF goes negative); multiplying a negative score
    # by 2× would perversely demote relevant entries below irrelevant ones.
    scores: list[float] = [
        s * kb.get(e.kind, 1.0) * sb.get(e.source, 1.0) if s > 0 else s
        for s, e in zip(raw_scores, entries)
    ]

    # Hybrid fusion — only when both opted in (alpha > 0) AND an embedder is wired.
    # Any failure (missing model, flaky network on first load, embedding-dim
    # mismatch after a model swap) degrades gracefully to pure BM25 for this
    # query rather than crashing the caller.
    if hybrid_alpha > 0 and embedder is not None:
        try:
            from . import embeddings as _emb
            entry_vecs = embedder.embed_texts([e.text for e in entries])
            query_vec = embedder.embed_query(expanded_query)
            # Vectors are L2-normalized → dot product = cosine similarity.
            cosines = (entry_vecs @ query_vec).tolist()
            scores = _emb.fuse_scores(scores, cosines, hybrid_alpha)
        except Exception as exc:
            import sys
            print(f"[agmem] hybrid embedding failed ({type(exc).__name__}: {exc}); "
                  f"falling back to BM25 for this query.", file=sys.stderr)

    ranked = sorted(zip(entries, scores), key=lambda x: x[1], reverse=True)

    # Cross-encoder rerank — only over the top-K pool. Re-orders ranks 0..K-1
    # by relevance score; ranks K+ keep their original BM25/hybrid order. Same
    # graceful-degradation contract as the hybrid block above.
    if rerank_top_k > 0 and reranker is not None and len(ranked) > 1:
        try:
            k = min(rerank_top_k, len(ranked))
            pool_entries = [e for e, _ in ranked[:k]]
            ce_scores = reranker.score(query, [e.text for e in pool_entries])
            reranked = sorted(zip(pool_entries, ce_scores),
                              key=lambda x: x[1], reverse=True)
            ranked = reranked + ranked[k:]
        except Exception as exc:
            import sys
            print(f"[agmem] rerank failed ({type(exc).__name__}: {exc}); "
                  f"keeping BM25/hybrid order.", file=sys.stderr)

    if mmr_enabled and len(ranked) > 1 and top_n > 0:
        pool = ranked[: max(top_n * 2, DEFAULT_MMR_POOL_SIZE)]
        return _mmr_rerank(pool, top_k=top_n, lambda_=mmr_lambda)

    return ranked[:top_n]


def search_filtered(
    query: str,
    limit: int = 10,
    tag: str | None = None,
    cwd: str | None = None,
    kind_boost: dict[str, float] | None = None,
    source_boost: dict[str, float] | None = None,
    mmr_enabled: bool = False,
    mmr_lambda: float = 0.7,
    hybrid_alpha: float | None = None,
    rerank_top_k: int | None = None,
) -> list[tuple[MemoryEntry, float]]:
    from .store import read_all_entries
    entries = read_all_entries(cwd)
    aliases = _resolve_aliases(cwd)
    # Resolve hybrid_alpha + rerank_top_k: explicit param > config; default off.
    if hybrid_alpha is None:
        hybrid_alpha = _read_hybrid_alpha(cwd)
    if rerank_top_k is None:
        rerank_top_k = _read_rerank_top_k(cwd)

    extras_needed = (hybrid_alpha and hybrid_alpha > 0) or (rerank_top_k and rerank_top_k > 0)
    embedder = None
    reranker = None
    if extras_needed:
        from . import embeddings as _emb
        if not _emb.is_available():
            import sys
            print("[agmem] hybrid/rerank requested but optional extras not installed; "
                  "falling back to BM25. Install via `pip install agmem[hybrid]`.", file=sys.stderr)
            hybrid_alpha = 0.0
            rerank_top_k = 0
        else:
            if hybrid_alpha and hybrid_alpha > 0:
                embedder = _emb.Embedder(cache_dir=config.agmem_dir(cwd) / "embeddings")
            if rerank_top_k and rerank_top_k > 0:
                reranker = _emb.Reranker()
    return search(
        query, entries,
        top_n=limit, tag_filter=tag,
        kind_boost=kind_boost, source_boost=source_boost,
        aliases=aliases,
        mmr_enabled=mmr_enabled, mmr_lambda=mmr_lambda,
        hybrid_alpha=hybrid_alpha or 0.0, embedder=embedder,
        rerank_top_k=rerank_top_k or 0, reranker=reranker,
    )
