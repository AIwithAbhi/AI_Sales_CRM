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


def _numeric_field(rec: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        val = rec.get(key)
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _enterprise_tier(rec: Dict[str, Any]) -> str:
    raw = (
        rec.get("enterprise_readiness_tier")
        or rec.get("Enterprise Readiness Tier")
        or ""
    )
    text = str(raw).strip()
    low = text.lower()
    if low == "high":
        return "High"
    if low == "medium":
        return "Medium"
    if low == "low":
        return "Low"
    return text or "Unknown"


def _scorecard_confidence(rec: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = str(rec.get(key) or "").strip().lower()
        if text in ("high", "medium", "low"):
            return text
    return ""


def _has_low_scorecard_confidence(rec: Dict[str, Any]) -> bool:
    """True when either maturity or transformation confidence is low."""
    ai = _scorecard_confidence(
        rec, "ai_maturity_confidence", "AI Maturity Confidence"
    )
    tr = _scorecard_confidence(
        rec,
        "transformation_readiness_confidence",
        "Transformation Readiness Confidence",
    )
    return ai == "low" or tr == "low"


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
    maturity_sum = 0.0
    maturity_n = 0
    readiness_sum = 0.0
    readiness_n = 0
    tier_high = tier_medium = tier_low = tier_unknown = 0
    low_conf_high = low_conf_medium = low_conf_low = low_conf_unknown = 0
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
        maturity = _numeric_field(
            r, "ai_maturity_score", "AI Maturity Score"
        )
        if maturity is not None:
            maturity_sum += maturity
            maturity_n += 1
        readiness = _numeric_field(
            r,
            "transformation_readiness_score",
            "Transformation Readiness Score",
        )
        if readiness is not None:
            readiness_sum += readiness
            readiness_n += 1
        tier = _enterprise_tier(r)
        low_conf = _has_low_scorecard_confidence(r)
        if tier == "High":
            tier_high += 1
            if low_conf:
                low_conf_high += 1
        elif tier == "Medium":
            tier_medium += 1
            if low_conf:
                low_conf_medium += 1
        elif tier == "Low":
            tier_low += 1
            if low_conf:
                low_conf_low += 1
        else:
            tier_unknown += 1
            if low_conf:
                low_conf_unknown += 1

    def pct(n: int) -> float:
        return round((n / total) * 100.0, 1) if total else 0.0

    def tier_bucket(count: int, low_conf: int) -> Dict[str, Any]:
        return {
            "count": count,
            "pct": pct(count),
            "low_confidence_count": low_conf,
            "needs_deeper_research": low_conf > 0,
        }

    payload = {
        "total_leads": total,
        "hot": {"count": hot, "pct": pct(hot)},
        "warm": {"count": warm, "pct": pct(warm)},
        "cold": {"count": cold, "pct": pct(cold)},
        "unknown": {"count": unknown, "pct": pct(unknown)},
        "avg_lead_score": round(score_sum / score_n, 2) if score_n else 0.0,
        "scored_count": score_n,
        "avg_ai_maturity": (
            round(maturity_sum / maturity_n, 2) if maturity_n else 0.0
        ),
        "avg_transformation_readiness": (
            round(readiness_sum / readiness_n, 2) if readiness_n else 0.0
        ),
        "enterprise_readiness": {
            "high": tier_bucket(tier_high, low_conf_high),
            "medium": tier_bucket(tier_medium, low_conf_medium),
            "low": tier_bucket(tier_low, low_conf_low),
            "unknown": tier_bucket(tier_unknown, low_conf_unknown),
        },
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
    buckets: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: {"lead": [], "maturity": [], "readiness": []}
    )
    for r in rows:
        industry = str(r.get("industry") or r.get("Industry") or "Unknown").strip() or "Unknown"
        score = _score_of(r)
        buckets[industry]["lead"].append(score if score is not None else 0.0)
        maturity = _numeric_field(r, "ai_maturity_score", "AI Maturity Score")
        if maturity is not None:
            buckets[industry]["maturity"].append(maturity)
        readiness = _numeric_field(
            r,
            "transformation_readiness_score",
            "Transformation Readiness Score",
        )
        if readiness is not None:
            buckets[industry]["readiness"].append(readiness)

    items = []
    for industry, data in buckets.items():
        leads = data["lead"]
        mats = data["maturity"]
        reads = data["readiness"]
        items.append({
            "industry": industry,
            "count": len(leads),
            "avg_lead_score": round(sum(leads) / len(leads), 2) if leads else 0.0,
            "avg_ai_maturity": (
                round(sum(mats) / len(mats), 2) if mats else 0.0
            ),
            "avg_transformation_readiness": (
                round(sum(reads) / len(reads), 2) if reads else 0.0
            ),
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
        ai_conf = _scorecard_confidence(
            r, "ai_maturity_confidence", "AI Maturity Confidence"
        )
        tr_conf = _scorecard_confidence(
            r,
            "transformation_readiness_confidence",
            "Transformation Readiness Confidence",
        )
        enriched.append({
            "company_name": r.get("company_name") or r.get("Name") or "",
            "lead_score": _score_of(r),
            "status_tag": _normalize_status(r.get("status_tag") or r.get("Status")),
            "industry": r.get("industry") or r.get("Industry") or "",
            "enterprise_readiness_tier": _enterprise_tier(r),
            "ai_maturity_confidence": ai_conf,
            "transformation_readiness_confidence": tr_conf,
            "low_confidence": _has_low_scorecard_confidence(r),
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
