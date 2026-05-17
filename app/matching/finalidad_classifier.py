"""Gemini-based finalidad classifier for BDNS subvenciones.

Replaces the keyword heuristic in bdns_mappers.infer_finalidad for cases where
the heuristic produces ['otros']. Uses Gemini 2.5 Flash with a fixed vocabulary
of tokens. Falls back gracefully when API key is empty, request fails, or output
can't be parsed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re as _re
import time

from app.config import get_settings

logger = logging.getLogger(__name__)

# Fixed vocabulary — LLM must pick from this set, anything else is filtered out
VOCAB: set[str] = {
    "digitalizacion",
    "i+d",
    "contratacion",
    "eficiencia_energetica",
    "internacionalizacion",
    "formacion",
    "innovacion",
    "comercio",
    "turismo",
    "cultura",
    "deportes",
    "social",
    "agricultura",
    "medio_ambiente",
    "otros",
}

_PROMPT = """Eres un experto en clasificar subvenciones públicas españolas. Lee la siguiente
descripción y devuelve EXCLUSIVAMENTE un JSON array con 1 a 3 tokens del vocabulario
(NUNCA inventes tokens, solo elige de esta lista):

VOCABULARIO: digitalizacion, i+d, contratacion, eficiencia_energetica, internacionalizacion,
formacion, innovacion, comercio, turismo, cultura, deportes, social, agricultura,
medio_ambiente, otros

REGLAS:
- Elige el/los token(s) que mejor describan la finalidad económica/social de la subvención.
- 1-3 tokens máximo, ordenados de más a menos relevante.
- Si no encaja en nada específico, devuelve ["otros"].
- "i+d" cubre I+D+i, investigación, desarrollo experimental.
- "contratacion" cubre empleo, plantilla, autónomos, jóvenes.
- "social" cubre bienestar, dependencia, inclusión, igualdad.
- "comercio" y "turismo" son distintos de "internacionalizacion".

DESCRIPCIÓN:
{text}

Responde SOLO con el array JSON, sin texto adicional ni markdown."""

# (tokens, expires_unix)
_cache: dict[str, tuple[list[str], float]] = {}
_CACHE_TTL = 30 * 86400  # 30 days — texts don't change
_TIMEOUT_S = 8.0


def _cache_key(text: str) -> str:
    return hashlib.sha256(text[:500].encode("utf-8")).hexdigest()[:16]


_JSON_ARRAY_RE = _re.compile(r"\[[^\[\]]*?(?:\[[^\[\]]*?\][^\[\]]*?)*\]", _re.DOTALL)


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _extract_json_array(text: str) -> str | None:
    """Find the first plausible JSON array in ``text``, tolerant to wrappers.

    Handles cases like:
      - ```json\\n["digitalizacion"]\\n```
      - print(["digitalizacion"])
      - Here's the answer: ["digitalizacion"]
      - ["digitalizacion"] (clean)
      - ["X"]);  (trailing punctuation like the ``}\\n]);`` pattern from production)
    Returns the substring of the matched array, or None if no array is found.
    """
    if not text:
        return None
    stripped = _strip_markdown_fences(text)
    # First try: maybe it's a clean JSON array now
    s = stripped.strip()
    if s.startswith("[") and s.endswith("]"):
        return s
    # Fallback: find the first balanced [...] in the original text
    match = _JSON_ARRAY_RE.search(text)
    if match:
        return match.group(0)
    return None


async def classify(text: str | None, fallback: list[str]) -> list[str]:
    """Classify subvención text into 1-3 finalidad tokens from VOCAB.

    Returns fallback list verbatim if:
    - text is empty/None
    - gemini_api_key is empty
    - any error in API call, parsing, or vocab filtering
    """
    if not text or not text.strip():
        return fallback

    settings = get_settings()
    if not settings.gemini_api_key:
        return fallback

    # Cache check
    key = _cache_key(text)
    now = time.time()
    cached = _cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    import google.generativeai as genai

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        prompt = _PROMPT.format(text=text[:1500])

        resp = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=_TIMEOUT_S,
        )
        raw_text = resp.text or ""
        extracted = _extract_json_array(raw_text)
        if extracted is None:
            logger.warning(
                "Finalidad classifier: no JSON array found in response. Raw text (first 200 chars): %r",
                raw_text[:200],
            )
            return fallback
        try:
            parsed = json.loads(extracted)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Finalidad classifier: JSON decode failed (%s). Extracted: %r. Raw: %r",
                exc,
                extracted[:200],
                raw_text[:200],
            )
            return fallback
        if not isinstance(parsed, list):
            logger.warning(
                "Finalidad classifier: expected JSON array, got %s. Raw: %r",
                type(parsed).__name__,
                raw_text[:200],
            )
            return fallback
        # Filter unknown tokens and cap at 3
        cleaned = [t for t in parsed if isinstance(t, str) and t in VOCAB][:3]
        if not cleaned:
            return fallback
        _cache[key] = (cleaned, now + _CACHE_TTL)
        return cleaned
    except Exception as exc:
        logger.warning("Finalidad classifier failed (%s); falling back", exc)
        return fallback
