"""Backfill BORME for last N days.

Iterates Monday-Friday dates within the window. Idempotent: re-running on an
already-ingested date just upserts (no duplicates due to UNIQUE on hoja_rm).

Usage:
    PYTHONPATH=. python scripts/backfill_borme.py --days 90
    PYTHONPATH=. python scripts/backfill_borme.py --days 7 --until 2025-05-16

Logs progress per day. Resumable by simply re-running with the same args.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta

from app.db.session import SessionLocal
from app.sync.borme_ingester import sync_day


async def main(days: int, until: date) -> None:
    start = until - timedelta(days=days)
    total_created = total_updated = total_errors = 0
    cur = start
    print(f"Backfilling BORME from {start} to {until} ({days} days)")
    while cur <= until:
        if cur.weekday() < 5:  # Mon-Fri
            with SessionLocal() as session:
                try:
                    stats = await sync_day(session, cur)
                    total_created += stats["created"]
                    total_updated += stats["updated"]
                    total_errors += stats["errors"]
                    print(f"  {cur}: created={stats['created']:>5} updated={stats['updated']:>5} "
                          f"skipped={stats['skipped_no_hoja']:>4} errors={stats['errors']} "
                          f"pdfs={stats['total_pdfs']}")
                except Exception as exc:
                    print(f"  {cur}: FAILED: {exc}", file=sys.stderr)
                    total_errors += 1
        cur += timedelta(days=1)
    print(f"\nDone. Total: created={total_created} updated={total_updated} errors={total_errors}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30, help="Number of days back from --until (default: 30)")
    ap.add_argument(
        "--until",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today(),
        help="End date (inclusive) in YYYY-MM-DD format. Default: today.",
    )
    args = ap.parse_args()
    asyncio.run(main(args.days, args.until))
