"""Tests for alias query expansion."""

from agmem.aliases import expand_query, ALIASES


def test_expand_mongodb():
    result = expand_query("mongodb cluster")
    assert "docdb" in result
    assert "documentdb" in result
    assert "mongo" in result
    assert "mongodb" in result


def test_expand_postgres():
    result = expand_query("postgres database")
    assert "rds" in result
    assert "aurora" in result
    assert "postgresql" in result


def test_expand_redis():
    result = expand_query("redis")
    assert "elasticache" in result


def test_expand_kafka():
    result = expand_query("kafka streaming")
    assert "msk" in result


def test_expand_no_alias():
    result = expand_query("some unknown term")
    assert result == "some unknown term"


def test_expand_multiple_terms():
    result = expand_query("mongodb kafka postgres")
    assert "docdb" in result
    assert "msk" in result
    assert "rds" in result


def test_expand_no_duplicates():
    result = expand_query("postgres rds rds")
    # Count occurrences of "rds" — should appear only once
    tokens = result.split()
    assert tokens.count("rds") == 1
    assert tokens.count("postgres") == 1
