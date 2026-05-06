"""
Rule-based event parser — no AI required.
Normalises raw scraped RawEvent objects into structured dicts.
"""
from datetime import date, datetime, timedelta
from scraper import RawEvent

# ---------------------------------------------------------------------------
# Site → Category mapping
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Noise filter — exact lowercase matches that are NOT events
# ---------------------------------------------------------------------------
NOISE_TITLES_EXACT = {
    "contact us", "about us", "about", "home", "search", "events",
    "promotions", "promotion", "news", "login", "register", "sign in",
    "sign up", "newsletter", "faq", "sitemap", "careers", "jobs",
    "privacy policy", "terms", "terms of use", "academy", "membership",
    "boardroom rental", "featured event", "my.it magazine",
    "pikom membership", "pikom academy", "ict job market outlook",
    "ict strategic review", "whats on", "what's on", "happenings",
    "read more", "learn more", "see more", "view all", "more info",
    "click here", "find out more",
}

NOISE_TITLE_PREFIXES = (
    "page ", "category:", "tag:", "archive:", "author:",
)

MIN_TITLE_LEN = 10   # characters

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
_TODAY          = date.today()
_DATE_MIN       = _TODAY - timedelta(days=14)   # ignore events ended >14 days ago
_DATE_MIN_MEDIA = _TODAY - timedelta(days=30)   # media articles: allow slightly older
_DATE_MAX       = _TODAY + timedelta(days=730)  # ignore events >2 years away


def _valid_date(date_str: str, media: bool = False) -> bool:
    """Return True if date_str is parseable and within the acceptable window."""
    if not date_str:
        return False
    try:
        d = date.fromisoformat(date_str)
        min_d = _DATE_MIN_MEDIA if media else _DATE_MIN
        return min_d <= d <= _DATE_MAX
    except ValueError:
        return False


def _is_noise(title: str) -> bool:
    """Return True if the title looks like a navigation item, not an event."""
    t = title.strip()
    if len(t) < MIN_TITLE_LEN:
        return True
    tl = t.lower()
    if tl in NOISE_TITLES_EXACT:
        return True
    if any(tl.startswith(p) for p in NOISE_TITLE_PREFIXES):
        return True
    # All-caps short strings are usually menu labels (e.g. "CONTACT US")
    if t.isupper() and len(t) < 40:
        return True
    return False


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_events(raw_events: list[RawEvent]) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []

    for e in raw_events:
        title = (e.title or "").strip()

        # ── noise filter ──────────────────────────────────────────────────
        if _is_noise(title):
            continue

        # ── date validation ───────────────────────────────────────────────
        start = e.start_date or ""
        end   = e.end_date   or start

        # For media sites (article publish dates), only keep recent articles
        if e.source_site in ("Lowyat.net", "SoyaCincau"):
            if not _valid_date(start, media=True):
                continue   # skip articles with no date or old dates
        else:
            # For retail / event sites: skip if no date at all
            # (avoids flooding calendar with today's date)
            if not start:
                continue
            # If date is out of range, skip
            if start and not _valid_date(start):
                continue

        # ── deduplication ─────────────────────────────────────────────────
        key = f"{title.lower()}|{start}|{e.source_site}"
        if key in seen:
            continue
        seen.add(key)

        # ── summary ───────────────────────────────────────────────────────
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
