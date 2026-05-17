"""One-off: re-classify BDNS subvenciones that ended up with finalidad=['otros'] using Gemini.

Targets only OPEN records to stay within Gemini's free tier (1,500 req/day).
Run manually after Plan 3 Task 2 is deployed:

    source .venv/bin/activate
    python scripts/reclassify_finalidad.py
"""

import asyncio
import logging as _logging
import sys
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import Subvencion
from app.matching.finalidad_classifier import classify
from app.sync.bdns_mappers import infer_finalidad

# Per-call debug log — truncated on each run so it stays small
_debug = _logging.getLogger("reclassify_debug")
_debug.setLevel(_logging.DEBUG)
_h = _logging.FileHandler("/tmp/reclassify_debug.log", mode="w")
_h.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_debug.addHandler(_h)
_debug.propagate = False


async def main() -> None:
    with SessionLocal() as session:
        rows = session.execute(
            select(Subvencion).where(
                Subvencion.source == "bdns",
                Subvencion.estado == "abierta",
            )
        ).scalars().all()
        candidates = [
            s
            for s in rows
            if not s.finalidad or s.finalidad == ["otros"]
        ]
        print(f"Reclassifying {len(candidates)} of {len(rows)} open BDNS records...")
        improved = unchanged = fallbacks_used = 0
        _debug.info("Starting reclassify run — %d candidates", len(candidates))
        for i, sub in enumerate(candidates, 1):
            text = (sub.raw_payload or {}).get("descripcionBasesReguladoras") or sub.titulo or ""
            fallback = infer_finalidad((sub.raw_payload or {}).get("descripcionFinalidad")) or ["otros"]
            new = await classify(text[:1500], fallback=fallback)
            used_fallback = new == fallback
            if used_fallback:
                fallbacks_used += 1
            _debug.info(
                "nif=%s text=%r → result=%s fallback_used=%s",
                sub.external_id,
                text[:100],
                new,
                used_fallback,
            )
            if new and new != sub.finalidad:
                sub.finalidad = new
                improved += 1
            else:
                unchanged += 1
            if i % 50 == 0:
                session.commit()
                print(f"  Processed {i}/{len(candidates)} — improved: {improved}, fallbacks: {fallbacks_used}")
        session.commit()
        _debug.info("Run complete — improved=%d unchanged=%d fallbacks=%d", improved, unchanged, fallbacks_used)
        print(f"\nDone. Total improved: {improved}, unchanged: {unchanged}, fallbacks: {fallbacks_used}")


if __name__ == "__main__":
    asyncio.run(main())
