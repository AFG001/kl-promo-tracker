"""
Playwright-based scrapers for JS-rendered / Cloudflare-protected sites.

Sites:
  - ExhibitionsForYou  (Cloudflare blocks CI IPs on static httpx fetch)
  - TMT / Thunder Match (custom JS-rendered e-commerce platform)

Requirements (no third-party stealth library needed):
  pip install playwright
  playwright install chromium

Run scrapers sequentially (one Chromium process at a time) to stay
within GitHub Actions memory limits (2 vCPU / 7 GB RAM).
"""
import re
from datetime import date, timedelta

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError as _err:
    PLAYWRIGHT_AVAILABLE = False
    print(f"[scraper_pw] WARNING: playwright not installed ({_err})")

from scraper import RawEvent, _clean, _date_range, _is_electronics_related

# ── constants ──────────────────────────────────────────────────────────────────
NAV_TIMEOUT = 45_000   # ms per page navigation

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Minimal JS to suppress obvious headless/automation indicators.
# Covers the most common Cloudflare / bot-detection checks.
_STEALTH_JS = """
// Hide the webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Fake plugin list (headless has 0 plugins)
Object.defineProperty(navigator, 'plugins', {
    get: () => ({ length: 3, item: () => null, namedItem: () => null,
                   refresh: () => {} })
});

// Mimic real browser languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en', 'ms']
});

// Provide a minimal chrome object
if (!window.chrome) {
    window.chrome = { runtime: {}, app: { isInstalled: false } };
}

// Remove HeadlessChrome from User-Agent string exposed to JS
Object.defineProperty(navigator, 'userAgent', {
    get: () => navigator.userAgent.replace('HeadlessChrome', 'Chrome')
});
"""


# ── shared helpers ─────────────────────────────────────────────────────────────

