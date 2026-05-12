"""Tests for search module: BM25 relevance and filtering."""

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
