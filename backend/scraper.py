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
import html as html_module
import json
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
TIMEOUT = httpx.Timeout(30.0)

# Electronics keyword filter (used for venue/media scrapers)
ELEC_KEYWORDS = [
    # Consumer electronics & devices
    "tech", "electronic", "gadget", "phone", "smartphone", "tv", "television",
    "audio", "appliance", "home appliance", "electrical appliance",
    "computer", "laptop", "tablet", "camera",
    # IT & Digital industry
    "digital", "ict", "it fair", "pc fair", "consumer electronics",
    "semiconductor", "semicon", "data center", "cloud",
    "smart home", "smart device", "iot", "internet of things",
    # Industrial / manufacturing tech (venue events)
    "automation", "robotics", "manufacturing", "machinery",
    "automechanika", "engineer", "marvex",
    # Energy tech
    "electric vehicle", "ev charging", "enertec", "energy tech",
    # Gaming & entertainment tech
    "gaming", "esports",
    # Finance tech / digital economy
    "fintech", "digital economy",
    # Known Malaysia tech events
    "itex", "mitec",
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

    # "D1 – D2 Month YYYY"  (also handles "D1 - D2 Month, YYYY" with comma)
    m = re.search(r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", text)
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


_EVENT_TYPES = {
    "Event", "ExhibitionEvent", "BusinessEvent", "SaleEvent",
    "Festival", "SportsEvent", "MusicEvent", "EducationEvent",
}


def _jsonld_events(
    soup: BeautifulSoup,
    base_url: str,
    organizer: str,
    source_site: str,
    location: str,
    venue: str,
    tags: list[str],
) -> list[RawEvent]:
    """Extract events from JSON-LD structured data (schema.org Event)."""
    events: list[RawEvent] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        items: list = []
        if isinstance(data, dict):
            items = data.get("@graph", []) or [data]
        elif isinstance(data, list):
            items = data
        for item in items:
            t = item.get("@type", "")
            if isinstance(t, list):
                t = t[0]
            if t not in _EVENT_TYPES:
                continue
            name = item.get("name", "").strip()
            if not name or len(name) < 6:
                continue
            raw_start = item.get("startDate", "")[:10]
            raw_end   = item.get("endDate",   raw_start)[:10]
            start = _parse_date(raw_start)
            end   = _parse_date(raw_end) or start
            loc_data = item.get("location", {})
            loc_name = ""
            if isinstance(loc_data, dict):
                loc_name = (
                    loc_data.get("name", "")
                    or loc_data.get("address", {}).get("addressLocality", "")
                )
            events.append(RawEvent(
                title=name, organizer=organizer,
                location=loc_name or location,
                venue=venue,
                start_date=start, end_date=end,
                source_url=item.get("url", base_url),
                source_site=source_site,
                tags=tags,
            ))
    return events


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
    urls = [f"{base}/homedec-kl/", f"{base}/events/", f"{base}/"]
    for url in urls:
        try:
            soup = _soup(await _get(client, url))

            # 1. JSON-LD structured data (most reliable)
            jld = _jsonld_events(soup, url, "HOMEDEC", "HOMEDEC",
                                 "Kuala Lumpur", "", ["exhibition", "home-appliance", "fair", "kl"])
            events.extend(jld)

            # 2. Card-level extraction
            for card in soup.select("article, .event-item, .exhibition-item, section.event, .promo-block"):
                title_el = card.select_one("h1, h2, h3, h4, .title")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not title:
                    continue
                link_el = card.select_one("a[href]")
                link = _abs_url(link_el["href"], base) if link_el else url
                start, end = _card_date(card, link)
                if not start:
                    continue
                venue_el = card.select_one(".venue, .location, .place")
                venue = _clean(venue_el.get_text()) if venue_el else ""
                desc_el = card.select_one("p, .desc, .description")
                desc = _clean(desc_el.get_text()) if desc_el else ""
                events.append(RawEvent(
                    title=title, organizer="HOMEDEC",
                    location="Kuala Lumpur", venue=venue,
                    start_date=start, end_date=end,
                    description=desc, source_url=link,
                    source_site="HOMEDEC",
                    tags=["exhibition", "home-appliance", "fair", "kl"],
                ))

            # 3. Page-level fallback: look for dates in body text near heading
            if not events:
                h = soup.select_one("h1, h2, .exhibition-title")
                if h:
                    title = _clean(h.get_text())
                    # Search nearby text for date pattern
                    page_text = " ".join(_clean(el.get_text()) for el in soup.select("p, span, li, .date, time"))
                    start, end = _date_range(page_text[:1000])
                    if start:
                        events.append(RawEvent(
                            title=title, organizer="HOMEDEC",
                            location="Kuala Lumpur", venue="",
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
    """
    Harvey Norman MY promotions.
    Dates are embedded in <img alt="Title (DD Mon - DD Mon YYYY) - Promo Banner"> attributes.
    This is in static HTML — no JS rendering needed.
    """
    events: list[RawEvent] = []
    base = "https://www.harveynorman.com.my"
    url  = f"{base}/promotions/catalogues-and-promotions.html"
    try:
        html = await _get(client, url)
        # Debug: show response size and first tag to detect bot-block pages
        preview = html[:200].replace("\n", " ")
        print(f"    [HN debug] html_len={len(html)} preview={preview!r}")
        soup = _soup(html)
        all_imgs = soup.select("img[alt]")
        print(f"    [HN debug] img[alt] count={len(all_imgs)}")
        seen: set[str] = set()

        for img in all_imgs:
            alt = img.get("alt", "")
            # Pattern: "Promo Title (DD Mon - DD Mon YYYY)" or "(DD Mon YYYY)"
            m = re.search(r"^(.+?)\s*\((\d{1,2}\s+\w+[^)]+\d{4})\)", alt)
            if not m:
                continue
            title    = _clean(m.group(1))
            date_str = m.group(2).strip()
            if not title or len(title) < 5:
                continue
            start, end = _date_range(date_str)
            if not start:
                continue
            if title in seen:
                continue
            seen.add(title)

            parent_a = img.find_parent("a")
            link = _abs_url(parent_a["href"], base) if parent_a and parent_a.get("href") else url

            events.append(RawEvent(
                title=title,
                organizer="Harvey Norman Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link, source_site="Harvey Norman MY",
                tags=["electronics", "retail", "promotion"],
            ))
    except Exception as exc:
        print(f"[scraper] Harvey Norman error: {exc}")
    return events


async def scrape_courts(client: httpx.AsyncClient) -> list[RawEvent]:
    """
    Courts MY – /latest-promo-and-catalogue
    Promo banners are Magento-dynamic; only img[alt] with non-generic text
    is available in static HTML.  Dates are extracted from alt text or
    nearby headings where possible.
    """
    base = "https://www.courts.com.my"
    url  = f"{base}/latest-promo-and-catalogue"
    events: list[RawEvent] = []
    try:
        soup = _soup(await _get(client, url))
        seen: set[str] = set()
        for img in soup.find_all("img", src=re.compile(r"wysiwyg", re.I)):
            alt = _clean(img.get("alt", ""))
            src = img.get("src", "")
            # Skip generic product-category labels ("X Image", single words, etc.)
            if (not alt or "Image" in alt or len(alt) < 8
                    or alt.lower() in {"store locator", "payment method",
                                       "lowest price guaranteed",
                                       "30 days free returns",
                                       "product protection plans up to 10 years"}):
                continue
            if alt in seen:
                continue
            seen.add(alt)

            parent_a = img.find_parent("a")
            link = parent_a["href"] if parent_a and parent_a.get("href") else url

            # Try to extract a date from the alt text itself
            start, end = _date_range(alt)
            # Also look for dates in nearby text within the same container
            if not start:
                container = img.parent
                for _ in range(4):
                    if not container:
                        break
                    txt = container.get_text()
                    s, e = _date_range(txt)
                    if s:
                        start, end = s, e
                        break
                    container = getattr(container, "parent", None)

            events.append(RawEvent(
                title=alt,
                organizer="Courts Malaysia",
                location="Kuala Lumpur / Malaysia",
                start_date=start, end_date=end,
                source_url=link,
                source_site="Courts MY",
                tags=["electronics", "retail", "promotion"],
            ))
    except Exception as exc:
        print(f"[Courts] error: {exc}")
    return events


async def scrape_exhibitionsforyou(client: httpx.AsyncClient) -> list[RawEvent]:
    """
    exhibitionsforyou.com – Electric & Electronics category,
    filtered for Malaysia / Kuala Lumpur events.
    """
    base_url = "https://exhibitionsforyou.com/event_category/electric-electronics/"
    events: list[RawEvent] = []
    seen: set[str] = set()
    MYS = re.compile(r"Malaysia|Kuala Lumpur\b|KL\b", re.I)

    for page in range(1, 8):   # up to 7 pages
        url = base_url if page == 1 else f"{base_url}page/{page}/"
        try:
            soup = _soup(await _get(client, url))
        except Exception as exc:
            print(f"[ExhibitionsForYou] page {page} error: {exc}")
            break

        boxes = soup.find_all(class_="event-box")
        if not boxes:
            break

        found_any = False
        for box in boxes:
            box_text = box.get_text()
            if not MYS.search(box_text):
                continue            # skip non-Malaysia events

            title_el = box.find("h2") or box.find("h3")
            date_el  = box.find("h4")
            if not title_el:
                continue

            title = _clean(html_module.unescape(title_el.get_text()))
            if not title or title in seen:
                continue
            seen.add(title)
            found_any = True

            date_raw    = _clean(date_el.get_text()) if date_el else ""
            # date_el often: "05 - 07 May 2026"
            start, end  = _date_range(date_raw)

            link_el = title_el.find("a") or box.find("a", href=True)
            link    = link_el["href"] if link_el and link_el.get("href") else base_url

            events.append(RawEvent(
                title=title,
                organizer="",
                location="Kuala Lumpur",
                venue="",
                start_date=start, end_date=end,
                source_url=link,
                source_site="ExhibitionsForYou",
                tags=["exhibition", "kl", "malaysia"],
            ))

        if not found_any and page > 1:
            break   # stop if a page has no Malaysia events

    return events


async def scrape_tmt(client: httpx.AsyncClient) -> list[RawEvent]:
    """
    TMT (Thunder Match) – Malaysian consumer electronics retailer.
    Tries Shopify collection JSON API for current sale items.
    The site is JS-rendered so HTML scraping yields nothing useful;
    the Shopify products.json endpoint is the only viable path.
    """
    base = "https://www.tmt.my"
    events: list[RawEvent] = []
    # Shopify exposes /collections/<handle>/products.json
    for coll in ["sale", "monthly-sales", "promotions", "deals"]:
        url = f"{base}/collections/{coll}/products.json?limit=20"
        try:
            r = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            data   = r.json()
            prods  = data.get("products", [])
            if not prods:
                continue
            # Use earliest available_on or created_at as start date proxy
            for p in prods[:20]:
                title = _clean(p.get("title", ""))
                if not title or not _is_electronics_related(title):
                    continue
                handle = p.get("handle", "")
                link   = f"{base}/products/{handle}" if handle else base
                # Shopify product dates are not campaign dates; skip date
                events.append(RawEvent(
                    title=title,
                    organizer="Thunder Match (TMT)",
                    location="Kuala Lumpur / Malaysia",
                    start_date="", end_date="",
                    source_url=link,
                    source_site="TMT",
                    tags=["electronics", "retail", "online"],
                ))
            if events:
                break   # stop after first successful collection
        except Exception:
            continue
    return events


# ===========================================================================
# TIER 3 – Venue calendars
# ===========================================================================

_DATE_TEXT_RE = re.compile(
    r"\d{1,2}\s*[-–]\s*\d{1,2}\s+[A-Za-z]+\s+\d{4}"   # "5 - 7 May 2026"
    r"|"
    r"\d{1,2}\s+[A-Za-z]+\s*[-–]\s*\d{1,2}\s+[A-Za-z]+\s+\d{4}"  # "30 Apr - 3 May 2026"
)


async def scrape_mitec(client: httpx.AsyncClient) -> list[RawEvent]:
    """
    MITEC homepage lists upcoming events with date text like '5 - 7 May 2026'.
    Strategy: find all date-pattern text nodes, then look for the nearest heading.
    """
    base = "https://www.mitec.com.my"
    events: list[RawEvent] = []
    for url in [f"{base}/", f"{base}/events/"]:
        try:
            html = await _get(client, url)
            print(f"    [MITEC debug] url={url} html_len={len(html)}")
            soup = _soup(html)
            nodes = soup.find_all(string=_DATE_TEXT_RE)
            print(f"    [MITEC debug] date_text_nodes={len(nodes)} examples={[str(n)[:40] for n in nodes[:3]]}")

            # JSON-LD first
            jld = _jsonld_events(soup, url, "MITEC", "MITEC",
                                 "Kuala Lumpur",
                                 "Malaysia International Trade & Exhibition Centre",
                                 ["venue", "exhibition", "kl"])
            events.extend(jld)

            # Text-node date search: walk UP the DOM to find the card container,
            # then look for the event title WITHIN that container.
            seen: set[str] = set()
            for text_node in soup.find_all(string=_DATE_TEXT_RE):
                date_text = _clean(str(text_node))
                start, end = _date_range(date_text)
                if not start:
                    continue

                # Walk up from the text node's parent until we find a heading
                node = text_node.parent
                title = ""
                for _ in range(7):  # max 7 levels up
                    h = node.select_one("h1, h2, h3, h4, h5, .event-title, .title")
                    if h:
                        t = _clean(h.get_text())
                        if len(t) > 5 and t != date_text:
                            title = t
                            break
                    parent_tag = getattr(node, "parent", None)
                    if not parent_tag or parent_tag.name in ("html", "body", "[document]"):
                        break
                    node = parent_tag

                if not title or len(title) < 5:
                    continue
                # For MITEC venue, keep all events (broad keyword match)
                if not _is_electronics_related(title):
                    continue
                key = f"{title}|{start}"
                if key in seen:
                    continue
                seen.add(key)
                link_node = node.select_one("a[href]") or text_node.parent.find_next("a", href=True)
                link = _abs_url(link_node["href"], base) if link_node else url
                print(f"    [MITEC] found: '{title[:50]}' {start}~{end}")
                events.append(RawEvent(
                    title=title, organizer="MITEC",
                    location="Kuala Lumpur",
                    venue="Malaysia International Trade & Exhibition Centre",
                    start_date=start, end_date=end,
                    source_url=link, source_site="MITEC",
                    tags=["venue", "exhibition", "kl"],
                ))
        except Exception as exc:
            print(f"[scraper] MITEC error: {exc}")
    return events


def _parse_klcc_date(s: str) -> str:
    """Parse 'May 07, 2026, 12:00 AM' → '2026-05-07'."""
    if not s:
        return ""
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y, %I:%M %p").date().isoformat()
    except ValueError:
        return ""


async def scrape_expolah(client: httpx.AsyncClient) -> list[RawEvent]:
    """
    expolah.com – Malaysian exhibition directory (server-rendered, no bot block).
    Scrapes the 'technology' and 'trade-fairs' categories, filtered to
    electronics-related events via _is_electronics_related().
    Date format on this site: "12 - 13 May, 2026" (comma before year).
    """
    base = "https://expolah.com"
    events: list[RawEvent] = []
    seen:   set[str] = set()

    for category in ["technology", "trade-fairs"]:
        for page in range(1, 8):  # up to 7 pages per category
            url = (
                f"{base}/events/?category={category}"
                if page == 1
                else f"{base}/events/?category={category}&page={page}"
            )
            try:
                html = await _get(client, url)
                soup = _soup(html)
            except Exception as exc:
                print(f"[Expolah] {category} page {page} error: {exc}")
                break

            # Debug: report page structure on first page of each category
            if page == 1:
                all_h3 = soup.find_all("h3")
                all_h2 = soup.find_all("h2")
                print(
                    f"[Expolah debug] {category} → "
                    f"html_len={len(html)} h2={len(all_h2)} h3={len(all_h3)}"
                )
                # Show first 5 h3 texts to understand structure
                for i, h in enumerate(all_h3[:5]):
                    print(f"  h3[{i}]: {_clean(h.get_text())[:80]!r}")

            # Strategy 1: common event-card containers
            cards = soup.select(
                "article, .event-card, .event-item, "
                "[class*='event-card'], [class*='event-item'], "
                ".tribe-events-calendar-list__event-row, li[class*='event']"
            )

            # Strategy 2: any h3 whose text looks like an event title (has year or
            # is long enough), use the h3 element itself as the anchor
            if not cards:
                h3_anchors = soup.find_all("h3")
                print(f"[Expolah debug] {category} p{page}: no cards → "
                      f"trying {len(h3_anchors)} h3 anchors")
                for h3 in h3_anchors:
                    title = _clean(h3.get_text())
                    if not title or len(title) < 6:
                        continue
                    if not _is_electronics_related(title):
                        continue
                    if title in seen:
                        continue
                    seen.add(title)
                    # Date: search ancestor chain for year text
                    container = h3.parent or h3
                    ctx_text  = _clean(container.get_text()).replace(",", " ")
                    start, end = _date_range(ctx_text)

                    venue_el = container.select_one(
                        "[class*='venue'], [class*='location'], [class*='address']"
                    )
                    venue = _clean(venue_el.get_text()) if venue_el else ""

                    link_el = h3.find("a", href=True) or container.find("a", href=True)
                    link    = _abs_url(link_el["href"], base) if link_el else url

                    events.append(RawEvent(
                        title=title,
                        organizer="",
                        location="Kuala Lumpur",
                        venue=venue,
                        start_date=start,
                        end_date=end,
                        source_url=link,
                        source_site="Expolah",
                        tags=["exhibition", "kl", "malaysia"],
                    ))
                if not events and page == 1:
                    break  # nothing on first page → skip remaining pages
                break  # h3 strategy doesn't paginate (flat list)

            found_on_page = False
            for card in cards:
                title_el = card.find("h3") or card.find("h2")
                if not title_el:
                    continue
                title = _clean(title_el.get_text())
                if not title or title in seen:
                    continue
                if not _is_electronics_related(title):
                    continue
                seen.add(title)
                found_on_page = True

                # Date – strip commas so _date_range handles "May, 2026" correctly
                card_text = card.get_text().replace(",", " ")
                start, end = _date_range(card_text)

                # Venue – look for a venue/location element, else leave blank
                venue_el = card.select_one(
                    "[class*='venue'], [class*='location'], [class*='address']"
                )
                venue = _clean(venue_el.get_text()) if venue_el else ""

                link_el = card.find("a", href=True)
                link    = _abs_url(link_el["href"], base) if link_el else url

                events.append(RawEvent(
                    title=title,
                    organizer="",
                    location="Kuala Lumpur",
                    venue=venue,
                    start_date=start,
                    end_date=end,
                    source_url=link,
                    source_site="Expolah",
                    tags=["exhibition", "kl", "malaysia"],
                ))

            if not found_on_page and page > 1:
                break   # no relevant events on this page → stop paginating

    return events


async def scrape_mte(client: httpx.AsyncClient) -> list[RawEvent]:
    """
    Malaysia Technology Expo (MTE) – annual tech innovation expo
    at World Trade Centre KL. Static HTML with event dates in headings/paragraphs.
    """
    base = "https://www.mte.org.my"
    events: list[RawEvent] = []
    try:
        url = f"{base}/"
        html = await _get(client, url)
        soup = _soup(html)

        # Debug: report page structure
        h_counts = {t: len(soup.find_all(t)) for t in ["h1", "h2", "h3"]}
        print(
            f"[MTE debug] html_len={len(html)} "
            f"h1={h_counts['h1']} h2={h_counts['h2']} h3={h_counts['h3']}"
        )
        for i, h in enumerate(soup.find_all(["h1", "h2", "h3"])[:8]):
            print(f"  {h.name}[{i}]: {_clean(h.get_text())[:80]!r}")

        # First try: JSON-LD structured data
        jld = _jsonld_events(
            soup, url,
            "Malaysia Technology Expo", "MTE",
            "Kuala Lumpur", "World Trade Centre Kuala Lumpur",
            ["exhibition", "technology", "innovation", "kl"],
        )
        if jld:
            print(f"[MTE debug] found {len(jld)} JSON-LD events")
            return [e for e in jld if _is_electronics_related(e.title)]

        # Fallback: scan h2/h3 elements for "MTE" or "Malaysia Technology Expo"
        for heading in soup.find_all(["h1", "h2", "h3"]):
            h_text = _clean(heading.get_text())
            if not re.search(r"Malaysia Technology Expo|MTE\s+20\d{2}", h_text, re.I):
                continue

            # Look for date pattern in surrounding text (parent + siblings)
            container = heading.parent or heading
            ctx_text  = _clean(container.get_text())
            start, end = _date_range(ctx_text.replace(",", " "))
            if not start:
                continue

            # Venue: look for "World Trade Centre" or "WTCKL" nearby
            venue = "World Trade Centre Kuala Lumpur"

            link_el = heading.find("a", href=True) or container.find("a", href=True)
            link    = _abs_url(link_el["href"], base) if link_el else base

            events.append(RawEvent(
                title=h_text[:200],
                organizer="Malaysia Technology Expo",
                location="Kuala Lumpur",
                venue=venue,
                start_date=start,
                end_date=end,
                source_url=link,
                source_site="MTE",
                tags=["exhibition", "technology", "innovation", "kl"],
            ))
            break   # one main event per page

    except Exception as exc:
        print(f"[MTE] error: {exc}")

    return events


async def scrape_klcc_convention(client: httpx.AsyncClient) -> list[RawEvent]:
    """KLCC Convention Centre – What's On via XTOPIA CMS internal API."""
    base    = "https://www.klccconventioncentre.com"
    API_URL = f"{base}/data_molecule_source/contentMS_rmo.ashx"
    TID     = "ddb45973-2631-408a-955e-f0b254ee61fa"
    events: list[RawEvent] = []
    seen:   set[str]       = set()

    api_headers = {
        "User-Agent":      HEADERS["User-Agent"],
        "Content-Type":    "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":         f"{base}/whats-on",
        "Origin":          base,
    }

    for idx in range(20):   # safety cap – site currently shows ~35 events
        try:
            r = await client.post(
                API_URL, headers=api_headers,
                data={"tid": TID, "idx": idx, "fsp": ""},
                timeout=TIMEOUT,
            )
            data = r.json()
        except Exception as exc:
            print(f"[KLCC] page {idx} error: {exc}")
            break

        pag   = data.get("pagination", {}).get(TID, {})
        sdk   = data.get("sdk", {}).get(TID, {})
        pages = pag.get("pages", {})

        if not pages:
            break

        for _pi, items in pages.items():
            for item in items:
                title = _clean(html_module.unescape(item.get("Page Name", "")))
                if not title or title in seen:
                    continue
                seen.add(title)

                # Skip events unrelated to electronics / tech
                if not _is_electronics_related(title):
                    continue

                url = item.get("Page Address", "").strip()

                # Dates via xDataId → sdk lookup
                xid       = item.get("xDataId", "")
                sdk_entry = sdk.get(xid, {})
                start = _parse_klcc_date(sdk_entry.get("startDate", ""))
                end   = _parse_klcc_date(sdk_entry.get("endDate",   "")) or start

                events.append(RawEvent(
                    title=title,
                    organizer="KLCC Convention Centre",
                    location="Kuala Lumpur City Centre",
                    venue="Kuala Lumpur Convention Centre",
                    start_date=start,
                    end_date=end,
                    source_url=url,
                    source_site="KLCC Convention",
                    tags=["venue", "convention", "kl"],
                ))

    return events


async def scrape_mvec(client: httpx.AsyncClient) -> list[RawEvent]:
    """MVEC (Mid Valley Exhibition Centre) – calendar-kl.json"""
    JSON_URL = "https://www.mvec.com.my/calendar-kl.json"
    events: list[RawEvent] = []

    try:
        r    = await client.get(JSON_URL, headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
    except Exception as exc:
        print(f"[MVEC] fetch error: {exc}")
        return events

    for item in data:
        # Strip HTML tags from event name (e.g. "24<sup>th</sup> Fair")
        raw_name = item.get("event", "")
        title    = _clean(BeautifulSoup(raw_name, "lxml").get_text())
        if not title:
            continue

        # Skip events unrelated to electronics / home appliances
        if not _is_electronics_related(title):
            continue

        date_str    = (item.get("datetime") or {}).get("date", "")
        start, end  = _date_range(date_str)

        organiser   = item.get("organiser") or {}
        organizer   = _clean(organiser.get("name", ""))
        website     = (organiser.get("website") or "").strip()
        if website and not website.startswith("http"):
            website = "https://" + website

        events.append(RawEvent(
            title=title,
            organizer=organizer,
            location="Kuala Lumpur",
            venue="Mid Valley Exhibition Centre",
            start_date=start,
            end_date=end,
            source_url=website or JSON_URL,
            source_site="MVEC",
            tags=["venue", "exhibition", "midvalley", "kl"],
        ))

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
        f"{base}/",                              # homepage — most recent articles
        f"{base}/category/deals/",
        f"{base}/category/news/",
        f"{base}/category/features/",
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
    "Expolah":              ("Tier1-Exhibition", scrape_expolah),
    "MTE":                  ("Tier1-Exhibition", scrape_mte),
    "Senheng":              ("Tier2-Retail",     scrape_senheng),
    "Harvey Norman MY":     ("Tier2-Retail",     scrape_harvey_norman),
    "Courts MY":            ("Tier2-Retail",     scrape_courts),
    # TMT (tmt.my) uses a fully JS-rendered custom platform — no static API.
    # Re-enable when Playwright is available.
    # "TMT":                ("Tier2-Retail",     scrape_tmt),
    "MITEC":                ("Tier3-Venue",      scrape_mitec),
    "KLCC Convention":      ("Tier3-Venue",      scrape_klcc_convention),
    "MVEC":                 ("Tier3-Venue",      scrape_mvec),
    # ExhibitionsForYou blocks GitHub Actions IPs (Cloudflare 403).
    # Re-enable when Playwright / residential proxy is available.
    # "ExhibitionsForYou":  ("Tier3-Venue",      scrape_exhibitionsforyou),
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
    """Run all scrapers in small concurrent batches to avoid rate-limiting."""
    results: dict[str, list[RawEvent]] = {}
    names   = list(SCRAPERS.keys())
    BATCH   = 4   # run at most 4 sites concurrently

    async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
        for i in range(0, len(names), BATCH):
            batch = names[i:i + BATCH]
            tasks = {name: asyncio.create_task(SCRAPERS[name][1](client)) for name in batch}
            for name, task in tasks.items():
                try:
                    results[name] = await task
                except Exception as exc:
                    print(f"[scraper] {name} failed: {exc}")
                    results[name] = []
            if i + BATCH < len(names):
                await asyncio.sleep(2)   # 2-second gap between batches

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
