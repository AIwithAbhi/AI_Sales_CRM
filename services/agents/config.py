"""Environment-driven config for marketing agents."""

from __future__ import annotations

import os
from typing import Any, Dict


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_key_usable(name: str) -> bool:
    value = (os.getenv(name) or "").strip()
    if not value:
        return False
    low = value.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return False
    return True


def agents_enabled() -> bool:
    return _env_bool("AGENTS_ENABLED", True)


def agents_db_path() -> str:
    return (os.getenv("AGENTS_DB_PATH") or "data/agents.db").strip()


def jobs_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "jobs")


def outreach_score_threshold() -> int:
    return _env_int("OUTREACH_SCORE_THRESHOLD", 8)


def call_score_threshold() -> int:
    return _env_int("CALL_SCORE_THRESHOLD", 9)


def content_lookback_days() -> int:
    return _env_int("CONTENT_LOOKBACK_DAYS", 14)


def skip_review_needed() -> bool:
    """When True, review_needed leads are excluded from outreach drafts."""
    return _env_bool("OUTREACH_SKIP_REVIEW_NEEDED", True)


def vapi_api_key() -> str:
    return (os.getenv("VAPI_API_KEY") or "").strip()


def vapi_assistant_id() -> str:
    return (os.getenv("VAPI_ASSISTANT_ID") or "").strip()


def vapi_phone_number_id() -> str:
    return (os.getenv("VAPI_PHONE_NUMBER_ID") or "").strip()


def vapi_configured() -> bool:
    return _env_key_usable("VAPI_API_KEY") and bool(vapi_assistant_id())


def public_config() -> Dict[str, Any]:
    """Safe config for the API/UI (no secrets)."""
    return {
        "agents_enabled": agents_enabled(),
        "outreach_score_threshold": outreach_score_threshold(),
        "call_score_threshold": call_score_threshold(),
        "content_lookback_days": content_lookback_days(),
        "skip_review_needed": skip_review_needed(),
        "vapi_configured": vapi_configured(),
        "vapi_assistant_configured": bool(vapi_assistant_id()),
    }
