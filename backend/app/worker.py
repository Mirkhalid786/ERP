"""
Background worker: sends queued WhatsApp messages and creates payment reminders.

Runs as its own long-lived process (the `worker` service in docker-compose), not
inside web requests, so nothing is lost when a request ends. All state lives in
MySQL (`message_outbox`), so a restart simply picks up where it left off.

    python -m app.worker                 # run forever
    python -m app.worker --once          # one pass, then exit (cron / Task Scheduler)
    python -m app.worker --once --dry-run [--date 2026-11-30]
                                         # show which reminders would be created; writes nothing
"""
import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import date
from typing import Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.schema_guard import check_schema
from app.models.auth import Pharmacy
from app.services import admin_ops
from app.services.messaging import process_outbox, scan_reminders

log = logging.getLogger("pharmacy.worker")


async def scan_all(session_factory, on_date: Optional[date] = None, dry_run: bool = False) -> list:
    results = []
    async with session_factory() as db:
        pharmacy_ids = (await db.execute(
            select(Pharmacy.id).where(Pharmacy.is_setup_complete.is_(True))
        )).scalars().all()
    for pharmacy_id in pharmacy_ids:
        async with session_factory() as db:
            rows = await scan_reminders(db, pharmacy_id, on_date=on_date, dry_run=dry_run)
            if not dry_run:
                await db.commit()
        results.extend(rows)
    return results


async def run_once(session_factory=AsyncSessionLocal, scan: bool = True, dry_run: bool = False,
                   on_date: Optional[date] = None) -> dict:
    created = []
    if scan:
        created = await scan_all(session_factory, on_date=on_date, dry_run=dry_run)
    sent = 0 if dry_run else await process_outbox(session_factory)
    return {"reminder_candidates": created, "messages_attempted": sent}


class _SchemaPaused(Exception):
    """This pass was skipped because the database schema does not match the code."""


async def _schema_state() -> dict:
    try:
        return await check_schema(engine)
    finally:
        await engine.dispose()


async def run_forever() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    last_scan = None
    last_schema_reason = None
    log.info("Worker started (interval %ss, reminder scan every %s min)",
             settings.WORKER_INTERVAL_SECONDS, settings.WORKER_REMINDER_SCAN_MINUTES)
    while not stop.is_set():
        try:
            # Do nothing at all (no sends, no heartbeat, no backups) unless the schema matches this code
            state = await check_schema(engine)
            if not state["ok"]:
                if state["reason"] != last_schema_reason:
                    log.error("Worker paused, waiting for a planned migration: %s", state["reason"])
                    last_schema_reason = state["reason"]
                raise _SchemaPaused()
            if last_schema_reason is not None:
                log.info("Schema matches revision %s; worker resuming", state["code_head"])
                last_schema_reason = None
            now = loop.time()
            do_scan = last_scan is None or now - last_scan >= settings.WORKER_REMINDER_SCAN_MINUTES * 60
            result = await run_once(scan=do_scan)
            if do_scan:
                last_scan = now
                made = sum(1 for r in result["reminder_candidates"] if r.get("message_id") and r["eligible"])
                log.info("Reminder scan: %s new reminder(s)", made)
            if result["messages_attempted"]:
                log.info("Sent/attempted %s message(s)", result["messages_attempted"])
            async with AsyncSessionLocal() as session:
                await admin_ops.worker_heartbeat(session)
                await session.commit()
            backup = await admin_ops.maybe_scheduled_backup(AsyncSessionLocal)
            if backup:
                log.info("Scheduled backup: %s", backup)
        except _SchemaPaused:
            pass
        except Exception:  # Keep running; the next pass retries
            log.exception("Worker pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.WORKER_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("Worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pharmacy ERP background worker")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit")
    parser.add_argument("--dry-run", action="store_true", help="With --once: report reminders without writing")
    parser.add_argument("--date", type=date.fromisoformat, help="Business date for the reminder scan (testing)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.once:
        state = asyncio.run(_schema_state())
        if not state["ok"]:
            print(f"Refusing to run: {state['reason']}", file=sys.stderr)
            raise SystemExit(3)
        result = asyncio.run(run_once(dry_run=args.dry_run, on_date=args.date))
        print(json.dumps(result, default=str, indent=2))
    else:
        asyncio.run(run_forever())


if __name__ == "__main__":
    main()
