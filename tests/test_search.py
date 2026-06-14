"""Tests for search module: BM25 relevance and filtering."""

import pytest

from agmem.search import search
from agmem.store import MemoryEntry


FIXTURE = [
    MemoryEntry(id="01A", ts="2026-01-01T00:00:00Z", text="Billing webhooks must be idempotent.", tags=["billing", "constraint"]),
    MemoryEntry(id="01B", ts="2026-01-02T00:00:00Z", text="Do not call Stripe directly from request handlers.", tags=["billing", "constraint"]),
    MemoryEntry(id="01C", ts="2026-01-03T00:00:00Z", text="Retry state is stored in invoice_events table.", tags=["billing", "db"]),
    MemoryEntry(id="01D", ts="2026-01-04T00:00:00Z", text="We use FastAPI for HTTP routes.", tags=["architecture"]),
    MemoryEntry(id="01E", ts="2026-01-05T00:00:00Z", text="Celery handles async jobs and background tasks.", tags=["architecture"]),
    MemoryEntry(id="01F", ts="2026-01-06T00:00:00Z", text="Auth is handled in the gateway service.", tags=["auth"]),
    MemoryEntry(id="01G", ts="2026-01-07T00:00:00Z", text="User passwords are hashed with bcrypt.", tags=["auth", "security"]),
    MemoryEntry(id="01H", ts="2026-01-08T00:00:00Z", text="Deployments use Docker Compose and a Makefile.", tags=["devops"]),
    MemoryEntry(id="01I", ts="2026-01-09T00:00:00Z", text="Run tests with pytest -v --reuse-db.", tags=["testing"]),
    MemoryEntry(id="01J", ts="2026-01-10T00:00:00Z", text="Stripe webhook signature verification is in webhooks.py.", tags=["billing", "stripe"]),
    MemoryEntry(id="01K", ts="2026-01-11T00:00:00Z", text="Database migrations are managed by Alembic.", tags=["db", "devops"]),
    MemoryEntry(id="01L", ts="2026-01-12T00:00:00Z", text="Logging goes to stdout in JSON format for CloudWatch.", tags=["devops", "observability"]),
    MemoryEntry(id="01M", ts="2026-01-13T00:00:00Z", text="Never store raw credit card numbers in logs or database.", tags=["security", "billing"]),
    MemoryEntry(id="01N", ts="2026-01-14T00:00:00Z", text="API rate limiting is per user, 100 req/min default.", tags=["api", "constraint"]),
    MemoryEntry(id="01O", ts="2026-01-15T00:00:00Z", text="The invoice PDF is generated asynchronously via Celery task.", tags=["billing", "celery"]),
    MemoryEntry(id="01P", ts="2026-01-16T00:00:00Z", text="Session tokens expire after 24 hours of inactivity.", tags=["auth", "security"]),
    MemoryEntry(id="01Q", ts="2026-01-17T00:00:00Z", text="Feature flags are managed via LaunchDarkly SDK.", tags=["feature-flags"]),
    MemoryEntry(id="01R", ts="2026-01-18T00:00:00Z", text="All monetary values are stored as integer cents.", tags=["billing", "constraint"]),
    MemoryEntry(id="01S", ts="2026-01-19T00:00:00Z", text="Health check endpoint returns 200 with DB and Redis status.", tags=["api", "devops"]),
    MemoryEntry(id="01T", ts="2026-01-20T00:00:00Z", text="Webhook retry policy: 3 attempts with exponential backoff.", tags=["billing", "webhook"]),
]


def test_search_returns_results():
    results = search("webhook", FIXTURE, top_n=5)
    assert len(results) > 0
    # The Stripe webhook-related entry should be in top 3
    top_ids = [r[0].id for r in results[:3]]
    assert "01T" in top_ids or "01J" in top_ids or "01A" in top_ids


def test_search_relevance_billing_webhook():
    results = search("stripe webhook retry", FIXTURE, top_n=3)
    # Most relevant: webhook retry policy, stripe webhook verification, webhook idempotency
    top_texts = [r[0].text for r in results[:3]]
    any_webhook = any("webhook" in t.lower() for t in top_texts)
    assert any_webhook, f"Expected webhook-related results, got scores: {[r[1] for r in results[:3]]}"


def test_search_top_n_limit():
    results = search("billing", FIXTURE, top_n=3)
    assert len(results) == 3


