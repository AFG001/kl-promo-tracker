"""
Firebase Firestore database layer.
Replaces the previous SQLite implementation.

Collections:
  events       — normalised promotion / event records
  scrape_logs  — per-run scraping audit trail
"""
import hashlib
import os
from datetime import datetime, timezone
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

_db: Any = None  # google.cloud.firestore.Client


def _get_db():
    global _db
    if _db is not None:
        return _db

    if not firebase_admin._apps:
        key_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
        if key_path and os.path.isfile(key_path):
            cred = credentials.Certificate(key_path)
        else:
            # Fall back to Application Default Credentials
            # (works on GCP / when GOOGLE_APPLICATION_CREDENTIALS is set)
            cred = credentials.ApplicationDefault()

        project_id = os.getenv("FIREBASE_PROJECT_ID")
        init_kwargs: dict = {"credential": cred}
        if project_id:
            init_kwargs["options"] = {"projectId": project_id}
        firebase_admin.initialize_app(**init_kwargs)

    _db = firestore.client()
    return _db


def init_db():
    """Ensure Firestore connection is established."""
    _get_db()
    print("[db] Firestore connected.")


# ---------------------------------------------------------------------------
# Document ID helpers
# ---------------------------------------------------------------------------

def _event_doc_id(title: str, start_date: str, organizer: str) -> str:
    """Deterministic doc ID used for deduplication."""
    raw = f"{(title or '').strip()}|{(start_date or '').strip()}|{(organizer or '').strip()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def upsert_event(event: dict) -> tuple[str, bool]:
    """
    Insert or update an event document.
    Returns (doc_id, is_new).
    """
    db = _get_db()
    now = _now_iso()

    doc_id = _event_doc_id(
        event.get("title", ""),
        event.get("start_date", ""),
        event.get("organizer", ""),
    )

    ref = db.collection("events").document(doc_id)
    snap = ref.get()
    is_new = not snap.exists

    payload = {
        "title":       event.get("title", ""),
        "organizer":   event.get("organizer", ""),
        "location":    event.get("location", ""),
        "venue":       event.get("venue", ""),
        "start_date":  event.get("start_date", ""),
        "end_date":    event.get("end_date", ""),
        "category":    event.get("category", ""),
        "description": event.get("description", ""),
        "summary":     event.get("summary", ""),
        "source_url":  event.get("source_url", ""),
        "source_site": event.get("source_site", ""),
        "tags":        event.get("tags", []),
        "updated_at":  now,
    }
    if is_new:
        payload["scraped_at"] = now

    ref.set(payload, merge=True)
    return doc_id, is_new


def get_events(
    start: str | None = None,
    end: str | None = None,
    site: str | None = None,
    category: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """
    Query events with optional filters.
    Firestore has limited multi-field query support, so post-filter
    for search (full-text) and date range after fetching.
    """
    db = _get_db()
    query = db.collection("events")

    if site:
        query = query.where(filter=FieldFilter("source_site", "==", site))
    if category:
        query = query.where(filter=FieldFilter("category", "==", category))

    docs = query.stream()
    results: list[dict] = []

    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id

        # Date range filter (post-query)
        s_date = d.get("start_date", "")
        e_date = d.get("end_date", "") or s_date

        if start and e_date and e_date < start:
            continue
        if end and s_date and s_date > end:
            continue

        # Keyword search (post-query)
        if search:
            needle = search.lower()
            haystack = " ".join([
                d.get("title", ""),
                d.get("description", ""),
                d.get("organizer", ""),
            ]).lower()
            if needle not in haystack:
                continue

        results.append(d)

    results.sort(key=lambda x: x.get("start_date", "") or "")
    return results


def get_event(doc_id: str) -> dict | None:
    db = _get_db()
    snap = db.collection("events").document(doc_id).get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    d["id"] = snap.id
    return d


def get_scrape_logs(limit: int = 50) -> list[dict]:
    db = _get_db()
    docs = (
        db.collection("scrape_logs")
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [{"id": d.id, **d.to_dict()} for d in docs]


def log_scrape(
    site: str,
    status: str,
    items_found: int = 0,
    error_msg: str | None = None,
) -> str:
    db = _get_db()
    now = _now_iso()
    _, ref = db.collection("scrape_logs").add({
        "site":        site,
        "status":      status,
        "items_found": items_found,
        "error_msg":   error_msg or "",
        "started_at":  now,
        "finished_at": now,
    })
    return ref.id
