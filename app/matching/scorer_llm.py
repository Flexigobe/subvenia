"""Gemini 2.5 Flash scoring for ranked candidates with in-process 7d cache.

3 batches of up to 10 candidates per search = 3 LLM calls. Caches by
(perfil_hash, subvencion_id) so repeated searches with the same profile cost 0.
Fallback to deterministic score if Gemini fails, times out, or no API key set.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:
    from app.matching.filter import Candidate, EmpresaProfile

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """Eres asesor de subvenciones para PYMES españolas. Para cada subvención en la lista,
evalúa cómo de bien encaja con esta empresa y devuelve EXCLUSIVAMENTE un JSON array de objetos en
el mismo orden, con campos:
  - "score": entero 0-100
  - "razon": una sola frase en español explicando el encaje (máx. 200 caracteres)

EMPRESA: cnae={cnae}, tamano={tamano}, provincia={provincia}, finalidad={finalidad}

SUBVENCIONES:
{items}

Responde SOLO con el array JSON, sin texto adicional ni markdown."""

# (score, razon, expires_unix)
_cache: dict[str, tuple[int, str | None, float]] = {}
_CACHE_TTL = 7 * 86400  # 7 days
_BATCH_SIZE = 10
_TIMEOUT_S = 8.0


def _perfil_hash(perfil: EmpresaProfile) -> str:
    blob = f"{perfil.cnae}|{perfil.tamano}|{perfil.provincia}|{','.join(sorted(perfil.finalidad))}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _cache_key(perfil_hash: str, sub_id: str) -> str:
    return f"{perfil_hash}:{sub_id}"


def _strip_markdown_fences(text: str) -> str:
    """Some models wrap output in ```json fences despite the instruction."""
    text = text.strip()
    if text.startswith("```"):
        # Drop opening fence (and optional language tag) and trailing fence
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def _score_one_batch(
    model, perfil: EmpresaProfile, batch: list[tuple[int, Candidate]]
) -> dict[int, tuple[int, str | None]]:
    """Score a single batch of up to _BATCH_SIZE candidates via the LLM.

    Returns dict mapping original index → (score, razon).
    On any LLM error returns dict with fallback values from `c.score`.
    """
    items_text = "\n".join(
        f"{i+1}. id={c.subvencion.external_id}: {(c.subvencion.titulo or '')[:200]} | "
        f"finalidad={c.subvencion.finalidad} | cnae={c.subvencion.cnae_elegible} | "
        f"organismo={c.subvencion.organismo or '-'}"
        for i, (_orig_idx, c) in enumerate(batch)
    )
    prompt = _PROMPT_TEMPLATE.format(
        cnae=perfil.cnae,
        tamano=perfil.tamano,
        provincia=perfil.provincia,
        finalidad=perfil.finalidad,
        items=items_text,
    )
    try:
        # Gemini SDK is sync; run in thread with timeout
        resp = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=_TIMEOUT_S,
        )
        text = _strip_markdown_fences(resp.text or "")
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
        out: dict[int, tuple[int, str | None]] = {}
        for (orig_idx, candidate), item in zip(batch, parsed):
            try:
                score = max(0, min(100, int(item.get("score", candidate.score))))
            except (TypeError, ValueError):
                score = candidate.score
            razon = item.get("razon")
            if razon is not None:
                razon = str(razon)[:280] or None
            out[orig_idx] = (score, razon)
        # If LLM returned fewer items than the batch, fill the rest with fallback
        for orig_idx, candidate in batch:
            out.setdefault(orig_idx, (candidate.score, None))
        return out
    except Exception as exc:
        logger.warning("Gemini scoring batch failed (%s); falling back to deterministic", exc)
        return {orig_idx: (candidate.score, None) for orig_idx, candidate in batch}


async def score_batch(
    perfil: EmpresaProfile, candidates: list[Candidate]
) -> list[tuple[int, str | None]]:
    """Devuelve [(score, razon), ...] del mismo length que candidates.

    Pipeline:
    1. Si gemini_api_key vacía → devuelve scores deterministas directamente, razon=None.
    2. Aplica cache para entradas ya vistas en últimos 7 días.
    3. Lo no cacheado lo batchea en grupos de 10 y llama al LLM.
    4. Si el LLM falla o tarda > 8s → fallback a score determinista para ese batch.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        return [(c.score, None) for c in candidates]

    # Import here so tests can monkey-patch sys.modules['google.generativeai'] cleanly
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)

    ph = _perfil_hash(perfil)
    now = time.time()
    results: list[tuple[int, str | None] | None] = [None] * len(candidates)
    to_score: list[tuple[int, Candidate]] = []

    for idx, c in enumerate(candidates):
        key = _cache_key(ph, str(c.subvencion.id))
        cached = _cache.get(key)
        if cached and cached[2] > now:
            results[idx] = (cached[0], cached[1])
        else:
            to_score.append((idx, c))

    for start in range(0, len(to_score), _BATCH_SIZE):
        batch = to_score[start : start + _BATCH_SIZE]
        batch_out = await _score_one_batch(model, perfil, batch)
        for orig_idx, (score, razon) in batch_out.items():
            results[orig_idx] = (score, razon)
            sub_id = str(candidates[orig_idx].subvencion.id)
            _cache[_cache_key(ph, sub_id)] = (score, razon, now + _CACHE_TTL)

    # Defensive: any None left → fallback
    for i, r in enumerate(results):
        if r is None:
            results[i] = (candidates[i].score, None)

    return results  # type: ignore[return-value]
