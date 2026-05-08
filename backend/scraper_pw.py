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
# 10times.com – global exhibition directory
# ===========================================================================

async def scrape_10times_pw() -> list[RawEvent]:
    """
    10times.com – largest global trade fair database.
    Returns 403 on bare httpx; Playwright bypasses Cloudflare.
    Scrapes the Malaysia electronics + technology trade-show pages.
    """
    events: list[RawEvent] = []
    seen:   set[str] = set()
    BASE   = "https://10times.com"

    URLS = [
        f"{BASE}/malaysia/electronics-electricals/tradeshows",
        f"{BASE}/malaysia/technology/tradeshows",
        f"{BASE}/malaysia/computer/tradeshows",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await _new_page(browser)

        for url in URLS:
            if not await _safe_goto(page, url):
                continue

            # 10times is a React app — wait for event cards to render
            try:
                await page.wait_for_selector(
                    "[class*='event'], [class*='show-'], article, "
                    ".event-name, h3, h2",
                    timeout=15_000,
                )
            except Exception:
                pass

            cards = await page.query_selector_all(
                "[class*='event-box'], [class*='event-card'], "
                "[class*='event-item'], [class*='show-item'], "
                "[class*='show-card'], article[class*='event'], "
                "li[class*='event'], .event-listing"
            )
            if not cards:
                # Fallback: any element containing an h3 + a year-like text
                all_h3 = await page.query_selector_all("h3, h2")
                for h in all_h3:
                    txt = await h.inner_text()
                    if re.search(r"\b20\d{2}\b", txt):
                        parent = await h.evaluate_handle("el => el.parentElement")
                        cards.append(parent)

            for card in cards[:30]:
                try:
                    # title
                    title_el = await card.query_selector(
                        "h3, h2, [class*='event-name'], [class*='show-name'], "
                        "[class*='title'], a[class*='name']"
                    )
                    if not title_el:
                        continue
                    title = _clean(await title_el.inner_text())
                except Exception:
                    continue

                if not title or len(title) < 6 or title in seen:
                    continue
                if not _is_electronics_related(title):
                    continue
                seen.add(title)

                # date — try specific selector first, fall back to full card text
                date_raw = ""
                try:
                    date_el = await card.query_selector(
                        "[class*='date'], [class*='period'], time, "
                        "[class*='when'], [class*='timing'], [class*='schedule']"
                    )
                    if date_el:
                        date_raw = _clean(await date_el.inner_text())
                except Exception:
                    pass
                if not date_raw:
                    try:
                        # Scan full card text for any date pattern
                        date_raw = await card.inner_text()
                    except Exception:
                        pass
                start, end = _date_range(date_raw.replace(",", " "))

                # venue / location
                venue = ""
                try:
                    venue_el = await card.query_selector(
                        "[class*='venue'], [class*='location'], "
                        "[class*='city'], [class*='place']"
                    )
                    if venue_el:
                        venue = _clean(await venue_el.inner_text())
                except Exception:
                    pass

                # link
                try:
                    link_el = await card.query_selector("a[href]")
                    link = await link_el.get_attribute("href") if link_el else url
                    if link and not link.startswith("http"):
                        link = BASE + link
                except Exception:
                    link = url

                events.append(RawEvent(
                    title=title,
                    organizer="",
                    location="Kuala Lumpur",
                    venue=venue,
                    start_date=start,
                    end_date=end,
                    source_url=link,
                    source_site="10times",
                    tags=["exhibition", "trade-fair", "kl", "malaysia"],
                ))

        await browser.close()

    print(f"    [10times PW] total {len(events)} events")
    return events


# ===========================================================================
# MyCEB – Malaysia Convention & Exhibition Bureau (government MICE body)
# ===========================================================================

async def scrape_myceb_pw() -> list[RawEvent]:
    """
    MyCEB calendar via Playwright.
    MyCEB uses xtopia.io CMS + SimpleView CRM embed.
    Events appear to be in an iframe or via SimpleView's sched API.
    This function: loads the page, reads all Playwright frames (incl. iframes),
    and harvests event text from whichever frame contains event data.
    """
    events: list[RawEvent] = []
    seen:   set[str] = set()
    BASE   = "https://www.myceb.com.my"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await _new_page(browser)

        url = BASE + "/events/calendar-of-events"
        if not await _safe_goto(page, url):
            await browser.close()
            return events

        # Wait for initial load
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        # The calendar requires a dropdown selection before events load.
        # Find and interact with "Please select one" <select> element.
        try:
            selects = await page.query_selector_all("select")
            print(f"    [MyCEB PW] select elements: {len(selects)}")
            for sel in selects:
                opts = await sel.evaluate(
                    "el => [...el.options].map(o => ({v: o.value, t: o.text.trim()}))"
                )
                print(f"      options: {opts[:10]}")
                # Try each non-empty option to trigger event loading
                for opt in opts:
                    if not opt["v"]:
                        continue
                    print(f"      selecting: {opt['v']!r} ({opt['t']!r})")
                    await sel.select_option(opt["v"])
                    await page.wait_for_timeout(3000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8_000)
                    except Exception:
                        pass
                    body_after = await page.inner_text("body")
                    print(f"      body after select ({len(body_after)} chars): {body_after[:600]!r}")
                    # If we see event-like content (year + more text), stop
                    if len(body_after) > 400 and re.search(r"\b20\d{2}\b", body_after):
                        break
        except Exception as exc:
            print(f"    [MyCEB PW] dropdown error: {exc}")

        # Also try clicking any visible button that might load events
        try:
            btns = await page.query_selector_all("button, input[type='submit'], a[class*='btn']")
            for btn in btns[:5]:
                btn_text = _clean(await btn.inner_text())
                print(f"    [MyCEB PW] button: {btn_text!r}")
        except Exception:
            pass

        # Inspect ALL Playwright frames (iframes load as separate frames)
        all_frames = page.frames
        print(f"    [MyCEB PW] {len(all_frames)} frames total")
        for frame in all_frames:
            furl = frame.url
            if furl != url:
                print(f"      frame: {furl[:100]}")

        # Try to extract events from any frame that mentions "2026" or event patterns
        for frame in all_frames:
            try:
                ftext = await frame.inner_text("body")
            except Exception:
                continue
            if not re.search(r"\b20\d{2}\b", ftext):
                continue

            # Extract event blocks using JS inside the frame
            try:
                items = await frame.evaluate("""
                () => {
                    const rows = [];
                    // SimpleView calendar typically uses table rows or list items
                    const candidates = [
                        ...document.querySelectorAll('tr, li, .event, [class*="event"], [class*="calendar"]')
                    ];
                    candidates.forEach(el => {
                        const text = (el.innerText || '').trim();
                        if (text.length > 15 && /\\b20\\d{2}\\b/.test(text)) {
                            rows.push(text.slice(0, 300));
                        }
                    });
                    return rows.slice(0, 50);
                }
                """)
                print(f"    [MyCEB PW] frame {frame.url[:60]} → {len(items)} event rows")
                for row in items[:5]:
                    print(f"      {row[:120]!r}")
            except Exception:
                items = []

            for row_text in items:
                title_m = re.search(r"[A-Z][A-Za-z0-9 &:,'-]{15,}", row_text)
                if not title_m:
                    continue
                title = title_m.group().strip()
                if title in seen:
                    continue
                if not _is_electronics_related(title):
                    continue
                seen.add(title)
                start, end = _date_range(row_text.replace(",", " "))
                events.append(RawEvent(
                    title=title,
                    organizer="MyCEB",
                    location="Kuala Lumpur",
                    venue="",
                    start_date=start,
                    end_date=end,
                    source_url=url,
                    source_site="MyCEB",
                    tags=["exhibition", "mice", "kl", "malaysia"],
                ))

        await browser.close()

    print(f"    [MyCEB PW] total {len(events)} events")
    return events


# ===========================================================================
# Runner
# ===========================================================================

_PW_SCRAPERS: list[tuple[str, callable]] = [
    ("ExhibitionsForYou", scrape_exhibitionsforyou_pw),
    # TMT disabled: tmt.my consistently times out (networkidle never reached).
    # ("TMT",             scrape_tmt_pw),
    ("10times",           scrape_10times_pw),
    # MyCEB disabled: xtopia.io CMS doesn't render in headless mode
    # (dropdown options don't load); SimpleView CRM backend requires auth.
    # ("MyCEB",           scrape_myceb_pw),
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
