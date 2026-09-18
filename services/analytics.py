"""Airtable-backed KPI analytics with short in-memory cache + SQLite funnel."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pipeline.crm import fetch_from_airtable
from utils.funnel_log import funnel_summary, recent_funnel_activity

CACHE_TTL_SECONDS = 60

_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Any]] = {}


def _cache_get(key: str) -> Optional[Any]:
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        expires, value = item
        if time.time() > expires:
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: Any, ttl: int = CACHE_TTL_SECONDS) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)


def clear_analytics_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _normalize_status(raw: Any) -> str:
    text = str(raw or "").strip()
    low = text.lower()
    if low == "hot":
        return "Hot"
    if low == "warm":
        return "Warm"
    if low == "cold":
        return "Cold"
    return text or "Unknown"


def _score_of(rec: Dict[str, Any]) -> Optional[float]:
    val = rec.get("lead_score", rec.get("Lead Score"))
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _created_ts(rec: Dict[str, Any]) -> Optional[float]:
    raw = rec.get("_created_time") or rec.get("createdTime") or ""
    if not raw:
        return None
    try:
        # Airtable: 2024-01-15T12:34:56.000Z
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


def get_airtable_records_cached() -> List[Dict[str, Any]]:
    cached = _cache_get("airtable_records")
    if cached is not None:
        return cached
    records = fetch_from_airtable() or []
    _cache_set("airtable_records", records)
    return records


def compute_summary(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    cached = _cache_get("summary")
    if cached is not None and records is None:
        return cached

    rows = records if records is not None else get_airtable_records_cached()
    total = len(rows)
    hot = warm = cold = unknown = 0
    score_sum = 0.0
    score_n = 0
    for r in rows:
        status = _normalize_status(r.get("status_tag") or r.get("Status"))
        if status == "Hot":
            hot += 1
        elif status == "Warm":
            warm += 1
        elif status == "Cold":
            cold += 1
        else:
            unknown += 1
        score = _score_of(r)
        if score is not None:
            score_sum += score
            score_n += 1

    def pct(n: int) -> float:
        return round((n / total) * 100.0, 1) if total else 0.0

    payload = {
        "total_leads": total,
        "hot": {"count": hot, "pct": pct(hot)},
        "warm": {"count": warm, "pct": pct(warm)},
        "cold": {"count": cold, "pct": pct(cold)},
        "unknown": {"count": unknown, "pct": pct(unknown)},
        "avg_lead_score": round(score_sum / score_n, 2) if score_n else 0.0,
        "scored_count": score_n,
        "cached_for_seconds": CACHE_TTL_SECONDS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if records is None:
        _cache_set("summary", payload)
    return payload


def compute_industries(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    cached = _cache_get("industries")
    if cached is not None and records is None:
        return cached

    rows = records if records is not None else get_airtable_records_cached()
    buckets: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        industry = str(r.get("industry") or r.get("Industry") or "Unknown").strip() or "Unknown"
        score = _score_of(r)
        buckets[industry].append(score if score is not None else 0.0)

    items = []
    for industry, scores in buckets.items():
        items.append({
            "industry": industry,
            "count": len(scores),
            "avg_lead_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        })
    items.sort(key=lambda x: (-x["count"], x["industry"]))
    payload = {
        "industries": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if records is None:
        _cache_set("industries", payload)
    return payload


def compute_trend(
    days: int = 30,
    records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    days = max(1, min(int(days or 30), 365))
    cache_key = f"trend_{days}"
    cached = _cache_get(cache_key)
    if cached is not None and records is None:
        return cached

    rows = records if records is not None else get_airtable_records_cached()
    now = datetime.now(timezone.utc)
    # Build day buckets for last N days
    day_keys: List[str] = []
    for i in range(days - 1, -1, -1):
        d = now.timestamp() - i * 86400
        day_keys.append(datetime.fromtimestamp(d, tz=timezone.utc).strftime("%Y-%m-%d"))

    counts = {k: 0 for k in day_keys}
    score_sums = {k: 0.0 for k in day_keys}
    score_ns = {k: 0 for k in day_keys}

    for r in rows:
        ts = _created_ts(r)
        if ts is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        if day not in counts:
            continue
        counts[day] += 1
        score = _score_of(r)
        if score is not None:
            score_sums[day] += score
            score_ns[day] += 1

    series = []
    for day in day_keys:
        n = score_ns[day]
        series.append({
            "date": day,
            "leads": counts[day],
            "avg_score": round(score_sums[day] / n, 2) if n else 0.0,
        })

    payload = {
        "days": days,
        "series": series,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Trend uses Airtable record createdTime when available; "
            "days with no creates show zero."
        ),
    }
    if records is None:
        _cache_set(cache_key, payload)
    return payload


def compute_funnel() -> Dict[str, Any]:
    cached = _cache_get("funnel")
    if cached is not None:
        return cached
    summary = funnel_summary()
    # Optional Airtable cross-check for pushed count
    try:
        airtable_total = len(get_airtable_records_cached())
    except Exception:
        airtable_total = None
    summary["airtable_pushed_count"] = airtable_total
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    _cache_set("funnel", summary, ttl=30)  # funnel can refresh a bit faster
    return summary


def compute_recent_activity(limit: int = 20) -> Dict[str, Any]:
    """Prefer Airtable leads; fall back to funnel log for pipeline activity."""
    cached = _cache_get(f"recent_{limit}")
    if cached is not None:
        return cached

    rows = get_airtable_records_cached()
    enriched = []
    for r in rows:
        ts = _created_ts(r)
        enriched.append({
            "company_name": r.get("company_name") or r.get("Name") or "",
            "lead_score": _score_of(r),
            "status_tag": _normalize_status(r.get("status_tag") or r.get("Status")),
            "industry": r.get("industry") or r.get("Industry") or "",
            "timestamp": (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                if ts is not None
                else ""
            ),
            "source": "airtable",
        })
    enriched.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    activity = enriched[:limit]

    if len(activity) < limit:
        for row in recent_funnel_activity(limit):
            activity.append({
                "company_name": row.get("company_name") or "",
                "lead_score": row.get("lead_score"),
                "status_tag": row.get("status_tag") or row.get("stage_reached") or "",
                "industry": row.get("industry") or "",
                "timestamp": row.get("timestamp") or "",
                "source": "funnel",
                "stage_reached": row.get("stage_reached"),
                "failure_reason": row.get("failure_reason"),
            })
        # de-dupe by company+timestamp roughly
        seen = set()
        deduped = []
        for a in activity:
            key = (a["company_name"], a["timestamp"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(a)
        activity = deduped[:limit]

    payload = {
        "activity": activity,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(f"recent_{limit}", payload)
    return payload
