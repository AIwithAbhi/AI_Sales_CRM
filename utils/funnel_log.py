"""Durable funnel stage logging for sales search pipeline (SQLite)."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "funnel.db")

STAGES = (
    "uploaded",
    "scraped",
    "scored",
    "pushed",
    "skipped_duplicate",
    "failed",
)

_STAGE_RANK = {
    "uploaded": 1,
    "scraped": 2,
    "scored": 3,
    "pushed": 5,
    "skipped_duplicate": 5,
    "failed": 5,
}

_lock = threading.Lock()
_initialized = False


def _db_path() -> str:
    return os.getenv("FUNNEL_DB_PATH", DEFAULT_DB)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_funnel_db() -> None:
    global _initialized
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS funnel_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    stage_reached TEXT NOT NULL,
                    failure_reason TEXT,
                    lead_score INTEGER,
                    status_tag TEXT,
                    industry TEXT,
                    UNIQUE(run_id, company_name)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_funnel_stage ON funnel_events(stage_reached)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_funnel_ts ON funnel_events(timestamp)"
            )
            conn.commit()
            _initialized = True
        finally:
            conn.close()


def _ensure_init() -> None:
    if not _initialized:
        init_funnel_db()


def log_funnel_stage(
    company_name: str,
    run_id: str,
    stage_reached: str,
    *,
    failure_reason: Optional[str] = None,
    lead_score: Optional[int] = None,
    status_tag: Optional[str] = None,
    industry: Optional[str] = None,
) -> None:
    """Upsert one row per (run_id, company_name). Never raises into the pipeline."""
    if not company_name or not run_id or stage_reached not in _STAGE_RANK:
        return
    try:
        _ensure_init()
        now = datetime.now(timezone.utc).isoformat()
        name = company_name.strip()
        with _lock:
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT stage_reached FROM funnel_events WHERE run_id=? AND company_name=?",
                    (run_id, name),
                ).fetchone()
                if row:
                    prev = row["stage_reached"]
                    if _STAGE_RANK[stage_reached] < _STAGE_RANK.get(prev, 0):
                        return
                    conn.execute(
                        """
                        UPDATE funnel_events
                        SET timestamp=?, stage_reached=?, failure_reason=?,
                            lead_score=COALESCE(?, lead_score),
                            status_tag=COALESCE(?, status_tag),
                            industry=COALESCE(?, industry)
                        WHERE run_id=? AND company_name=?
                        """,
                        (
                            now,
                            stage_reached,
                            failure_reason if stage_reached == "failed" else None,
                            lead_score,
                            status_tag,
                            industry,
                            run_id,
                            name,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO funnel_events
                        (company_name, run_id, timestamp, stage_reached, failure_reason,
                         lead_score, status_tag, industry)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            name,
                            run_id,
                            now,
                            stage_reached,
                            failure_reason if stage_reached == "failed" else None,
                            lead_score,
                            status_tag,
                            industry,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[funnel_log] warning: {exc}")


def _failed_before_scrape(reason: Optional[str]) -> bool:
    low = (reason or "").lower()
    return any(
        token in low
        for token in (
            "website not found",
            "url validation",
            "no valid company url",
            "failed to scrape",
            "scrape failed",
        )
    )


def funnel_summary() -> Dict[str, Any]:
    """Conversion funnel from logged stages (one row per company per run)."""
    _ensure_init()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT stage_reached, failure_reason FROM funnel_events"
            ).fetchall()
        finally:
            conn.close()

    uploaded = len(rows)
    scraped = 0
    scored = 0
    pushed = 0
    skipped = 0
    failed = 0
    failure_reasons: Dict[str, int] = {}

    for r in rows:
        stage = r["stage_reached"]
        reason = r["failure_reason"]
        if stage == "failed":
            failed += 1
            key = (reason or "unknown").strip() or "unknown"
            failure_reasons[key] = failure_reasons.get(key, 0) + 1
            if not _failed_before_scrape(reason):
                scraped += 1
            continue
        if stage in ("scraped", "scored", "pushed", "skipped_duplicate"):
            scraped += 1
        if stage in ("scored", "pushed", "skipped_duplicate"):
            scored += 1
        if stage == "pushed":
            pushed += 1
        if stage == "skipped_duplicate":
            skipped += 1
        if stage == "uploaded":
            pass

    def dropoff(prev: int, curr: int) -> float:
        if prev <= 0:
            return 0.0
        return round(max(0.0, (prev - curr) / prev * 100.0), 1)

    stages = [
        {"key": "uploaded", "label": "Uploaded", "count": uploaded, "dropoff_pct": 0.0},
        {
            "key": "scraped",
            "label": "Successfully scraped",
            "count": scraped,
            "dropoff_pct": dropoff(uploaded, scraped),
        },
        {
            "key": "scored",
            "label": "Successfully scored",
            "count": scored,
            "dropoff_pct": dropoff(scraped, scored),
        },
        {
            "key": "pushed",
            "label": "Pushed to Airtable",
            "count": pushed,
            "dropoff_pct": dropoff(scored, pushed),
        },
        {
            "key": "skipped_duplicate",
            "label": "Skipped as duplicates",
            "count": skipped,
            "dropoff_pct": 0.0,
        },
        {
            "key": "failed",
            "label": "Failed",
            "count": failed,
            "dropoff_pct": 0.0,
        },
    ]
    return {
        "stages": stages,
        "failure_reasons": [
            {"reason": k, "count": v}
            for k, v in sorted(failure_reasons.items(), key=lambda x: -x[1])
        ],
        "total_logged": uploaded,
    }


def recent_funnel_activity(limit: int = 20) -> List[Dict[str, Any]]:
    _ensure_init()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT company_name, run_id, timestamp, stage_reached, failure_reason,
                       lead_score, status_tag, industry
                FROM funnel_events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


try:
    init_funnel_db()
except Exception as _exc:  # noqa: BLE001
    print(f"[funnel_log] init deferred: {_exc}")
