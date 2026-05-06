"""
FastAPI backend — KL Electronics Promo Tracker
"""
import asyncio
import csv
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

import database as db
import scheduler as sched
from scraper import available_sites, run_single_scraper
from summarizer import generate_digest, structure_events

INTERVAL_HOURS = int(os.getenv("SCRAPE_INTERVAL_HOURS", "24"))
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    sched.start_scheduler(interval_hours=INTERVAL_HOURS)
    yield
    sched.stop_scheduler()


app = FastAPI(
    title="KL Electronics Promo Tracker",
    description="Kuala Lumpur consumer electronics promotion & event tracker API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@app.get("/api/events")
def list_events(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    site: str | None = None,
    category: str | None = None,
    search: str | None = None,
):
    """FullCalendar-compatible event list."""
    events = db.get_events(start=start, end=end, site=site, category=category, search=search)
    return [_to_calendar_event(e) for e in events]


@app.get("/api/events/raw")
def list_events_raw(
    start: str | None = None,
    end: str | None = None,
    site: str | None = None,
    category: str | None = None,
    search: str | None = None,
):
    return db.get_events(start=start, end=end, site=site, category=category, search=search)


@app.get("/api/events/{event_id}")
def get_event(event_id: str):
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get("/api/events/export/csv")
def export_csv(
    start: str | None = None,
    end: str | None = None,
    site: str | None = None,
):
    events = db.get_events(start=start, end=end, site=site)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id", "title", "organizer", "location", "venue",
            "start_date", "end_date", "category", "summary",
            "source_site", "source_url", "tags", "scraped_at",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    for e in events:
        e["tags"] = ", ".join(e.get("tags") or [])
        writer.writerow(e)
    output.seek(0)
    fname = f"kl_promo_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

@app.post("/api/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks, site: str | None = None):
    if site:
        sites = {s["name"] for s in available_sites()}
        if site not in sites:
            raise HTTPException(status_code=400, detail=f"Unknown site: {site}")
        background_tasks.add_task(_scrape_site, site)
        return {"status": "started", "site": site}
    background_tasks.add_task(sched.scrape_and_store)
    return {"status": "started", "site": "all"}


async def _scrape_site(site_name: str):
    raw = await run_single_scraper(site_name)
    structured = await structure_events(raw)
    for event in structured:
        db.upsert_event(event)


@app.get("/api/scrape/sites")
def list_sites():
    return {"sites": available_sites()}


@app.get("/api/scrape/logs")
def scrape_logs(limit: int = Query(50, le=200)):
    return db.get_scrape_logs(limit)


@app.get("/api/scrape/status")
def scrape_status():
    return {
        "next_run": sched.get_next_run(),
        "interval_hours": INTERVAL_HOURS,
    }


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

@app.get("/api/digest")
def get_digest(start: str | None = None, end: str | None = None):
    events = db.get_events(start=start, end=end)
    report = generate_digest(events)
    return {
        "report": report,
        "event_count": len(events),
        "generated_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def stats():
    all_events = db.get_events()
    sites: dict = {}
    categories: dict = {}
    tiers: dict = {}
    for e in all_events:
        s = e.get("source_site", "Unknown")
        sites[s] = sites.get(s, 0) + 1
        c = e.get("category", "Unknown")
        categories[c] = categories.get(c, 0) + 1
    return {
        "total_events": len(all_events),
        "by_site": sites,
        "by_category": categories,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SITE_COLORS = {
    "HOMEDEC":           "#4CAF50",
    "HomeLove MY":       "#8BC34A",
    "PIKOM":             "#CDDC39",
    "Senheng":           "#e53935",
    "Harvey Norman MY":  "#ff6f00",
    "Courts MY":         "#1565c0",
    "Best Denki MY":     "#2e7d32",
    "MITEC":             "#6a1b9a",
    "KLCC Convention":   "#ad1457",
    "PWTC":              "#00695c",
    "Mid Valley":        "#4527a0",
    "Sunway Pyramid":    "#00838f",
    "Suria KLCC":        "#d84315",
    "Starling Mall":     "#0277bd",
    "Lowyat.net":        "#546e7a",
    "SoyaCincau":        "#5d4037",
    "Samsung MY":        "#1976d2",
    "LG MY":             "#b71c1c",
    "Panasonic MY":      "#0288d1",
    "Lazada MY":         "#ff6900",
    "Shopee MY":         "#ee4d2d",
}


def _to_calendar_event(e: dict) -> dict:
    color = SITE_COLORS.get(e.get("source_site", ""), "#546e7a")
    end_date = e.get("end_date") or e.get("start_date") or ""
    if end_date:
        try:
            from datetime import date, timedelta
            end_exclusive = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        except ValueError:
            end_exclusive = end_date
    else:
        end_exclusive = end_date

    return {
        "id":    e.get("id"),
        "title": e.get("title", ""),
        "start": e.get("start_date", ""),
        "end":   end_exclusive,
        "color": color,
        "extendedProps": {
            "organizer":   e.get("organizer", ""),
            "location":    e.get("location", ""),
            "venue":       e.get("venue", ""),
            "category":    e.get("category", ""),
            "summary":     e.get("summary", ""),
            "description": e.get("description", ""),
            "source_url":  e.get("source_url", ""),
            "source_site": e.get("source_site", ""),
            "tags":        e.get("tags", []),
            "scraped_at":  e.get("scraped_at", ""),
            "updated_at":  e.get("updated_at", ""),
        },
    }


# ---------------------------------------------------------------------------
# Frontend static files
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
