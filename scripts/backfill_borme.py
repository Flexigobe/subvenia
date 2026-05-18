"""Backfill BORME with parallel-across-dates + resume from state file.

Designed for multi-year backfills (1-30 years). Each date takes ~90s sequential
with internal PDF concurrency=5; processing 4 dates in parallel gives effective
~6 dates/min, so 3 years (≈750 working days) finishes in ~2 hours wall time at
peak, more realistically 6-10 hours with BOE rate variance.

State file (`/tmp/borme_backfill_state.json` by default) tracks completed dates.
Re-running skips them. Safe to Ctrl+C: in-flight dates finish, state saves, exit.

Examples:
    PYTHONPATH=. python scripts/backfill_borme.py --days 1095
    PYTHONPATH=. python scripts/backfill_borme.py --days 365 --parallel 8
    PYTHONPATH=. python scripts/backfill_borme.py --days 30 --until 2025-05-16

The DB upsert is idempotent (UNIQUE on hoja_rm), so accidental re-runs are safe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from app.db.session import SessionLocal
from app.sync.borme_ingester import sync_day


_DEFAULT_STATE = Path("/tmp/borme_backfill_state.json")
_BATCH_DELAY_SECONDS = 1.0  # Polite delay between batches


# ─── State file ──────────────────────────────────────────────────────────────


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set(data.get("completed", []))
    except Exception as exc:
        print(f"[WARN] state file unreadable ({exc}); starting fresh", file=sys.stderr)
        return set()


def save_state(path: Path, completed: set[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "completed": sorted(completed),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(completed),
    }
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


# ─── Date helpers ────────────────────────────────────────────────────────────


def weekdays_in_range(start: date, end: date) -> list[date]:
    """Inclusive list of Mon-Fri dates from start to end."""
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # Mon-Fri
            out.append(cur)
        cur += timedelta(days=1)
    return out


# ─── Worker ──────────────────────────────────────────────────────────────────


async def process_date(target: date) -> dict[str, int]:
    """Process one date and return stats. Each call uses its own DB session."""
    with SessionLocal() as session:
        return await sync_day(session, target)


# ─── Main loop ───────────────────────────────────────────────────────────────


_stop_requested = False


def _signal_handler(signum, frame) -> None:
    global _stop_requested
    if not _stop_requested:
        print(
            "\n[INTERRUPT] Stop requested. Finishing in-flight dates then exiting…",
            file=sys.stderr,
        )
        _stop_requested = True
    else:
        print("\n[INTERRUPT x2] Hard stop.", file=sys.stderr)
        sys.exit(130)


def _format_eta(seconds: float) -> str:
    seconds = int(seconds)
    h, m = divmod(seconds, 3600)
    m, s = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


async def main(
    days: int,
    until: date,
    parallel: int,
    state_path: Path,
    skip_completed: bool = True,
) -> int:
    signal.signal(signal.SIGINT, _signal_handler)

    completed = load_state(state_path) if skip_completed else set()
    print(
        f"BORME backfill: from {until - timedelta(days=days)} to {until} "
        f"({days} calendar days, parallel={parallel}, "
        f"state has {len(completed)} dates already done)"
    )

    candidates = weekdays_in_range(until - timedelta(days=days), until)
    pending = [d for d in candidates if d.isoformat() not in completed]
    if not pending:
        print("Nothing to do — all dates in range already in state file.")
        return 0

    print(f"Will process {len(pending)} new dates (skipping {len(candidates) - len(pending)} already done).")

    totals = {"created": 0, "updated": 0, "errors": 0, "skipped_no_hoja": 0}
    overall_start = time.monotonic()
    batches = [pending[i:i + parallel] for i in range(0, len(pending), parallel)]
    total_batches = len(batches)

    for idx, batch in enumerate(batches, 1):
        if _stop_requested:
            break
        batch_start = time.monotonic()
        # Run the batch concurrently
        results = await asyncio.gather(
            *(process_date(d) for d in batch),
            return_exceptions=True,
        )
        batch_dur = time.monotonic() - batch_start

        # Aggregate + persist completion per date
        for d, res in zip(batch, results):
            if isinstance(res, Exception):
                print(f"  {d}: FAILED: {res}", file=sys.stderr)
                totals["errors"] += 1
                continue
            totals["created"] += res["created"]
            totals["updated"] += res["updated"]
            totals["errors"] += res["errors"]
            totals["skipped_no_hoja"] += res["skipped_no_hoja"]
            completed.add(d.isoformat())
            print(
                f"  {d}: created={res['created']:>5} updated={res['updated']:>5} "
                f"skipped={res['skipped_no_hoja']:>4} errors={res['errors']} "
                f"pdfs={res['total_pdfs']}"
            )

        # Save state after each batch
        save_state(state_path, completed)

        # ETA based on average batch duration so far
        elapsed = time.monotonic() - overall_start
        avg_batch = elapsed / idx
        remaining_batches = total_batches - idx
        eta_seconds = remaining_batches * avg_batch
        print(
            f"[batch {idx}/{total_batches}] {len(batch)} dates in {batch_dur:.1f}s · "
            f"running totals: created={totals['created']:,} updated={totals['updated']:,} "
            f"errors={totals['errors']} · ETA {_format_eta(eta_seconds)}"
        )

        # Polite delay between batches (unless stopping)
        if idx < total_batches and not _stop_requested:
            await asyncio.sleep(_BATCH_DELAY_SECONDS)

    total_elapsed = time.monotonic() - overall_start
    print(
        f"\nDone in {_format_eta(total_elapsed)}. "
        f"created={totals['created']:,} updated={totals['updated']:,} "
        f"skipped_no_hoja={totals['skipped_no_hoja']:,} errors={totals['errors']}"
    )
    print(f"State saved to {state_path} ({len(completed)} dates total).")
    return 130 if _stop_requested else 0


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=1095,
                    help="Calendar days back from --until (default 1095 = 3 years)")
    ap.add_argument(
        "--until",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today(),
        help="End date inclusive, YYYY-MM-DD. Default: today.",
    )
    ap.add_argument(
        "--parallel", type=int, default=4,
        help="Dates processed concurrently (1-10, default 4)",
    )
    ap.add_argument(
        "--state-file", type=Path, default=_DEFAULT_STATE,
        help=f"State JSON path (default {_DEFAULT_STATE})",
    )
    ap.add_argument(
        "--no-skip", action="store_true",
        help="Ignore state file and process every date.",
    )
    args = ap.parse_args()
    if not 1 <= args.parallel <= 10:
        ap.error("--parallel must be 1-10")
    if args.days < 0:
        ap.error("--days must be >= 0")
    return args


if __name__ == "__main__":
    args = parse_args()
    exit_code = asyncio.run(main(
        days=args.days,
        until=args.until,
        parallel=args.parallel,
        state_path=args.state_file,
        skip_completed=not args.no_skip,
    ))
    sys.exit(exit_code)
