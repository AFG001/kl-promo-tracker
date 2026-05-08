"""
Playwright-based scrapers for JS-rendered / Cloudflare-protected sites.

Sites:
  - ExhibitionsForYou  (Cloudflare blocks CI IPs on static fetch)
  - TMT / Thunder Match (custom JS-rendered e-commerce platform)

Requirements:
  pip install playwright playwright-stealth
  playwright install chromium

Run sequentially (1 Chromium process at a time) to stay within
GitHub Actions memory limits (7 GB RAM, 2 vCPU).
"""
import re
from datetime import date, timedelta

try:
    from playwright.async_api import async_playwright
    from playwright_stealth import stealth_async
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[scraper_pw] WARNING: playwright / playwright-stealth not installed")

from scraper import RawEvent, _clean, _date_range, _is_electronics_related

# ── constants ─────────────────────────────────────────────────────────────────
NAV_TIMEOUT  = 45_000   # 45 s per page navigation
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── shared helpers ─────────────────────────────────────────────────────────────

async def _new_stealth_page(browser):
    """Create a browser context with human-like fingerprint + stealth patches."""
    ctx = await browser.new_context(
        user_agent=_UA,
        locale="en-US",
        timezone_id="Asia/Kuala_Lumpur",
        viewport={"width": 1280, "height": 800},
        java_script_enabled=True,
    )
    page = await ctx.new_page()
    await stealth_async(page)
    return page


async def _safe_goto(page, url: str, *, retries: int = 2) -> bool:
    """Navigate to url, return True on success."""
    for attempt in range(retries):
        try:
            resp = await page.goto(url, timeout=NAV_TIMEOUT, wait_until="networkidle")
            if resp and resp.status < 400:
                return True
            if resp:
                print(f"    [PW] HTTP {resp.status} for {url}")
        except Exception as exc:
            print(f"    [PW] goto {url} attempt {attempt+1} error: {exc}")
    return False


# ===========================================================================
# ExhibitionsForYou
# ===========================================================================

async def scrape_exhibitionsforyou_pw() -> list[RawEvent]:
    """
    exhibitionsforyou.com – Malaysia events, filtered to electronics category.
    Uses Playwright to bypass Cloudflare (returns 403 on static httpx).
    """
    events: list[RawEvent] = []
    seen:   set[str] = set()
    MYS    = re.compile(r"Malaysia|Kuala Lumpur\b|\bKL\b", re.I)
    BASE   = "https://exhibitionsforyou.com"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await _new_stealth_page(browser)

        for pg in range(1, 8):
            if pg == 1:
                url = f"{BASE}/all-events/?location=Malaysia"
            else:
                url = f"{BASE}/all-events/page/{pg}/?location=Malaysia"

            ok = await _safe_goto(page, url)
            if not ok:
                print(f"    [ExhibitionsForYou PW] failed page {pg}, stopping")
                break

            # ── find event cards ───────────────────────────────────────────
            # The site uses .event-box on category pages; the all-events page
            # may use the same or .tribe-events-list-event-row etc.
            boxes = await page.query_selector_all(
                ".event-box, .tribe-events-list-event-row, "
                ".tribe-event-list-item, article.type-tribe_events, "
                ".events-list__event, li.event"
            )
            if not boxes:
                # Generic fallback: any <article> or <li> containing a year
                boxes = await page.query_selector_all("article, li")
                boxes = [b for b in boxes
                         if str(await b.inner_text()).count("202") > 0]

            if not boxes:
                print(f"    [ExhibitionsForYou PW] no event boxes on page {pg}")
                break

            found_on_page = False
            for box in boxes:
                try:
                    box_text = await box.inner_text()
                except Exception:
                    continue
                if not MYS.search(box_text):
                    continue

                # title
                title_el = await box.query_selector(
                    "h2, h3, h4, .event-title, "
                    ".tribe-event-url, [class*='event-name'], a.url"
                )
                if not title_el:
                    continue
                title = _clean(await title_el.inner_text())
                if not title or title in seen:
                    continue
                if not _is_electronics_related(title):
                    continue
                seen.add(title)
                found_on_page = True

                # date
                date_el = await box.query_selector(
                    "h4, .event-date, time, "
                    ".tribe-event-date-start, [class*='event-date'], abbr"
                )
                date_raw = _clean(await date_el.inner_text()) if date_el else ""
                start, end = _date_range(date_raw)

                # link
                link_el = await box.query_selector("a[href]")
                link = await link_el.get_attribute("href") if link_el else url
                if link and not link.startswith("http"):
                    link = BASE + link

                events.append(RawEvent(
                    title=title,
                    organizer="",
                    location="Kuala Lumpur",
                    venue="",
                    start_date=start,
                    end_date=end,
                    source_url=link,
                    source_site="ExhibitionsForYou",
                    tags=["exhibition", "kl", "malaysia"],
                ))

            if not found_on_page and pg > 1:
                break   # no Malaysia+electronics events on this page → stop

        await browser.close()

    print(f"    [ExhibitionsForYou PW] found {len(events)} events")
    return events


