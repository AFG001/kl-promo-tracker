"""
Claude API integration.
1. Structure / normalise raw scraped events → English JSON
2. Generate a concise English digest report for team sharing
"""
import json
import os
from datetime import datetime

import anthropic

from scraper import RawEvent


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# ---------------------------------------------------------------------------
# Event structuring
# ---------------------------------------------------------------------------

STRUCTURE_SYSTEM = (
    "You are an assistant that structures consumer-electronics promotion and event data "
    "collected from Kuala Lumpur retail and event websites. "
    "Return only a JSON array (no markdown fences). All field values must be in English."
)

STRUCTURE_PROMPT = """Organise the raw scraped data below into a JSON array.
Each object must have exactly these fields:
- title       : event / promotion name (English)
- organizer   : organiser name (e.g. Senheng, Samsung Malaysia)
- location    : city or area (e.g. Kuala Lumpur, Petaling Jaya)
- venue       : specific venue name, or empty string
- start_date  : YYYY-MM-DD (use today if unknown)
- end_date    : YYYY-MM-DD (same as start_date if unknown)
- category    : one of Electronics | Brand Event | Mall Event | Online | Other
- description : brief English description (max 200 chars)
- summary     : one-sentence summary for a sales promotion team (max 120 chars, English)
- tags        : array of lowercase strings (e.g. ["samsung","tv","discount"])
- source_url  : original URL
- source_site : site name

Today's date: {today}

Rules:
- Exclude events unrelated to consumer electronics / home appliances.
- Merge duplicate titles into one record.
- If a date cannot be determined, make a reasonable estimate and note it in description.

Raw data:
{data}
"""


async def structure_events(raw_events: list[RawEvent]) -> list[dict]:
    if not raw_events:
        return []

    today = datetime.utcnow().strftime("%Y-%m-%d")
    lines = [
        f"- Title: {e.title}\n"
        f"  Organizer: {e.organizer}\n"
        f"  Location: {e.location} / {e.venue}\n"
        f"  Period: {e.start_date} to {e.end_date}\n"
        f"  Description: {e.description[:300]}\n"
        f"  URL: {e.source_url}\n"
        f"  Site: {e.source_site}\n"
        for e in raw_events
    ]

    batch_size = 20
    all_results: list[dict] = []
    client = _get_client()

    for i in range(0, len(lines), batch_size):
        batch = lines[i: i + batch_size]
        prompt = STRUCTURE_PROMPT.format(today=today, data="\n".join(batch))
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=STRUCTURE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(text)
            if isinstance(parsed, list):
                all_results.extend(parsed)
        except Exception as exc:
            print(f"[summarizer] batch {i} error: {exc}")
            # Fallback without AI enrichment
            for e in raw_events[i: i + batch_size]:
                all_results.append({
                    "title":       e.title,
                    "organizer":   e.organizer,
                    "location":    e.location,
                    "venue":       e.venue,
                    "start_date":  e.start_date,
                    "end_date":    e.end_date,
                    "category":    "Electronics",
                    "description": e.description,
                    "summary":     "",
                    "tags":        e.tags,
                    "source_url":  e.source_url,
                    "source_site": e.source_site,
                })

    return all_results


# ---------------------------------------------------------------------------
# Digest report
# ---------------------------------------------------------------------------

DIGEST_SYSTEM = (
    "You are a consumer-electronics market analyst specialising in the Kuala Lumpur retail scene. "
    "Write in clear, professional English for an internal sales promotion team."
)

DIGEST_PROMPT = """Based on the collected KL electronics promotion and event data below,
write a concise English report with the following sections:

## Trend Summary
(3–5 sentences on overall market activity this period)

## Activity by Retailer / Brand
(bullet points per major organiser)

## Top 5 Events to Watch
(event name, dates, venue, and why it matters)

## Strategic Implications
(2–3 actionable insights for our own promotional planning)

## Competitive Monitoring Points
(what to watch in the next cycle)

---
Report date: {today}
Total events collected: {count}

Event data (JSON):
{events_json}
"""


def generate_digest(events: list[dict]) -> str:
    if not events:
        return "No data available for the selected period."

    today = datetime.utcnow().strftime("%d %B %Y")
    slim = [
        {
            "title":      e.get("title"),
            "organizer":  e.get("organizer"),
            "location":   e.get("location"),
            "venue":      e.get("venue"),
            "start_date": e.get("start_date"),
            "end_date":   e.get("end_date"),
            "category":   e.get("category"),
            "summary":    e.get("summary"),
            "tags":       e.get("tags"),
        }
        for e in events[:100]
    ]

    prompt = DIGEST_PROMPT.format(
        today=today,
        count=len(events),
        events_json=json.dumps(slim, ensure_ascii=False, indent=2),
    )

    try:
        msg = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=DIGEST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        return f"Report generation error: {exc}"
