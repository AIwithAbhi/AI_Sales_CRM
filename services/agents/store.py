"""SQLite store for approval-queue drafts and call-trigger events."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from services.agents import config as agent_config

_LOCK = threading.Lock()
_INITIALIZED = False


def _connect() -> sqlite3.Connection:
    path = agent_config.agents_db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    with _LOCK:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    global _INITIALIZED
    with _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                lead_key TEXT,
                job_id TEXT,
                company_name TEXT,
                title TEXT,
                subject TEXT,
                body TEXT NOT NULL,
                lead_snapshot TEXT,
                context_summary TEXT,
                replied INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
            CREATE INDEX IF NOT EXISTS idx_drafts_lead_key ON drafts(lead_key);
            CREATE INDEX IF NOT EXISTS idx_drafts_type ON drafts(type);

            CREATE TABLE IF NOT EXISTS processed_leads (
                lead_key TEXT PRIMARY KEY,
                job_id TEXT,
                company_name TEXT,
                processed_at REAL NOT NULL,
                draft_id TEXT
            );

            CREATE TABLE IF NOT EXISTS call_events (
                id TEXT PRIMARY KEY,
                lead_key TEXT NOT NULL,
                company_name TEXT,
                phone TEXT,
                rule TEXT NOT NULL,
                reason TEXT,
                vapi_call_id TEXT,
                status TEXT NOT NULL,
                error TEXT,
                metadata TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_call_events_lead ON call_events(lead_key);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_call_events_unique
                ON call_events(lead_key, rule);
            """
        )
    _INITIALIZED = True


def ensure_db() -> None:
    if not _INITIALIZED:
        init_db()


def make_lead_key(company_name: str, url: str = "", job_id: str = "") -> str:
    host = (url or "").strip().lower().rstrip("/")
    name = (company_name or "").strip().lower()
    jid = (job_id or "").strip()
    return f"{name}|{host}|{jid}"


def _row_to_draft(row: sqlite3.Row) -> Dict[str, Any]:
    snap = row["lead_snapshot"]
    try:
        lead_snapshot = json.loads(snap) if snap else None
    except json.JSONDecodeError:
        lead_snapshot = None
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "lead_key": row["lead_key"],
        "job_id": row["job_id"],
        "company_name": row["company_name"],
        "title": row["title"] or "",
        "subject": row["subject"] or "",
        "body": row["body"] or "",
        "lead_snapshot": lead_snapshot,
        "context_summary": row["context_summary"] or "",
        "replied": bool(row["replied"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_draft(
    *,
    draft_type: str,
    body: str,
    subject: str = "",
    title: str = "",
    company_name: str = "",
    lead_key: str = "",
    job_id: str = "",
    lead_snapshot: Optional[Dict[str, Any]] = None,
    context_summary: str = "",
    status: str = "pending",
) -> Dict[str, Any]:
    ensure_db()
    now = time.time()
    draft_id = str(uuid.uuid4())
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO drafts (
                id, type, status, lead_key, job_id, company_name, title, subject,
                body, lead_snapshot, context_summary, replied, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                draft_id,
                draft_type,
                status,
                lead_key or None,
                job_id or None,
                company_name or None,
                title or None,
                subject or None,
                body,
                json.dumps(lead_snapshot or {}, ensure_ascii=False),
                context_summary or None,
                now,
                now,
            ),
        )
        if lead_key and draft_type == "outreach":
            conn.execute(
                """
                INSERT INTO processed_leads (lead_key, job_id, company_name, processed_at, draft_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(lead_key) DO UPDATE SET
                    processed_at=excluded.processed_at,
                    draft_id=excluded.draft_id
                """,
                (lead_key, job_id or None, company_name or None, now, draft_id),
            )
    return get_draft(draft_id)  # type: ignore[return-value]


def get_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    ensure_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    return _row_to_draft(row) if row else None


def list_drafts(
    *,
    status: Optional[str] = None,
    draft_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    ensure_db()
    clauses: List[str] = []
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if draft_type:
        clauses.append("type = ?")
        params.append(draft_type)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(int(limit), 500)))
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM drafts {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_draft(r) for r in rows]


