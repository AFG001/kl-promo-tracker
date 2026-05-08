"""
Standalone scraping job for GitHub Actions.
No FastAPI, no Claude API required.

Flow:
  1. Scrape all sites
  2. Parse with simple rule-based parser
  3. Upsert to Firestore
  4. Export all events to docs/events.json (served by GitHub Pages)
"""
import asyncio
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "backend"))

# ── Firebase: support JSON string secret (GitHub Actions) ─────────────────────
_key_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")
if _key_json:
    _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    _tmp.write(_key_json)
    _tmp.close()
    os.environ["FIREBASE_SERVICE_ACCOUNT_KEY"] = _tmp.name

# ── load .env when running locally ────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from scraper import run_all_scrapers
from scraper_pw import run_pw_scrapers
from simple_parser import parse_events
import database as db

DOCS_DIR = ROOT / "docs"

SITE_COLORS = {
    "HOMEDEC":           "#4CAF50",
    "HomeLove MY":       "#8BC34A",
    "PIKOM":             "#CDDC39",
    "Expolah":           "#00acc1",
    "MTE":               "#7b1fa2",
    "Senheng":           "#e53935",
    "Harvey Norman MY":  "#ff6f00",
    "Courts MY":         "#1565c0",
    "TMT":               "#2e7d32",
    "MITEC":             "#6a1b9a",
    "ExhibitionsForYou": "#558b2f",
    "10times":           "#c62828",
    "MyCEB":             "#1565c0",
    "KLCC Convention":   "#ad1457",
    "MVEC":              "#00897b",
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


def to_calendar_event(e: dict) -> dict:
    color = SITE_COLORS.get(e.get("source_site", ""), "#546e7a")
    end_raw = e.get("end_date") or e.get("start_date") or ""
    try:
        end_excl = (date.fromisoformat(end_raw) + timedelta(days=1)).isoformat()
    except (ValueError, TypeError):
        end_excl = end_raw

    return {
        "id":    e.get("id", ""),
        "title": e.get("title", ""),
        "start": e.get("start_date", ""),
        "end":   end_excl,
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


async def main():
    started = datetime.now(timezone.utc)
    print(f"[scrape_job] Started at {started.isoformat()} UTC")

    db.init_db()

    # ── scrape ────────────────────────────────────────────────────────────────
    site_results = await run_all_scrapers()

    total_new = total_updated = 0
    fresh_events: list[dict] = []   # collect only events from this run

    for site_name, raw_events in site_results.items():
        if not raw_events:
            print(f"  [{site_name}] 0 raw events")
            continue
        parsed = parse_events(raw_events)
        print(f"  [{site_name}] raw={len(raw_events)} parsed={len(parsed)}")
        for raw in raw_events[:3]:  # debug: show first 3 raw titles/dates
            print(f"    raw: '{raw.title[:60]}' start='{raw.start_date}'")
        n = u = 0
        for event in parsed:
            doc_id, is_new = db.upsert_event(event)
            event["id"] = doc_id
            fresh_events.append(event)
            if is_new:
                n += 1
            else:
                u += 1
        print(f"  [{site_name}] +{n} new, ~{u} updated")
        total_new     += n
        total_updated += u

    print(f"\n[scrape_job] Static scrape done: +{total_new} new, ~{total_updated} updated")

    # ── Playwright scrapers (JS-rendered / Cloudflare-protected sites) ────────
    print("\n[scrape_job] Running Playwright scrapers…")
    pw_results = await run_pw_scrapers()
    for site_name, raw_events in pw_results.items():
        if not raw_events:
            print(f"  [PW] [{site_name}] 0 raw events")
            continue
        parsed = parse_events(raw_events)
        print(f"  [PW] [{site_name}] raw={len(raw_events)} parsed={len(parsed)}")
        for raw in raw_events[:3]:
            print(f"    raw: '{raw.title[:60]}' start='{raw.start_date}'")
        n = u = 0
        for event in parsed:
            doc_id, is_new = db.upsert_event(event)
            event["id"] = doc_id
            fresh_events.append(event)
            if is_new:
                n += 1
            else:
                u += 1
        print(f"  [PW] [{site_name}] +{n} new, ~{u} updated")
        total_new     += n
        total_updated += u

    print(f"\n[scrape_job] All done: +{total_new} new, ~{total_updated} updated")

    # ── export events.json (fresh data only, ignores stale Firestore records) ──
    calendar_events = [to_calendar_event(e) for e in fresh_events]

    DOCS_DIR.mkdir(exist_ok=True)
    meta = {
        "generated_at": started.isoformat().replace("+00:00", "Z"),
        "total":        len(calendar_events),
    }

    output = {"meta": meta, "events": calendar_events}
    events_path = DOCS_DIR / "events.json"
    with open(events_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    print(f"[scrape_job] events.json written — {len(calendar_events)} events total")
    print(f"[scrape_job] Finished at {datetime.now(timezone.utc).isoformat()} UTC")


if __name__ == "__main__":
    asyncio.run(main())
