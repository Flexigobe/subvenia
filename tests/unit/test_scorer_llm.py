"""Tests for the Gemini LLM scorer."""

import json
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from app.db.models import Subvencion
from app.matching.filter import Candidate, EmpresaProfile


def _make_candidate(external_id: str, titulo: str, score: int = 50) -> Candidate:
    sub = Subvencion(
        source="bdns",
        external_id=external_id,
        titulo=titulo,
        ambito="estatal",
        cnae_elegible=["6201"],
        finalidad=["digitalizacion"],
        estado="abierta",
        fecha_fin=date.today() + timedelta(days=30),
    )
    # Give it a fake UUID without committing to DB
    import uuid
    sub.id = uuid.uuid4()
    return Candidate(subvencion=sub, score=score)


@pytest.fixture
def perfil():
    return EmpresaProfile(cnae="6201", tamano="pequena", provincia="08", finalidad=["digitalizacion"])


@pytest.fixture
def clear_cache():
    """Reset the in-process score cache before each test."""
    from app.matching import scorer_llm
    scorer_llm._cache.clear()
    yield
    scorer_llm._cache.clear()


@pytest.fixture
def mock_genai(monkeypatch):
    """Mock google.generativeai.GenerativeModel.generate_content to return controlled JSON."""

    class MockResponse:
        def __init__(self, text: str) -> None:
            self.text = text

    class MockModel:
        last_prompt = None

        def __init__(self, model_name: str) -> None:
            self.model_name = model_name

        def generate_content(self, prompt: str) -> MockResponse:
            MockModel.last_prompt = prompt
            # Default: respond with a JSON array matching the count of items
            # Count "id=" tokens in the prompt to know how many we need to respond to
            n = prompt.count("id=")
            items = [{"score": 75 - i * 2, "razon": f"Encaja por motivo {i}"} for i in range(n)]
            return MockResponse(json.dumps(items))

    fake_genai = MagicMock()
    fake_genai.configure = MagicMock()
    fake_genai.GenerativeModel = MockModel
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    # Also set GEMINI_API_KEY in settings — easiest via monkeypatching get_settings
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "fake-key-for-test")
    return MockModel


@pytest.mark.asyncio
async def test_score_batch_returns_score_and_razon_for_each(mock_genai, clear_cache, perfil):
    from app.matching.scorer_llm import score_batch

    candidates = [_make_candidate(f"X{i}", f"Title {i}") for i in range(3)]
    results = await score_batch(perfil, candidates)

    assert len(results) == 3
    for score, razon in results:
        assert 0 <= score <= 100
        assert razon is not None
        assert "motivo" in razon


@pytest.mark.asyncio
async def test_score_batch_with_no_api_key_falls_back_to_deterministic(
    monkeypatch, clear_cache, perfil
):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "")

    from app.matching.scorer_llm import score_batch

    candidates = [_make_candidate("X1", "T", score=42)]
    results = await score_batch(perfil, candidates)

    assert results == [(42, None)]


@pytest.mark.asyncio
async def test_score_batch_caches_results_for_same_perfil_and_subvencion(
    mock_genai, clear_cache, perfil
):
    from app.matching.scorer_llm import score_batch

    candidates = [_make_candidate("X1", "T1")]
    results1 = await score_batch(perfil, candidates)
    # Second call should hit cache — verify by checking that generate_content not called again
    mock_genai.last_prompt = None  # reset marker
    results2 = await score_batch(perfil, candidates)

    assert results1 == results2
    assert mock_genai.last_prompt is None  # no new prompt sent


@pytest.mark.asyncio
async def test_score_batch_falls_back_on_llm_exception(monkeypatch, clear_cache, perfil):
    import sys
    fake_genai = MagicMock()
    fake_genai.configure = MagicMock()

    class FailingModel:
        def __init__(self, model_name: str) -> None:
            pass

        def generate_content(self, prompt: str):
            raise RuntimeError("API down")

    fake_genai.GenerativeModel = FailingModel
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "gemini_api_key", "fake-key")

    from app.matching.scorer_llm import score_batch

    candidates = [_make_candidate("X1", "T", score=33), _make_candidate("X2", "T2", score=44)]
    results = await score_batch(perfil, candidates)

    assert results == [(33, None), (44, None)]


@pytest.mark.asyncio
async def test_score_batch_batches_into_groups_of_10(mock_genai, clear_cache, perfil):
    """25 candidates → 3 LLM calls (10 + 10 + 5)."""
    from app.matching.scorer_llm import score_batch

    candidates = [_make_candidate(f"X{i}", f"T{i}") for i in range(25)]
    call_count = {"n": 0}
    original = mock_genai.generate_content

    def counter(self, prompt):
        call_count["n"] += 1
        return original(self, prompt)

    mock_genai.generate_content = counter

    results = await score_batch(perfil, candidates)
    assert len(results) == 25
    assert call_count["n"] == 3  # 10 + 10 + 5