def update_draft(
    draft_id: str,
    *,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    title: Optional[str] = None,
    status: Optional[str] = None,
    replied: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    ensure_db()
    draft = get_draft(draft_id)
    if not draft:
        return None
    new_subject = draft["subject"] if subject is None else subject
    new_body = draft["body"] if body is None else body
    new_title = draft["title"] if title is None else title
    new_status = draft["status"] if status is None else status
    new_replied = draft["replied"] if replied is None else bool(replied)
    now = time.time()
    with _db() as conn:
        conn.execute(
            """
            UPDATE drafts
            SET subject = ?, body = ?, title = ?, status = ?, replied = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_subject, new_body, new_title, new_status, int(new_replied), now, draft_id),
        )
    return get_draft(draft_id)


def is_lead_processed(lead_key: str) -> bool:
    ensure_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_leads WHERE lead_key = ?", (lead_key,)
        ).fetchone()
    return row is not None


def mark_lead_processed(
    lead_key: str,
    *,
    job_id: str = "",
    company_name: str = "",
    draft_id: str = "",
) -> None:
    ensure_db()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO processed_leads (lead_key, job_id, company_name, processed_at, draft_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(lead_key) DO UPDATE SET
                processed_at=excluded.processed_at,
                draft_id=COALESCE(excluded.draft_id, processed_leads.draft_id)
            """,
            (lead_key, job_id or None, company_name or None, time.time(), draft_id or None),
        )


def has_call_event(lead_key: str, rule: str) -> bool:
    ensure_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM call_events WHERE lead_key = ? AND rule = ?",
            (lead_key, rule),
        ).fetchone()
    return row is not None


def insert_call_event(
    *,
    lead_key: str,
    rule: str,
    company_name: str = "",
    phone: str = "",
    reason: str = "",
    vapi_call_id: str = "",
    status: str = "triggered",
    error: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_db()
    event_id = str(uuid.uuid4())
    now = time.time()
    with _db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO call_events (
                    id, lead_key, company_name, phone, rule, reason,
                    vapi_call_id, status, error, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    lead_key,
                    company_name or None,
                    phone or None,
                    rule,
                    reason or None,
                    vapi_call_id or None,
                    status,
                    error or None,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT * FROM call_events WHERE lead_key = ? AND rule = ?",
                (lead_key, rule),
            ).fetchone()
            return _row_to_call(row) if row else {"id": event_id, "duplicate": True}
    return get_call_event(event_id)  # type: ignore[return-value]


def get_call_event(event_id: str) -> Optional[Dict[str, Any]]:
    ensure_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM call_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_call(row) if row else None


def _row_to_call(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
    except json.JSONDecodeError:
        meta = {}
    phone = row["phone"] or ""
    masked = phone
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) >= 4:
        masked = ("*" * max(0, len(digits) - 4)) + digits[-4:]
        if phone.startswith("+"):
            masked = "+" + masked
    return {
        "id": row["id"],
        "lead_key": row["lead_key"],
        "company_name": row["company_name"] or "",
        "phone": phone,
        "phone_masked": masked,
        "rule": row["rule"],
        "reason": row["reason"] or "",
        "vapi_call_id": row["vapi_call_id"] or "",
        "status": row["status"],
        "error": row["error"] or "",
        "metadata": meta,
        "created_at": row["created_at"],
    }


def list_call_events(limit: int = 100) -> List[Dict[str, Any]]:
    ensure_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM call_events ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [_row_to_call(r) for r in rows]


def list_replied_outreach() -> List[Dict[str, Any]]:
    """Approved outreach drafts marked as replied (feeds Call Trigger)."""
    ensure_db()
    with _db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM drafts
            WHERE type = 'outreach'
              AND status = 'approved_ready_to_send'
              AND replied = 1
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [_row_to_draft(r) for r in rows]