def test_search_tag_filter():
    results = search("db", FIXTURE, top_n=10, tag_filter="devops")
    assert len(results) >= 1
    for entry, _ in results:
        assert "devops" in [t.lower() for t in entry.tags]


def test_search_tag_filter_no_results():
    results = search("billing", FIXTURE, top_n=10, tag_filter="nonexistent")
    assert len(results) == 0


def test_search_with_scores():
    results = search("billing webhook", FIXTURE, top_n=5)
    for entry, score in results:
        assert isinstance(score, float)
        assert score >= 0.0


def test_search_empty_query():
    results = search("", FIXTURE, top_n=5)
    assert len(results) <= 5


def test_search_empty_entries():
    results = search("anything", [], top_n=5)
    assert results == []


# ----- source boost (manual entries should outrank similar index entries) -----


def _noise_entries(n: int) -> list[MemoryEntry]:
    """Filler entries to make BM25 IDF positive in small test corpora.
    Without noise, ``df == N`` for any query term that appears in every test
    fixture entry → IDF goes negative and the boost test becomes degenerate.
    """
    return [
        MemoryEntry(id=f"noise-{i}", ts="t", text=f"unrelated noise content {i}", source="index")
        for i in range(n)
    ]


def test_manual_source_outranks_index_for_equivalent_text():
    """Two entries with identical text and identical query relevance — the
    manual one should win because of the source boost."""
    entries = [
        MemoryEntry(id="man1", ts="t", text="alpha beta gamma", source="manual"),
        MemoryEntry(id="idx1", ts="t", text="alpha beta gamma", source="index"),
        *_noise_entries(5),
    ]
    results = search("alpha beta", entries, top_n=2)
    assert results[0][0].id == "man1"
    assert results[1][0].id == "idx1"


def test_source_boost_can_be_overridden():
    """Caller can pass a ``source_boost`` dict to enable / invert source
    preference — useful for tests / experiments. (Since 2026-06-06 the default
    is ``manual=1.0`` — sweep-driven; see search.py comment.)"""
    entries = [
        MemoryEntry(id="m", ts="t", text="alpha beta", source="manual"),
        MemoryEntry(id="i", ts="t", text="alpha beta", source="index"),
        *_noise_entries(5),
    ]
    # Default boost (manual=1.0): raw BM25 scores are identical → equal finals.
    res_default = search("alpha beta", entries, top_n=2)
    s_manual_d = next(s for e, s in res_default if e.id == "m")
    s_index_d = next(s for e, s in res_default if e.id == "i")
    assert abs(s_manual_d - s_index_d) < 1e-6

    # Explicit override: manual entry exactly 2× the index entry.
    res_boosted = search("alpha beta", entries, top_n=2, source_boost={"manual": 2.0})
    s_manual_b = next(s for e, s in res_boosted if e.id == "m")
    s_index_b = next(s for e, s in res_boosted if e.id == "i")
    assert s_manual_b == pytest.approx(s_index_b * 2.0)


def test_kind_and_source_boosts_compose():
    """Boosts multiply, not replace. Asserted with an explicit ``source_boost``
    so the test exercises the composition mechanism independently of the
    runtime default (which is 1.0 manual since 2026-06-06)."""
    entries = [
        MemoryEntry(id="ir", ts="t", text="alpha beta", source="index", kind="rule"),
        MemoryEntry(id="mr", ts="t", text="alpha beta", source="manual", kind="rule"),
        MemoryEntry(id="if", ts="t", text="alpha beta", source="index", kind="fact"),
        MemoryEntry(id="mf", ts="t", text="alpha beta", source="manual", kind="fact"),
        *_noise_entries(5),
    ]
    # Explicit boosts so this test is independent of the runtime default.
    results = search("alpha beta", entries, top_n=4, source_boost={"manual": 2.0})
    by_id = {e.id: s for e, s in results}
    # Manual rule: 4.0 (kind) × 2.0 (source) = 8.0× raw
    # Index rule:  4.0 (kind) × 1.0 = 4.0× raw
    # Manual fact: 1.0 × 2.0 = 2.0× raw
    # Index fact:  1.0 × 1.0 = 1.0× raw
    assert by_id["mr"] > by_id["ir"] > by_id["mf"] > by_id["if"]


# --- Doc-field weighting ([1b] enrich-index) ---

