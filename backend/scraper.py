"""
Web scraper for KL electronics retail & event promotions.

Tier 1 – Exhibition organisers : HOMEDEC KL, HomeLove.my, PIKOM / PC Fair
Tier 2 – Retailers             : Senheng, Harvey Norman MY, Courts MY, Best Denki MY
Tier 3 – Venue calendars       : MITEC, KLCC Conv. Centre, PWTC,
                                  Mid Valley, Sunway Pyramid, Suria KLCC, Starling Mall
Tier 4 – Tech media            : Lowyat.net, SoyaCincau
Tier 5 – Brands (launch events): Samsung MY, LG MY, Panasonic MY
Tier 6 – Online campaigns      : Lazada MY, Shopee MY
"""
import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = httpx.Timeout(30.0)

# Electronics keyword filter (used for venue/media scrapers)
ELEC_KEYWORDS = [
    "tech", "electronic", "gadget", "phone", "smartphone", "tv", "television",
    "audio", "appliance", "computer", "laptop", "camera", "fair", "expo",
    "digital", "home appliance", "pc fair", "consumer electronics",
]


@dataclass
class RawEvent:
    title: str
    organizer: str
    location: str = "Kuala Lumpur"
    venue: str = ""
    start_date: str = ""
    end_date: str = ""
    category: str = "Electronics"
    description: str = ""
    source_url: str = ""
    source_site: str = ""
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_date(text: str) -> str:
    text = _clean(text)
    for fmt in ["%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
                "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _date_range(text: str) -> tuple[str, str]:
    """Parse various date formats including ISO datetime attributes."""
    text = re.sub(r"[–—]", "-", text).strip()

    # ISO datetime / date (e.g. <time datetime="2026-05-06T18:00:00+08:00">)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        d = m.group(1)
        return d, d

    # "D1 – D2 Month YYYY"
    m = re.search(r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if m:
        d1, d2, mon, yr = m.groups()
        return _parse_date(f"{d1} {mon} {yr}"), _parse_date(f"{d2} {mon} {yr}")

    # "D1 Month – D2 Month YYYY"
    m = re.search(
        r"(\d{1,2}\s+[A-Za-z]+\s*\d*)\s*-\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text
    )
    if m:
        s, e = m.group(1).strip(), m.group(2).strip()
        yr_m = re.search(r"\d{4}", e)
        if yr_m and not re.search(r"\d{4}", s):
            s += " " + yr_m.group()
        return _parse_date(s), _parse_date(e)

    # "D Month YYYY" or "Month D, YYYY" — fallback to _parse_date
    m = re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", text)
    if m:
        d = _parse_date(m.group())
        return d, d

    # Final fallback: try _parse_date on the entire text (handles "May 6, 2026" etc.)
    d = _parse_date(text)
    if d and re.match(r"\d{4}-\d{2}-\d{2}", d):
        return d, d

    return "", ""


def _url_date(url: str) -> str:
    """Extract date from WordPress-style URLs: /YYYY/MM/DD/slug/"""
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _card_date(card: BeautifulSoup, link_url: str = "") -> tuple[str, str]:
    """Multi-strategy date extraction from an article card element."""
    # 1. time[datetime] attribute (most reliable for WordPress)
    time_el = card.select_one("time[datetime]")
    if time_el:
        dt = time_el.get("datetime", "")
        if dt:
            return _date_range(dt)

    # 2. Any element whose class contains 'date'
    for el in card.find_all(True):
        cls = " ".join(el.get("class", []))
        if "date" in cls.lower():
            text = _clean(el.get_text())
            if text:
                s, e = _date_range(text)
                if s:
                    return s, e

    # 3. Generic time element text
    time_el = card.select_one("time, .post-date, .entry-date, .published, .updated")
    if time_el:
        text = _clean(time_el.get_text())
        s, e = _date_range(text)
        if s:
            return s, e

    # 4. Extract from URL (e.g. soyacincau.com/2026/05/06/slug/)
    if link_url:
        d = _url_date(link_url)
        if d:
            return d, d

    return "", ""


def _is_electronics_related(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in ELEC_KEYWORDS)


async def _get(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
    return r.text


def _abs_url(href: str, base: str) -> str:
    if href.startswith("http"):
        return href
    from urllib.parse import urljoin
    return urljoin(base, href)


# ===========================================================================
# TIER 1 – Exhibition / Event organisers
# ===========================================================================

async def scrape_homedec(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://homedec.com.my"
    urls = [f"{base}/homedec-kl/", f"{base}/events/"]
    for url in urls:
        try:
            soup = _soup(await _get(client, url))
            # Main featured event banner
            for card in soup.select("article, .event-item, .exhibition-item, section.event, .promo-block"):
                title_el = card.select_one("h1, h2, h3, h4, .title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not title:
                    continue
                date_el = card.select_one(".date, time, .event-date, .period")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                venue_el = card.select_one(".venue, .location, .place")
                venue = _clean(venue_el.get_text()) if venue_el else "HOMEDEC Venue"
                desc_el = card.select_one("p, .desc, .description")
                desc = _clean(desc_el.get_text()) if desc_el else ""
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"], base) if link_el else url
                events.append(RawEvent(
                    title=title, organizer="HOMEDEC",
                    location="Kuala Lumpur", venue=venue,
                    start_date=start, end_date=end,
                    description=desc, source_url=link,
                    source_site="HOMEDEC",
                    tags=["exhibition", "home-appliance", "fair", "kl"],
                ))
            # Also grab any date/venue info directly on the page (for single-event pages)
            if not events:
                h = soup.select_one("h1, h2, .exhibition-title")
                if h:
                    title = _clean(h.get_text())
                    date_text = " ".join(t.get_text() for t in soup.select("time, .date, .period"))
                    start, end = _date_range(date_text)
                    venue_el = soup.select_one(".venue, .location")
                    venue = _clean(venue_el.get_text()) if venue_el else ""
                    events.append(RawEvent(
                        title=title, organizer="HOMEDEC",
                        location="Kuala Lumpur", venue=venue,
                        start_date=start, end_date=end,
                        source_url=url, source_site="HOMEDEC",
                        tags=["exhibition", "home-appliance", "fair", "kl"],
                    ))
        except Exception as exc:
            print(f"[scraper] HOMEDEC error: {exc}")
    return events


async def scrape_homelove(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.homelove.com.my"
    urls = [f"{base}/", f"{base}/promotions", f"{base}/events"]
    for url in urls:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select(".promotion-item, .promo-card, article, .event-card, .deal-item"):
                title_el = card.select_one("h2, h3, h4, .title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not title:
                    continue
                date_el = card.select_one(".date, time, .validity, .period")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                desc_el = card.select_one("p, .desc")
                desc = _clean(desc_el.get_text()) if desc_el else ""
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"], base) if link_el else url
                events.append(RawEvent(
                    title=title, organizer="HomeLove.my",
                    location="Kuala Lumpur / Malaysia",
                    start_date=start, end_date=end,
                    description=desc, source_url=link,
                    source_site="HomeLove MY",
                    tags=["home-appliance", "promotion", "retail"],
                ))
        except Exception as exc:
            print(f"[scraper] HomeLove error: {exc}")
    return events


async def scrape_pikom(client: httpx.AsyncClient) -> list[RawEvent]:
    """PIKOM – organiser of PC Fair (Malaysia's largest consumer electronics fair)."""
    events: list[RawEvent] = []
    base = "https://www.pikom.org.my"
    urls = [f"{base}/events", f"{base}/pc-fair", f"{base}/"]
    for url in urls:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select("article, .event-item, .pc-fair-item, .event-card, li.event"):
                title_el = card.select_one("h2, h3, h4, .title, a")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not title:
                    continue
                date_el = card.select_one(".date, time, .period, .event-date")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                venue_el = card.select_one(".venue, .location")
                venue = _clean(venue_el.get_text()) if venue_el else ""
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"], base) if link_el else url
                events.append(RawEvent(
                    title=title, organizer="PIKOM",
                    location="Kuala Lumpur", venue=venue,
                    start_date=start, end_date=end,
                    source_url=link, source_site="PIKOM",
                    tags=["pc-fair", "exhibition", "electronics", "kl"],
                ))
        except Exception as exc:
            print(f"[scraper] PIKOM error: {exc}")
    return events


# ===========================================================================
# TIER 2 – Retailers
# ===========================================================================

async def scrape_senheng(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.senheng.com.my"
    for url in [f"{base}/promotion", f"{base}/events"]:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select(".promotion-item, .promo-card, article.promotion, .event-item"):
                title_el = card.select_one("h2, h3, h4, .title, .promo-title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                date_el = card.select_one(".date, .promo-date, .period, time")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                desc_el = card.select_one("p, .desc, .description")
                desc = _clean(desc_el.get_text()) if desc_el else ""
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                events.append(RawEvent(
                    title=title, organizer="Senheng",
                    location="Kuala Lumpur / Malaysia",
                    start_date=start, end_date=end,
                    description=desc, source_url=link,
                    source_site="Senheng",
                    tags=["electronics", "retail", "promotion"],
                ))
        except Exception:
            pass
    return events


async def scrape_harvey_norman(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.harveynorman.com.my"
    try:
        soup = _soup(await _get(client, f"{base}/promotions"))
        for card in soup.select(".promotion-item, .promo-block, article, .promo-card"):
            title_el = card.select_one("h1, h2, h3, .title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            date_el = card.select_one(".date, .validity, .period, time")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/promotions", base)
            events.append(RawEvent(
                title=title, organizer="Harvey Norman Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link, source_site="Harvey Norman MY",
                tags=["electronics", "retail", "promotion"],
            ))
    except Exception:
        pass
    return events


async def scrape_courts(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.courts.com.my"
    try:
        soup = _soup(await _get(client, f"{base}/promotions"))
        for card in soup.select(".promotion-item, .promo-card, .banner-item, article"):
            title_el = card.select_one("h2, h3, h4, .title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            date_el = card.select_one(".date, .period, .validity, time")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/promotions", base)
            events.append(RawEvent(
                title=title, organizer="Courts Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link, source_site="Courts MY",
                tags=["electronics", "retail", "promotion"],
            ))
    except Exception:
        pass
    return events


async def scrape_best_denki(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.bestdenki.com.my"
    try:
        soup = _soup(await _get(client, f"{base}/promotions/"))
        for card in soup.select(".promotion, .promo-item, article.post, .entry"):
            title_el = card.select_one("h2, h3, .entry-title, .title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            date_el = card.select_one(".date, time, .entry-date, .period")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/promotions/", base)
            events.append(RawEvent(
                title=title, organizer="Best Denki Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link, source_site="Best Denki MY",
                tags=["electronics", "retail", "promotion"],
            ))
    except Exception:
        pass
    return events


# ===========================================================================
# TIER 3 – Venue calendars
# ===========================================================================

async def scrape_mitec(client: httpx.AsyncClient) -> list[RawEvent]:
    base = "https://www.mitec.com.my"
    events: list[RawEvent] = []
    for url in [f"{base}/events", f"{base}/whats-on"]:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select("article, .event-item, .event-card, li.event"):
                title_el = card.select_one("h2, h3, h4, .title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not _is_electronics_related(title):
                    continue
                date_el = card.select_one(".date, time, .period")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                events.append(RawEvent(
                    title=title, organizer="MITEC",
                    location="Kuala Lumpur", venue="Malaysia International Trade & Exhibition Centre",
                    start_date=start, end_date=end,
                    source_url=link, source_site="MITEC",
                    tags=["venue", "exhibition", "kl"],
                ))
        except Exception:
            pass
    return events


async def scrape_klcc_convention(client: httpx.AsyncClient) -> list[RawEvent]:
    base = "https://www.klccconventioncentre.com"
    events: list[RawEvent] = []
    for url in [f"{base}/events", f"{base}/whats-on"]:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select("article, .event-item, .event-card"):
                title_el = card.select_one("h2, h3, h4, .title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not _is_electronics_related(title):
                    continue
                date_el = card.select_one(".date, time, .period")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                events.append(RawEvent(
                    title=title, organizer="KLCC Convention Centre",
                    location="Kuala Lumpur City Centre", venue="KLCC Convention Centre",
                    start_date=start, end_date=end,
                    source_url=link, source_site="KLCC Convention",
                    tags=["venue", "exhibition", "kl"],
                ))
        except Exception:
            pass
    return events


async def scrape_pwtc(client: httpx.AsyncClient) -> list[RawEvent]:
    base = "https://www.pwtc.com"
    events: list[RawEvent] = []
    for url in [f"{base}/events", f"{base}/whats-on", f"{base}/"]:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select("article, .event-item, .event-card, .events li"):
                title_el = card.select_one("h2, h3, h4, .title, a")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not _is_electronics_related(title):
                    continue
                date_el = card.select_one(".date, time, .period")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                events.append(RawEvent(
                    title=title, organizer="PWTC",
                    location="Kuala Lumpur", venue="Putra World Trade Centre",
                    start_date=start, end_date=end,
                    source_url=link, source_site="PWTC",
                    tags=["venue", "exhibition", "kl"],
                ))
        except Exception:
            pass
    return events


async def scrape_midvalley(client: httpx.AsyncClient) -> list[RawEvent]:
    base = "https://www.midvalley.com.my"
    events: list[RawEvent] = []
    try:
        soup = _soup(await _get(client, f"{base}/events"))
        for card in soup.select(".event-item, article.event, .events-list li, .event-card"):
            title_el = card.select_one("h2, h3, h4, .title, .event-title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            if not _is_electronics_related(title):
                continue
            date_el = card.select_one(".date, time, .event-date, .period")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/events", base)
            events.append(RawEvent(
                title=title, organizer="Mid Valley Megamall",
                location="Kuala Lumpur", venue="Mid Valley Megamall",
                start_date=start, end_date=end,
                source_url=link, source_site="Mid Valley",
                tags=["mall", "event", "kl"],
            ))
    except Exception:
        pass
    return events


async def scrape_sunway(client: httpx.AsyncClient) -> list[RawEvent]:
    base = "https://www.sunwaypyramid.com"
    events: list[RawEvent] = []
    try:
        soup = _soup(await _get(client, f"{base}/happenings"))
        for card in soup.select(".event-item, article.event, .happening-card, .event-card"):
            title_el = card.select_one("h2, h3, h4, .title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            if not _is_electronics_related(title):
                continue
            date_el = card.select_one(".date, time, .period")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/happenings", base)
            events.append(RawEvent(
                title=title, organizer="Sunway Pyramid",
                location="Petaling Jaya / KL", venue="Sunway Pyramid",
                start_date=start, end_date=end,
                source_url=link, source_site="Sunway Pyramid",
                tags=["mall", "event", "kl"],
            ))
    except Exception:
        pass
    return events


async def scrape_klcc_suria(client: httpx.AsyncClient) -> list[RawEvent]:
    base = "https://www.suriaklcc.com.my"
    events: list[RawEvent] = []
    try:
        soup = _soup(await _get(client, f"{base}/happenings/events"))
        for card in soup.select(".event-item, article.event, .events-card"):
            title_el = card.select_one("h2, h3, h4, .title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            if not _is_electronics_related(title):
                continue
            date_el = card.select_one(".date, time, .period")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/happenings/events", base)
            events.append(RawEvent(
                title=title, organizer="Suria KLCC",
                location="Kuala Lumpur City Centre", venue="Suria KLCC",
                start_date=start, end_date=end,
                source_url=link, source_site="Suria KLCC",
                tags=["mall", "event", "kl"],
            ))
    except Exception:
        pass
    return events


async def scrape_starling(client: httpx.AsyncClient) -> list[RawEvent]:
    """The Starling Mall – Damansara Uptown, PJ (Greater KL)."""
    base = "https://thestarling.com.my"
    events: list[RawEvent] = []
    for url in [f"{base}/whats-on", f"{base}/events", f"{base}/promotions"]:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select("article, .event-item, .event-card, .promo-card, .happening-item"):
                title_el = card.select_one("h2, h3, h4, .title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not _is_electronics_related(title):
                    continue
                date_el = card.select_one(".date, time, .period, .event-date")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                events.append(RawEvent(
                    title=title, organizer="The Starling Mall",
                    location="Petaling Jaya / KL", venue="The Starling Mall",
                    start_date=start, end_date=end,
                    source_url=link, source_site="Starling Mall",
                    tags=["mall", "event", "kl"],
                ))
        except Exception:
            pass
    return events


# ===========================================================================
# TIER 4 – Tech media (pre-filtered for electronics events)
# ===========================================================================

async def scrape_lowyat(client: httpx.AsyncClient) -> list[RawEvent]:
    """Lowyat.net – Malaysia's largest tech forum; scrape deal/event articles."""
    base = "https://www.lowyat.net"
    events: list[RawEvent] = []
    search_urls = [
        f"{base}/category/deals/",
        f"{base}/category/news/",
        f"{base}/?s=pc+fair+malaysia",
        f"{base}/?s=electronics+expo+malaysia",
    ]
    seen: set[str] = set()
    for url in search_urls:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select("article, .post, .article-item, .td-module-container"):
                title_el = card.select_one("h2, h3, .entry-title, .post-title, .td-module-title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not _is_electronics_related(title):
                    continue
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                if link in seen:
                    continue
                seen.add(link)
                start, end = _card_date(card, link)
                desc_el = card.select_one(".entry-summary, .excerpt, .td-excerpt, p")
                desc = _clean(desc_el.get_text()) if desc_el else ""
                events.append(RawEvent(
                    title=title, organizer="(via Lowyat.net)",
                    location="Kuala Lumpur",
                    start_date=start, end_date=end,
                    description=desc[:300],
                    source_url=link, source_site="Lowyat.net",
                    tags=["media", "tech", "deal"],
                ))
        except Exception:
            pass
    return events


async def scrape_soyacincau(client: httpx.AsyncClient) -> list[RawEvent]:
    """SoyaCincau – Malaysian tech/digital lifestyle news."""
    base = "https://soyacincau.com"
    events: list[RawEvent] = []
    search_urls = [
        f"{base}/category/events/",
        f"{base}/category/deals/",
        f"{base}/?s=fair+malaysia",
        f"{base}/?s=electronics+expo",
    ]
    seen: set[str] = set()
    for url in search_urls:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select("article, .post, .article-card, .jeg_post"):
                title_el = card.select_one("h2, h3, .entry-title, .jeg_post_title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not _is_electronics_related(title):
                    continue
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                if link in seen:
                    continue
                seen.add(link)
                start, end = _card_date(card, link)
                desc_el = card.select_one(".entry-summary, .excerpt, .jeg_post_excerpt, p")
                desc = _clean(desc_el.get_text()) if desc_el else ""
                events.append(RawEvent(
                    title=title, organizer="(via SoyaCincau)",
                    location="Kuala Lumpur",
                    start_date=start, end_date=end,
                    description=desc[:300],
                    source_url=link, source_site="SoyaCincau",
                    tags=["media", "tech", "deal"],
                ))
        except Exception:
            pass
    return events


# ===========================================================================
# TIER 5 – Brand sites (product launch events)
# ===========================================================================

async def scrape_samsung_my(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.samsung.com"
    try:
        soup = _soup(await _get(client, f"{base}/my/offer/"))
        for card in soup.select(".offer-item, .promo-item, .event-card, article, .product-offer"):
            title_el = card.select_one("h2, h3, h4, .title, .offer-title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            date_el = card.select_one(".date, .validity, time, .period")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/my/offer/", base)
            events.append(RawEvent(
                title=title, organizer="Samsung Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link, source_site="Samsung MY",
                tags=["samsung", "brand-event", "electronics"],
            ))
    except Exception:
        pass
    return events


async def scrape_lg_my(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.lg.com"
    try:
        soup = _soup(await _get(client, f"{base}/my/promotions"))
        for card in soup.select(".promotion-list li, .promo-item, article, .event-item"):
            title_el = card.select_one("h2, h3, h4, .title, strong")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            date_el = card.select_one(".date, time, .validity, .period")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/my/promotions", base)
            events.append(RawEvent(
                title=title, organizer="LG Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link, source_site="LG MY",
                tags=["lg", "brand-event", "electronics"],
            ))
    except Exception:
        pass
    return events


async def scrape_panasonic_my(client: httpx.AsyncClient) -> list[RawEvent]:
    events: list[RawEvent] = []
    base = "https://www.panasonic.com"
    try:
        soup = _soup(await _get(client, f"{base}/my/consumer/promotions.html"))
        for card in soup.select(".promotion-item, .promo-card, article, .item"):
            title_el = card.select_one("h2, h3, h4, .title")
            if not title_el:
                continue
            title = _clean(title_el.get_text())
            date_el = card.select_one(".date, time, .period, .validity")
            start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
            link_el = card.select_one("a[href]")
            link = _abs_url(link_el["href"] if link_el else "/my/consumer/promotions.html", base)
            events.append(RawEvent(
                title=title, organizer="Panasonic Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link, source_site="Panasonic MY",
                tags=["panasonic", "brand-event", "electronics"],
            ))
    except Exception:
        pass
    return events


# ===========================================================================
# TIER 6 – Online campaigns
# ===========================================================================

async def scrape_lazada(client: httpx.AsyncClient) -> list[RawEvent]:
    """Lazada Malaysia – campaign/sale landing pages."""
    events: list[RawEvent] = []
    base = "https://www.lazada.com.my"
    urls = [f"{base}/campaigns/", f"{base}/shop/campaigns/"]
    for url in urls:
        try:
            soup = _soup(await _get(client, url))
            for card in soup.select(".campaign-item, .sale-banner, article, .promo-item"):
                title_el = card.select_one("h2, h3, h4, .title, .name")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not _is_electronics_related(title):
                    continue
                date_el = card.select_one(".date, time, .period, .validity")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"] if link_el else url, base)
                events.append(RawEvent(
                    title=title, organizer="Lazada Malaysia",
                    location="Online / Malaysia",
                    category="Online",
                    start_date=start, end_date=end,
                    source_url=link, source_site="Lazada MY",
                    tags=["online", "campaign", "sale"],
                ))
        except Exception:
            pass
    return events


async def scrape_shopee(client: httpx.AsyncClient) -> list[RawEvent]:
    """
    Shopee Malaysia – campaign pages.
    Note: Shopee is a JS-heavy SPA; static scraping captures limited data.
    We fall back to their sitemap/campaign URLs for high-signal pages.
    """
    events: list[RawEvent] = []
    base = "https://shopee.com.my"
    campaign_urls = [
        f"{base}/m/shopee-sale",
        f"{base}/m/brand-sale",
        f"{base}/m/electronics-sale",
        f"{base}/m/tech-sale",
    ]
    for url in campaign_urls:
        try:
            soup = _soup(await _get(client, url))
            # Try to grab any campaign title / period visible in static HTML
            for card in soup.select(
                ".campaign-banner, .shopee-banner, [class*='campaign'], [class*='sale-'], article"
            ):
                title_el = card.select_one("h1, h2, h3, [class*='title'], [class*='heading']")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not title or len(title) < 4:
                    continue
                if not _is_electronics_related(title):
                    continue
                date_el = card.select_one("[class*='date'], [class*='period'], time")
                start, end = _date_range(_clean(date_el.get_text()) if date_el else "")
                events.append(RawEvent(
                    title=title, organizer="Shopee Malaysia",
                    location="Online / Malaysia",
                    category="Online",
                    start_date=start, end_date=end,
                    source_url=url, source_site="Shopee MY",
                    tags=["online", "shopee", "sale", "electronics"],
                ))
        except Exception:
            pass
    return events


# ===========================================================================
# Registry & runner
# ===========================================================================

SCRAPERS: dict[str, tuple[str, callable]] = {
    # label → (tier_label, coroutine_fn)
    "HOMEDEC":              ("Tier1-Exhibition", scrape_homedec),
    "HomeLove MY":          ("Tier1-Exhibition", scrape_homelove),
    "PIKOM":                ("Tier1-Exhibition", scrape_pikom),
    "Senheng":              ("Tier2-Retail",     scrape_senheng),
    "Harvey Norman MY":     ("Tier2-Retail",     scrape_harvey_norman),
    "Courts MY":            ("Tier2-Retail",     scrape_courts),
    "Best Denki MY":        ("Tier2-Retail",     scrape_best_denki),
    "MITEC":                ("Tier3-Venue",      scrape_mitec),
    "KLCC Convention":      ("Tier3-Venue",      scrape_klcc_convention),
    "PWTC":                 ("Tier3-Venue",      scrape_pwtc),
    "Mid Valley":           ("Tier3-Venue",      scrape_midvalley),
    "Sunway Pyramid":       ("Tier3-Venue",      scrape_sunway),
    "Suria KLCC":           ("Tier3-Venue",      scrape_klcc_suria),
    "Starling Mall":        ("Tier3-Venue",      scrape_starling),
    "Lowyat.net":           ("Tier4-Media",      scrape_lowyat),
    "SoyaCincau":           ("Tier4-Media",      scrape_soyacincau),
    "Samsung MY":           ("Tier5-Brand",      scrape_samsung_my),
    "LG MY":                ("Tier5-Brand",      scrape_lg_my),
    "Panasonic MY":         ("Tier5-Brand",      scrape_panasonic_my),
    "Lazada MY":            ("Tier6-Online",     scrape_lazada),
    "Shopee MY":            ("Tier6-Online",     scrape_shopee),
}


async def run_all_scrapers() -> dict[str, list[RawEvent]]:
    async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
        tasks = {name: asyncio.create_task(fn(client)) for name, (_, fn) in SCRAPERS.items()}
        results: dict[str, list[RawEvent]] = {}
        for name, task in tasks.items():
            try:
                results[name] = await task
            except Exception as exc:
                print(f"[scraper] {name} failed: {exc}")
                results[name] = []
    return results


async def run_single_scraper(site_name: str) -> list[RawEvent]:
    entry = SCRAPERS.get(site_name)
    if not entry:
        raise ValueError(f"Unknown site: {site_name}")
    _, fn = entry
    async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
        return await fn(client)


def available_sites() -> list[dict]:
    return [{"name": name, "tier": tier} for name, (tier, _) in SCRAPERS.items()]
