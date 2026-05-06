"""
APScheduler periodic scraping job.
Pipeline: scrape → Claude structure → Firestore upsert
"""
import asyncio
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import database as db
from scraper import run_all_scrapers
from summarizer import structure_events

_scheduler: AsyncIOScheduler | None = None


async def scrape_and_store() -> dict:
    print("[scheduler] Scrape job started.")
    db.init_db()

    site_results = await run_all_scrapers()
    total_new = 0
    total_updated = 0
    report: dict = {}

    for site_name, raw_events in site_results.items():
        db.log_scrape(site_name, "running")
        try:
            if not raw_events:
                db.log_scrape(site_name, "ok", 0)
                report[site_name] = {"new": 0, "updated": 0, "raw": 0}
                continue

            structured = await structure_events(raw_events)
            new_count = upd_count = 0
            for event in structured:
                _, is_new = db.upsert_event(event)
                if is_new:
                    new_count += 1
                else:
                    upd_count += 1

            db.log_scrape(site_name, "ok", len(structured))
            report[site_name] = {"new": new_count, "updated": upd_count, "raw": len(raw_events)}
            total_new += new_count
            total_updated += upd_count
            print(f"[scheduler] {site_name}: +{new_count} new, ~{upd_count} updated")
        except Exception as exc:
            db.log_scrape(site_name, "error", 0, str(exc))
            report[site_name] = {"error": str(exc)}
            print(f"[scheduler] {site_name} ERROR: {exc}")

    print(f"[scheduler] Done. total_new={total_new}, total_updated={total_updated}")
    return {"total_new": total_new, "total_updated": total_updated, "by_site": report}


def start_scheduler(interval_hours: int = 24):
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        scrape_and_store,
        trigger=IntervalTrigger(hours=interval_hours),
        id="scrape_job",
        name="KL Promo Scraper",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    print(f"[scheduler] Started — interval: every {interval_hours}h")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


def get_next_run() -> str | None:
    if not _scheduler:
        return None
    job = _scheduler.get_job("scrape_job")
    return job.next_run_time.isoformat() if job and job.next_run_time else None