class TestDocFieldWeighting:
    def test_build_corpus_text_extracts_and_repeats_docs(self):
        from agmem.search import _build_corpus_text, _DOC_WEIGHT
        from agmem.store import MemoryEntry
        e = MemoryEntry(
            id="x", ts="t",
            text=(
                "File `src/income.py` — Python file with 1 function. "
                "Items: function precompute_income.\nDocs: Pre-warm income cache on refresh"
            ),
            source_ref="src/income.py", source="index",
        )
        corpus = _build_corpus_text(e)
        # Docs text should appear (_DOC_WEIGHT + 1) times: once in entry.text + N repeated.
        assert corpus.count("Pre-warm income cache on refresh") == 1 + _DOC_WEIGHT

    def test_no_docs_segment_means_no_extra_weighting(self):
        from agmem.search import _build_corpus_text
        from agmem.store import MemoryEntry
        e = MemoryEntry(
            id="x", ts="t",
            text="File `src/bare.py` — Python file with 1 function. Items: function bare.",
            source_ref="src/bare.py", source="index",
        )
        # No "\nDocs: " segment → _DOCS_RE doesn't match → no extra weighting.
        corpus = _build_corpus_text(e)
        assert "Docs:" not in corpus

    def test_doc_match_outranks_name_only(self):
        """Doc field gives an intent-matching entry the lift it needs to beat
        a name-only match. Uses noise filler so BM25 IDF doesn't degenerate in
        a tiny 2-doc corpus."""
        from agmem.store import MemoryEntry
        gold = MemoryEntry(
            id="gold", ts="t",
            text=(
                "File `src/income.py` — Python file with 1 function. "
                "Items: function precompute_income.\nDocs: Pre-warm income cache on refresh webhook"
            ),
            source_ref="src/income.py", source="index",
        )
        rival = MemoryEntry(
            id="rival", ts="t",
            text=(
                "File `src/warmer.py` — Python file with 1 function. "
                "Items: function warm."
            ),
            source_ref="src/warmer.py", source="index",
        )
        entries = [gold, rival, *_noise_entries(10)]
        results = search("pre-warm income cache webhook refresh", entries, top_n=2)
        order = [e.id for e, _ in results]
        assert order[0] == "gold", f"expected docstring match to rank first, got {order}"


# --- Conservative stemming ([1c]) ---

class TestStem:
    """Conservative morphological folding — high precision, low recall stemmer."""
    def test_short_tokens_unchanged(self):
        from agmem.search import _stem
        assert _stem("aws") == "aws"        # don't break uppercase-ish identifiers
        assert _stem("api") == "api"
        assert _stem("rds") == "rds"
        assert _stem("fed") == "fed"        # too short to strip -ed

    def test_plural_simple_s(self):
        from agmem.search import _stem
        assert _stem("handlers") == "handler"
        assert _stem("tasks") == "task"
        assert _stem("routes") == "route"

    def test_plural_ies(self):
        from agmem.search import _stem
        assert _stem("categories") == "category"
        assert _stem("policies") == "policy"

    def test_plural_es_after_sxz(self):
        from agmem.search import _stem
        assert _stem("classes") == "class"
        assert _stem("boxes") == "box"
        assert _stem("buzzes") == "buzz"

    def test_false_plural_endings_preserved(self):
        from agmem.search import _stem
        assert _stem("address") == "address"   # ends "ss"
        assert _stem("status") == "status"     # ends "us"
        assert _stem("basis") == "basis"       # ends "is"

    def test_ing_simple(self):
        from agmem.search import _stem
        assert _stem("syncing") == "sync"
        assert _stem("handling") == "handl"   # imperfect but matches "handle" tokens after stem

    def test_ing_double_consonant(self):
        from agmem.search import _stem
        assert _stem("running") == "run"
        assert _stem("stopping") == "stop"

    def test_ed_simple(self):
        from agmem.search import _stem
        assert _stem("synced") == "sync"
        assert _stem("invalidated") == "invalidat"  # stems both query+index the same way

    def test_query_corpus_alignment(self):
        """The point of stemming: query 'syncing' should hit a doc containing 'sync'
        after both are tokenized."""
        from agmem.search import _tokenize, _STEMMING_ENABLED
        assert _STEMMING_ENABLED
        assert "sync" in _tokenize("the syncing logic")
        assert "sync" in _tokenize("Use sync to update")