# ===========================================================================
# TMT – Thunder Match
# ===========================================================================

async def scrape_tmt_pw() -> list[RawEvent]:
    """
    tmt.my – custom JS-rendered e-commerce platform.
    Scrapes the sale / promotions section of the homepage or dedicated pages.
    Assigns today → today+30 as the promotion date window.
    """
    events: list[RawEvent] = []
    seen:   set[str] = set()
    TODAY     = date.today().isoformat()
    MONTH_END = (date.today() + timedelta(days=30)).isoformat()
    BASE      = "https://www.tmt.my"

    # Selectors for product cards (try broad → narrow)
    CARD_SELECTORS = (
        ".product-item, .product-card, .product-tile, "
        "[class*='product-grid'] li, [class*='product-list'] li, "
        "article.product"
    )
    TITLE_SELECTORS = (
        "h2, h3, h4, .product-title, .product-name, "
        "[class*='product-title'], [class*='product-name']"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await _new_stealth_page(browser)

        for path in [
            "/collections/sale",
            "/collections/promotions",
            "/collections/deals",
            "/collections/featured",
            "/",   # homepage as last resort
        ]:
            url = BASE + path
            ok  = await _safe_goto(page, url)
            if not ok:
                continue

            # Give JS carousels a moment to settle
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

            cards = await page.query_selector_all(CARD_SELECTORS)
            if not cards:
                # Try a more generic selector
                cards = await page.query_selector_all("li[class*='product'], div[class*='product']")

            if not cards:
                continue

            for card in cards[:40]:
                try:
                    title_el = await card.query_selector(TITLE_SELECTORS)
                    if not title_el:
                        continue
                    title = _clean(await title_el.inner_text())
                except Exception:
                    continue

                if not title or len(title) < 5 or title in seen:
                    continue
                seen.add(title)

                link_el = await card.query_selector("a[href]")
                try:
                    link = await link_el.get_attribute("href") if link_el else url
                except Exception:
                    link = url
                if link and not link.startswith("http"):
                    link = BASE + link

                events.append(RawEvent(
                    title=title,
                    organizer="Thunder Match (TMT)",
                    location="Kuala Lumpur / Malaysia",
                    start_date=TODAY,
                    end_date=MONTH_END,
                    source_url=link,
                    source_site="TMT",
                    tags=["electronics", "retail", "promotion"],
                ))

            if events:
                print(f"    [TMT PW] got {len(events)} products from {path}")
                break   # first working page is enough

        await browser.close()

    print(f"    [TMT PW] total {len(events)} events")
    return events


# ===========================================================================
# Runner
# ===========================================================================

_PW_SCRAPERS: list[tuple[str, callable]] = [
    ("ExhibitionsForYou", scrape_exhibitionsforyou_pw),
    ("TMT",               scrape_tmt_pw),
]


async def run_pw_scrapers() -> dict[str, list[RawEvent]]:
    """
    Run all Playwright scrapers **sequentially** (one Chromium process at a
    time) to avoid OOM on GitHub Actions (2 vCPU / 7 GB RAM).
    """
    results: dict[str, list[RawEvent]] = {}

    if not PLAYWRIGHT_AVAILABLE:
        print("[scraper_pw] Playwright not installed — skipping all PW scrapers")
        return results

    for name, fn in _PW_SCRAPERS:
        print(f"  [PW scraper] Starting: {name}")
        try:
            events = await fn()
            results[name] = events
        except Exception as exc:
            print(f"  [PW scraper] {name} crashed: {exc}")
            results[name] = []

    return results
