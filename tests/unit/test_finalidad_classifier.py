"""Tests for the Gemini-based finalidad classifier."""

import json
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def clear_cache():
    from app.matching import finalidad_classifier
    finalidad_classifier._cache.clear()
    yield
    finalidad_classifier._cache.clear()


@pytest.fixture
def mock_gemini(monkeypatch):
    """Mock google.generativeai to return a controllable response."""

    class MockResponse:
        def __init__(self, text: str) -> None:
            self.text = text

    class MockModel:
        last_prompt: str | None = None
        response_text: str = '["digitalizacion"]'

        def __init__(self, model_name: str) -> None:
            self.model_name = model_name

        def generate_content(self, prompt: str) -> MockResponse:
            MockModel.last_prompt = prompt
            return MockResponse(MockModel.response_text)

    fake_genai = MagicMock()
    fake_genai.configure = MagicMock()
    fake_genai.GenerativeModel = MockModel
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "fake-key-for-test")
    return MockModel


@pytest.mark.asyncio
async def test_classify_returns_tokens_from_gemini(mock_gemini, clear_cache):
    mock_gemini.response_text = '["digitalizacion"]'
    from app.matching.finalidad_classifier import classify

    result = await classify("Ayudas para digitalización de PYMEs", fallback=["otros"])
    assert "digitalizacion" in result


@pytest.mark.asyncio
async def test_classify_returns_multiple_tokens(mock_gemini, clear_cache):
    mock_gemini.response_text = '["i+d", "innovacion"]'
    from app.matching.finalidad_classifier import classify

    result = await classify("Convocatoria de I+D+i", fallback=["otros"])
    assert "i+d" in result
    assert "innovacion" in result


@pytest.mark.asyncio
async def test_classify_falls_back_on_invalid_json(mock_gemini, clear_cache):
    mock_gemini.response_text = "not json at all"
    from app.matching.finalidad_classifier import classify

    result = await classify("texto", fallback=["i+d"])
    assert result == ["i+d"]


@pytest.mark.asyncio
async def test_classify_strips_markdown_fences(mock_gemini, clear_cache):
    mock_gemini.response_text = '```json\n["formacion"]\n```'
    from app.matching.finalidad_classifier import classify

    result = await classify("Programa formativo", fallback=["otros"])
    assert result == ["formacion"]


@pytest.mark.asyncio
async def test_classify_filters_unknown_tokens(mock_gemini, clear_cache):
    """LLM might invent tokens; only accept ones from our vocab."""
    mock_gemini.response_text = '["digitalizacion", "INVENTED_TOKEN", "innovacion"]'
    from app.matching.finalidad_classifier import classify

    result = await classify("texto", fallback=["otros"])
    assert "digitalizacion" in result
    assert "innovacion" in result
    assert "INVENTED_TOKEN" not in result


@pytest.mark.asyncio
async def test_classify_caps_at_3_tokens(mock_gemini, clear_cache):
    mock_gemini.response_text = (
        '["digitalizacion", "i+d", "innovacion", "formacion", "internacionalizacion"]'
    )
    from app.matching.finalidad_classifier import classify

    result = await classify("texto", fallback=["otros"])
    assert len(result) <= 3


@pytest.mark.asyncio
async def test_classify_falls_back_when_gemini_raises(monkeypatch, clear_cache):
    class FailingModel:
        def __init__(self, model_name: str) -> None:
            pass

        def generate_content(self, prompt: str):
            raise RuntimeError("API down")

    fake_genai = MagicMock()
    fake_genai.configure = MagicMock()
    fake_genai.GenerativeModel = FailingModel
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "gemini_api_key", "fake-key")

    from app.matching.finalidad_classifier import classify

    result = await classify("texto", fallback=["otros"])
    assert result == ["otros"]


@pytest.mark.asyncio
async def test_classify_no_api_key_returns_fallback(monkeypatch, clear_cache):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "gemini_api_key", "")

    from app.matching.finalidad_classifier import classify

    result = await classify("Programa de digitalización", fallback=["otros"])
    assert result == ["otros"]


@pytest.mark.asyncio
async def test_classify_empty_text_returns_fallback(mock_gemini, clear_cache):
    from app.matching.finalidad_classifier import classify

    assert await classify("", fallback=["i+d"]) == ["i+d"]
    assert await classify(None, fallback=["i+d"]) == ["i+d"]


@pytest.mark.asyncio
async def test_classify_uses_cache(mock_gemini, clear_cache):
    mock_gemini.response_text = '["digitalizacion"]'
    from app.matching.finalidad_classifier import classify

    result1 = await classify("Digitalización empresarial", fallback=["otros"])
    mock_gemini.last_prompt = None
    result2 = await classify("Digitalización empresarial", fallback=["otros"])

    assert result1 == result2
    assert mock_gemini.last_prompt is None  # cache hit, no LLM call


@pytest.mark.asyncio
async def test_classify_extracts_array_from_print_wrapper(mock_gemini, clear_cache):
    """LLM responds like `print(["digitalizacion"])` — extract the array."""
    mock_gemini.response_text = 'print(["digitalizacion", "i+d"])'
    from app.matching.finalidad_classifier import classify

    result = await classify("Programa de digitalización e I+D", fallback=["otros"])
    assert "digitalizacion" in result
    assert "i+d" in result


@pytest.mark.asyncio
async def test_classify_extracts_array_from_prefix_text(mock_gemini, clear_cache):
    """LLM responds with chat prefix before the array — extract just the array."""
    mock_gemini.response_text = 'Here\'s the result: ["formacion"] hope that helps!'
    from app.matching.finalidad_classifier import classify

    result = await classify("Programa formativo", fallback=["otros"])
    assert "formacion" in result


@pytest.mark.asyncio
async def test_classify_extracts_array_with_trailing_punctuation(mock_gemini, clear_cache):
    """The `}\\n]);` pattern observed in production — array with trailing junk."""
    mock_gemini.response_text = '["innovacion"]); '
    from app.matching.finalidad_classifier import classify

    result = await classify("Innovación", fallback=["otros"])
    assert "innovacion" in result


@pytest.mark.asyncio
async def test_classify_returns_fallback_when_no_array_in_response(mock_gemini, clear_cache):
    """LLM responds with prose only, no array — fallback applies."""
    mock_gemini.response_text = "I don't know the answer."
    from app.matching.finalidad_classifier import classify

    result = await classify("Texto", fallback=["i+d"])
    assert result == ["i+d"]
