"""Sliding-window per-client rate limiter for POST /search.

In-memory: each client (identified by SHA256(ip + UA[:50])) has a deque of UTC
timestamps within the last 1h. On each request:
  1. Drop expired entries.
  2. If len < limit: append now, allow.
  3. Else: reject 429 with Retry-After computed from the oldest entry.

Single-process: state is local to one uvicorn worker. For multi-worker production
this would need Redis, but for our single-worker Railway deployment this is fine.
Admin routes and GETs are exempt (only POST /search is metered).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 3600  # 1 hour


def _client_key(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")[:50]
    return hashlib.sha256(f"{ip}|{ua}".encode()).hexdigest()[:16]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter applied only to POST /search."""

    def __init__(self, app, *, requests_per_window: int = 60):
        super().__init__(app)
        self.limit = requests_per_window
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def _is_rate_limited_path(self, request: Request) -> bool:
        # Only POST /search is metered; everything else (GETs, admin, htmx enrich) passes through.
        return request.method == "POST" and request.url.path == "/search"

    async def dispatch(self, request: Request, call_next):
        if not self._is_rate_limited_path(request):
            return await call_next(request)

        key = _client_key(request)
        bucket = self._buckets[key]
        now = time.monotonic()

        # Drop expired entries from the left
        while bucket and (now - bucket[0]) > _WINDOW_SECONDS:
            bucket.popleft()

        if len(bucket) >= self.limit:
            oldest = bucket[0]
            retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
            retry_after = max(1, retry_after)
            logger.info(
                "Rate-limit hit: key=%s count=%d window=%ds retry_after=%ds",
                key, len(bucket), _WINDOW_SECONDS, retry_after,
            )
            minutes = max(1, retry_after // 60)
            html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Demasiadas búsquedas</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:480px;margin:80px auto;padding:0 24px;text-align:center;color:#222}}
h1{{color:#7a5300}}p{{color:#555;line-height:1.5}}a{{color:#1e6b2a}}</style></head>
<body><h1>&#9200; Demasiadas búsquedas</h1>
<p>Has hecho más de {self.limit} búsquedas en la última hora. Vuelve en aproximadamente <strong>{minutes} {"minuto" if minutes == 1 else "minutos"}</strong>.</p>
<p><a href="/">&#8592; Volver al inicio</a></p></body></html>"""
            return HTMLResponse(
                content=html,
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        return await call_next(request)
