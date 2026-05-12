"""BM25 search over memory entries.

Scoring is multi-field: ``text`` is the body, while ``source_ref`` (path) and
the markdown title (if present) are repeated in the BM25 corpus to give them
a structural boost. This means a query that matches a filename or H1 title
ranks above a long doc that merely mentions the same word in passing.
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

# Splits on whitespace, punctuation, AND underscores so compound names like
# `aws_s3_bucket` tokenize to ['aws', 's3', 'bucket'] and match queries like "s3 bucket".
_TOKEN_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)

# Multi-field weights — each segment is repeated this many times in the BM25 corpus,
# effectively giving its tokens a higher term frequency.
_SOURCE_REF_WEIGHT = 3
_BASENAME_WEIGHT = 2
_TITLE_WEIGHT = 2

# Pulls the H1/title out of indexer-generated text like:
#   File `path` — Markdown doc — "Real Title", 5 sections. ...
_TITLE_RE = re.compile(r'Markdown doc — "([^"]+)"')


def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN_SPLIT_RE.split(text.lower())
    return [t for t in tokens if t and t not in STOP_WORDS]


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


def search(
    query: str,
    entries: list[MemoryEntry],
    top_n: int = 10,
    tag_filter: str | None = None,
    kind_boost: dict[str, float] | None = None,
    aliases: dict[str, list[str]] | None = None,
) -> list[tuple[MemoryEntry, float]]:
    if not entries:
        return []

    if tag_filter:
        entries = [e for e in entries if tag_filter.lower() in [t.lower() for t in e.tags]]

    if not entries:
        return []

    boost = kind_boost if kind_boost is not None else DEFAULT_KIND_BOOST

    corpus = [_tokenize(_build_corpus_text(e)) for e in entries]
    # b=0.85 (vs default 0.75) penalizes long docs harder so short focused
    # entries outrank verbose READMEs that just mention the query word once.
    bm25 = BM25Okapi(corpus, b=0.85)
    expanded_query = expand_query(query, aliases)
    query_tokens = _tokenize(expanded_query)
    raw_scores = bm25.get_scores(query_tokens)
    scores = [s * boost.get(e.kind, 1.0) for s, e in zip(raw_scores, entries)]

    ranked = sorted(zip(entries, scores), key=lambda x: x[1], reverse=True)
    return ranked[:top_n]


def search_filtered(
    query: str,
    limit: int = 10,
    tag: str | None = None,
    cwd: str | None = None,
    kind_boost: dict[str, float] | None = None,
) -> list[tuple[MemoryEntry, float]]:
    from .store import read_all_entries
    entries = read_all_entries(cwd)
    aliases = _resolve_aliases(cwd)
    return search(
        query, entries,
        top_n=limit, tag_filter=tag, kind_boost=kind_boost, aliases=aliases,
    )
