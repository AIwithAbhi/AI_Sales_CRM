"""Parameterized cold email generation after lead scoring."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "cold_email.json"

_ENV_OVERRIDES = {
    "product_name": "COLD_EMAIL_PRODUCT_NAME",
    "sender_name": "COLD_EMAIL_SENDER_NAME",
    "default_first_name": "COLD_EMAIL_DEFAULT_FIRST_NAME",
    "subject_format": "COLD_EMAIL_SUBJECT_FORMAT",
    "cta": "COLD_EMAIL_CTA",
    "body_template": "COLD_EMAIL_BODY_TEMPLATE",
    "pain_point_default": "COLD_EMAIL_PAIN_POINT_DEFAULT",
    "impact_pct": "COLD_EMAIL_IMPACT_PCT",
    "csv_path": "COLD_EMAIL_CSV_PATH",
    "status_draft": "COLD_EMAIL_STATUS",
}

CSV_COLUMNS = ["Company", "Email", "Lead Score", "Status", "Send Time"]


def _config_path() -> Path:
    override = (os.getenv("COLD_EMAIL_CONFIG_PATH") or "").strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else ROOT / path
    return DEFAULT_CONFIG_PATH


def load_cold_email_config() -> Dict[str, Any]:
    """
    Load cold-email settings from config/cold_email.json, then apply .env overrides.

    Edit the JSON (or env vars) to change subject format, product name, CTA, etc.
    without changing Python code.
    """
    path = _config_path()
    config: Dict[str, Any] = {}
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config = loaded
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load cold email config from %s: %s", path, exc)
    else:
        logger.warning("Cold email config not found at %s — using defaults/env", path)

    defaults = {
        "product_name": "Your Product",
        "sender_name": "Your Name",
        "default_first_name": "there",
        "subject_format": "{company_name} + {signal}",
        "cta": "Quick question: Is {buying_signal} on your roadmap this year?",
        "body_template": (
            "Hi {first_name},\n\n"
            "I noticed {company_name} is {buying_signal} — that's exactly where "
            "companies in {industry} are struggling.\n\n"
            "{product_name} helps {industry} companies reduce {pain_point} by {impact_pct}%.\n\n"
            "{cta}\n\n"
            "{sender_name}"
        ),
        "pain_point_default": "operational friction",
        "pain_point_by_signal": {},
        "impact_pct": "30",
        "csv_path": "data/cold_emails.csv",
        "send_delay_hours": 0,
        "status_draft": "Draft",
        "skip_on_error": True,
        "min_lead_score": 1,
    }
    merged = {**defaults, **config}

    for key, env_name in _ENV_OVERRIDES.items():
        val = os.getenv(env_name)
        if val is not None and str(val).strip() != "":
            merged[key] = val.strip()

    delay = os.getenv("COLD_EMAIL_SEND_DELAY_HOURS")
    if delay is not None and str(delay).strip() != "":
        try:
            merged["send_delay_hours"] = float(delay)
        except ValueError:
            pass

    min_score = os.getenv("COLD_EMAIL_MIN_LEAD_SCORE")
    if min_score is not None and str(min_score).strip() != "":
        try:
            merged["min_lead_score"] = int(min_score)
        except ValueError:
            pass

    return merged


def extract_top_buying_signals(analysis: Dict[str, Any], limit: int = 2) -> List[str]:
    """Return up to `limit` buying signals from AI analysis / scored result."""
    raw = analysis.get("buying_signals") or []
    if isinstance(raw, str):
        parts = [p.strip() for p in re.split(r"[,;|]", raw) if p.strip()]
    elif isinstance(raw, list):
        parts = [str(s).strip() for s in raw if str(s).strip()]
    else:
        parts = []

    # Prefer concrete phrases; drop empties / duplicates (case-insensitive)
    seen = set()
    out: List[str] = []
    for signal in parts:
        key = signal.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(signal)
        if len(out) >= limit:
            break
    return out


def _guess_first_name(email: str, default: str) -> str:
    local = (email or "").split("@")[0].strip()
    if not local or local.lower() in ("info", "hello", "contact", "sales", "support", "admin"):
        return default
    token = re.split(r"[._+\-]", local)[0]
    if not token or not token.isalpha() or len(token) < 2:
        return default
    return token[:1].upper() + token[1:].lower()


def _resolve_pain_point(signal: str, config: Dict[str, Any]) -> str:
    mapping = config.get("pain_point_by_signal") or {}
    if not isinstance(mapping, dict):
        return str(config.get("pain_point_default") or "operational friction")
    lower = signal.lower()
    for key, value in mapping.items():
        if str(key).lower() in lower:
            return str(value)
    return str(config.get("pain_point_default") or "operational friction")


def _safe_format(template: str, values: Dict[str, str]) -> str:
    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return template.format_map(_Safe(values))
    except Exception:
        return template


def generate_cold_email(
    result: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build a personalized cold email from a scored company result.

    Returns None when the company should be skipped (errors / low score).
    """
    cfg = config or load_cold_email_config()
    if result.get("error") and cfg.get("skip_on_error", True):
        return None

    try:
        score = int(result.get("lead_score") or 0)
    except (TypeError, ValueError):
        score = 0
    min_score = int(cfg.get("min_lead_score") or 1)
    if score < min_score:
        return None

    signals = extract_top_buying_signals(result, limit=2)
    primary_signal = signals[0] if signals else "exploring growth initiatives"
    secondary_signal = signals[1] if len(signals) > 1 else ""

    company_name = str(result.get("company_name") or "your team").strip() or "your team"
    industry = str(result.get("industry") or "your industry").strip() or "your industry"
    email = str(result.get("email") or "").strip()
    status = str(result.get("status_tag") or cfg.get("status_draft") or "Draft")

    first_name = _guess_first_name(
        email, str(cfg.get("default_first_name") or "there")
    )
    pain_point = _resolve_pain_point(primary_signal, cfg)
    impact_pct = str(cfg.get("impact_pct") or "30").rstrip("%")

    values = {
        "company_name": company_name,
        "Company Name": company_name,
        "signal": primary_signal,
        "Signal": primary_signal,
        "buying_signal": primary_signal,
        "buying_signal_2": secondary_signal,
        "industry": industry,
        "Industry": industry,
        "first_name": first_name,
        "First Name": first_name,
        "product_name": str(cfg.get("product_name") or "Your Product"),
        "Your Product": str(cfg.get("product_name") or "Your Product"),
        "sender_name": str(cfg.get("sender_name") or "Your Name"),
        "Your Name": str(cfg.get("sender_name") or "Your Name"),
        "pain_point": pain_point,
        "impact_pct": impact_pct,
        "X": impact_pct,
        "cta": "",  # filled after first pass so nested CTA placeholders work
        "email": email,
        "lead_score": str(score),
        "status": status,
    }
    values["cta"] = _safe_format(str(cfg.get("cta") or ""), values)

    subject = _safe_format(str(cfg.get("subject_format") or "{company_name} + {signal}"), values)
    body = _safe_format(str(cfg.get("body_template") or ""), values)

    delay_hours = float(cfg.get("send_delay_hours") or 0)
    send_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
    send_time = send_at.strftime("%Y-%m-%d %H:%M UTC")

    return {
        "company": company_name,
        "email": email,
        "lead_score": score,
        "status": status if status else str(cfg.get("status_draft") or "Draft"),
        "send_time": send_time,
        "subject": subject,
        "body": body,
        "top_signals": signals,
        "first_name": first_name,
        "pain_point": pain_point,
    }


