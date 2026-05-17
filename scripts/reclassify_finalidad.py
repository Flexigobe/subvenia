"""One-off: re-classify BDNS subvenciones that ended up with finalidad=['otros'] using Gemini.

Targets only OPEN records to stay within Gemini's free tier (1,500 req/day).
Run manually after Plan 3 Task 2 is deployed:

    source .venv/bin/activate
    python scripts/reclassify_finalidad.py
"""

import asyncio
import sys
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import Subvencion
from app.matching.finalidad_classifier import classify
from app.sync.bdns_mappers import infer_finalidad


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
        improved = unchanged = 0
        for i, sub in enumerate(candidates, 1):
            text = (sub.raw_payload or {}).get("descripcionBasesReguladoras") or sub.titulo or ""
            fallback = infer_finalidad((sub.raw_payload or {}).get("descripcionFinalidad")) or ["otros"]
            new = await classify(text[:1500], fallback=fallback)
            if new and new != sub.finalidad:
                sub.finalidad = new
                improved += 1
            else:
                unchanged += 1
            if i % 50 == 0:
                session.commit()
                print(f"  Processed {i}/{len(candidates)} — improved: {improved}")
        session.commit()
        print(f"\nDone. Total improved: {improved}, unchanged: {unchanged}")


if __name__ == "__main__":
    asyncio.run(main())