async def _new_page(browser):
    """Create a browser context with human-like fingerprint (no extra libs)."""
    ctx = await browser.new_context(
        user_agent=_UA,
        locale="en-US",
        timezone_id="Asia/Kuala_Lumpur",
        viewport={"width": 1280, "height": 800},
        java_script_enabled=True,
        extra_http_headers={
            "Accept-Language":  "en-US,en;q=0.9",
            "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "sec-ch-ua":        '"Chromium";v="124","Google Chrome";v="124","Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )
    page = await ctx.new_page()
    await page.add_init_script(_STEALTH_JS)
    return page


async def _safe_goto(page, url: str, *, retries: int = 2) -> bool:
    """Navigate to url; return True on success (HTTP < 400)."""
    for attempt in range(retries):
        try:
            resp = await page.goto(url, timeout=NAV_TIMEOUT, wait_until="networkidle")
            if resp and resp.status < 400:
                return True
            print(f"    [PW] HTTP {resp.status if resp else '?'} for {url}")
        except Exception as exc:
            print(f"    [PW] goto {url} attempt {attempt + 1}: {exc}")
    return False


# ===========================================================================
# ExhibitionsForYou
# ===========================================================================

async def scrape_exhibitionsforyou_pw() -> list[RawEvent]:
    """
    exhibitionsforyou.com – all Malaysia events, filtered to
    electronics/tech category via _is_electronics_related().
    Playwright used because Cloudflare returns 403 on bare httpx.
    """
    events: list[RawEvent] = []
    seen:   set[str] = set()
    MYS  = re.compile(r"Malaysia|Kuala Lumpur\b|\bKL\b", re.I)
    BASE = "https://exhibitionsforyou.com"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await _new_page(browser)

        for pg in range(1, 8):
            url = (
                f"{BASE}/all-events/?location=Malaysia"
                if pg == 1
                else f"{BASE}/all-events/page/{pg}/?location=Malaysia"
            )

            if not await _safe_goto(page, url):
                print(f"    [ExhibitionsForYou PW] page {pg} failed — stopping")
                break

            # ── locate event cards ─────────────────────────────────────────
            boxes = await page.query_selector_all(
                ".event-box, "
                ".tribe-events-list-event-row, "
                ".tribe-event-list-item, "
                "article.type-tribe_events, "
                ".events-archive__item"
            )
            if not boxes:
                # Generic fallback: articles / list items with a 4-digit year
                all_items = await page.query_selector_all("article, li")
                boxes = [b for b in all_items
                         if "202" in (await b.inner_text())]

            if not boxes:
                print(f"    [ExhibitionsForYou PW] no event cards on page {pg}")
                break

            found_on_page = False
            for box in boxes:
                try:
                    box_text = await box.inner_text()
                except Exception:
                    continue

                if not MYS.search(box_text):
                    continue    # skip non-Malaysia events

                # title
                title_el = await box.query_selector(
                    "h2, h3, h4, .event-title, "
                    ".tribe-event-url, [class*='event-name'], a.url"
                )
                if not title_el:
                    continue
                title = _clean(await title_el.inner_text())
                if not title or title in seen or not _is_electronics_related(title):
                    continue
                seen.add(title)
                found_on_page = True

                # date
                date_el = await box.query_selector(
                    "h4, .event-date, time, "
                    ".tribe-event-date-start, [class*='date'], abbr"
                )
                date_raw = _clean(await date_el.inner_text()) if date_el else ""
                start, end = _date_range(date_raw)

                # link
                link_el = await box.query_selector("a[href]")
                try:
                    link = await link_el.get_attribute("href") if link_el else url
                except Exception:
                    link = url
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
                break   # no matching events on this page → stop paginating

        await browser.close()

    print(f"    [ExhibitionsForYou PW] total {len(events)} events")
    return events


# ===========================================================================
# TMT – Thunder Match
# ===========================================================================

async def scrape_tmt_pw() -> list[RawEvent]:
    """
    tmt.my – custom JS-rendered e-commerce.
    Scrapes current sale / promotion product listings.
    Assigns today → today+30 as the promotion date window so events
    appear on the calendar as "active this month".
    """
    events: list[RawEvent] = []
    seen:   set[str] = set()
    TODAY     = date.today().isoformat()
    MONTH_END = (date.today() + timedelta(days=30)).isoformat()
    BASE      = "https://www.tmt.my"

    CARD_SEL  = (
        ".product-item, .product-card, .product-tile, "
        "[class*='product-grid-item'], [class*='product-list-item'], "
        "article.product"
    )
    TITLE_SEL = (
        "h2, h3, h4, .product-title, .product-name, "
        "[class*='product-title'], [class*='product-name'], "
        "[class*='ProductTitle'], [class*='ProductName']"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await _new_page(browser)

        for path in [
            "/collections/sale",
            "/collections/promotions",
            "/collections/deals",
            "/collections/featured",
            "/",
        ]:
            url = BASE + path
            if not await _safe_goto(page, url):
                continue

            # Give JS carousels extra settle time
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

            cards = await page.query_selector_all(CARD_SEL)
            if not cards:
                cards = await page.query_selector_all(
                    "li[class*='product'], div[class*='product']"
                )
            if not cards:
                continue

            for card in cards[:40]:
                try:
                    title_el = await card.query_selector(TITLE_SEL)
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
                print(f"    [TMT PW] {len(events)} products from {path}")
                break

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
    time) to avoid OOM on GitHub Actions.
    Returns empty dict immediately if Playwright is not installed.
    """
    if not PLAYWRIGHT_AVAILABLE:
        print("[scraper_pw] Playwright not available — skipping PW scrapers")
        return {}

    results: dict[str, list[RawEvent]] = {}
    for name, fn in _PW_SCRAPERS:
        print(f"  [PW] Starting: {name}")
        try:
            results[name] = await fn()
        except Exception as exc:
            print(f"  [PW] {name} crashed: {exc}")
            results[name] = []

    return results