def cold_email_csv_path(config: Optional[Dict[str, Any]] = None) -> Path:
    cfg = config or load_cold_email_config()
    raw = str(cfg.get("csv_path") or "data/cold_emails.csv")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def append_cold_email_csv(
    draft: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Path:
    """Append one draft row to the cold-email CSV (creates file + header if needed)."""
    path = cold_email_csv_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.is_file() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "Company": draft.get("company", ""),
            "Email": draft.get("email", ""),
            "Lead Score": draft.get("lead_score", ""),
            "Status": draft.get("status", ""),
            "Send Time": draft.get("send_time", ""),
        })
    return path


def export_cold_emails_csv(
    drafts: List[Dict[str, Any]],
    path: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write (overwrite) a full cold-email CSV for a job or batch."""
    cfg = config or load_cold_email_config()
    out = path or cold_email_csv_path(cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for draft in drafts:
            writer.writerow({
                "Company": draft.get("company", ""),
                "Email": draft.get("email", ""),
                "Lead Score": draft.get("lead_score", ""),
                "Status": draft.get("status", ""),
                "Send Time": draft.get("send_time", ""),
            })
    return out


def attach_cold_email(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a cold email for a scored company, attach it to the result,
    and append a CSV row. Safe no-op when generation is skipped.
    """
    cfg = load_cold_email_config()
    draft = generate_cold_email(result, cfg)
    if not draft:
        result["cold_email"] = None
        return result

    try:
        csv_path = append_cold_email_csv(draft, cfg)
        draft["csv_path"] = str(csv_path)
    except OSError as exc:
        logger.warning("Failed to append cold email CSV: %s", exc)

    result["cold_email"] = {
        "subject": draft["subject"],
        "body": draft["body"],
        "top_signals": draft["top_signals"],
        "send_time": draft["send_time"],
        "status": draft["status"],
        "csv_row": {
            "Company": draft["company"],
            "Email": draft["email"],
            "Lead Score": draft["lead_score"],
            "Status": draft["status"],
            "Send Time": draft["send_time"],
        },
    }
    return result
