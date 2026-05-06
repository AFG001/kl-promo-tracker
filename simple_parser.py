"""
Rule-based event parser — no AI required.
Normalises raw scraped RawEvent objects into structured dicts.
"""
from datetime import datetime
from scraper import RawEvent

SITE_CATEGORY = {
    "HOMEDEC":           "Mall Event",
    "HomeLove MY":       "Electronics",
    "PIKOM":             "Mall Event",
    "Senheng":           "Electronics",
    "Harvey Norman MY":  "Electronics",
    "Courts MY":         "Electronics",
    "Best Denki MY":     "Electronics",
    "MITEC":             "Mall Event",
    "KLCC Convention":   "Mall Event",
    "PWTC":              "Mall Event",
    "Mid Valley":        "Mall Event",
    "Sunway Pyramid":    "Mall Event",
    "Suria KLCC":        "Mall Event",
    "Starling Mall":     "Mall Event",
    "Lowyat.net":        "Electronics",
    "SoyaCincau":        "Electronics",
    "Samsung MY":        "Brand Event",
    "LG MY":             "Brand Event",
    "Panasonic MY":      "Brand Event",
    "Lazada MY":         "Online",
    "Shopee MY":         "Online",
}


def parse_events(raw_events: list[RawEvent]) -> list[dict]:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    seen_titles: set[str] = set()
    results: list[dict] = []

    for e in raw_events:
        title = (e.title or "").strip()
        if not title or len(title) < 4:
            continue

        # Deduplicate within the same batch
        dedup_key = f"{title.lower()}|{e.start_date}|{e.source_site}"
        if dedup_key in seen_titles:
            continue
        seen_titles.add(dedup_key)

        start = e.start_date or today
        end   = e.end_date or start

        # One-line summary without AI
        parts = [title]
        if e.organizer and e.organizer.lower() not in title.lower():
            parts.append(f"by {e.organizer}")
        if e.venue:
            parts.append(f"at {e.venue}")
        summary = " ".join(parts)[:120]

        results.append({
            "title":       title[:200],
            "organizer":   e.organizer or "",
            "location":    e.location  or "Kuala Lumpur",
            "venue":       e.venue     or "",
            "start_date":  start,
            "end_date":    end,
            "category":    SITE_CATEGORY.get(e.source_site, "Electronics"),
            "description": (e.description or "")[:500],
            "summary":     summary,
            "tags":        e.tags or [],
            "source_url":  e.source_url  or "",
            "source_site": e.source_site or "",
        })

    return results
