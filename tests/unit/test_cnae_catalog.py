# tests/unit/test_cnae_catalog.py
import pytest

from app.lib.cnae_catalog import get_by_code, search


def test_get_by_code_returns_description():
    result = get_by_code("6201")
    assert result is not None
    assert "ordenador" in result.description.lower() or "programación" in result.description.lower()


def test_get_by_code_unknown_returns_none():
    assert get_by_code("9999") is None


def test_search_by_partial_description_returns_matches():
    results = search("agricultura", limit=5)
    assert len(results) >= 1
    assert any("agricultura" in r.description.lower() or r.code.startswith("01") for r in results)


def test_search_by_code_prefix():
    results = search("62", limit=10)
    assert len(results) >= 1
    assert all(r.code.startswith("62") for r in results[:3])


def test_search_empty_returns_empty():
    assert search("", limit=10) == []


def test_search_respects_limit():
    results = search("ind", limit=3)
    assert len(results) <= 3
