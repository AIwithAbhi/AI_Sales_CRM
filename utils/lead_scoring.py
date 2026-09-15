"""Weighted lead scoring based on industry, size, B2B fit, and buying signals."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Industry fit: max 30
HIGH_FIT_INDUSTRIES = {"Technology", "Manufacturing", "Energy"}
MEDIUM_FIT_INDUSTRIES = {"Finance", "Healthcare", "Consulting"}

# Size bands used by AI + Airtable validation
SIZE_BANDS = ("1-50", "51-200", "201-500", "501-1000", "1001+")

# Size score: max 40
SIZE_POINTS = {
    "1-50": 10,
    "51-200": 20,
    "201-500": 30,
    "501-1000": 35,
    "1001+": 40,
    # Legacy labels from older runs
    "Small": 10,
    "Medium": 25,
    "High": 40,
}

ENTERPRISE_SIZES = {"501-1000", "1001+", "High"}
MID_MARKET_SIZES = {"51-200", "201-500", "Medium"}


def _industry_points(industry: str) -> int:
    if industry in HIGH_FIT_INDUSTRIES:
        return 30
    if industry in MEDIUM_FIT_INDUSTRIES:
        return 18
    if industry and industry != "Other" and industry.lower() != "unknown":
        return 8
    return 0


def _size_points(size_estimate: str) -> int:
    return SIZE_POINTS.get(str(size_estimate).strip(), 5)


def _b2b_points(b2b_buyer: Any) -> int:
    return 20 if bool(b2b_buyer) else 0


def _signal_points(buying_signals: Any) -> Tuple[int, List[str]]:
    """1 signal = 3 points, max 10."""
    if not buying_signals:
        return 0, []
    if isinstance(buying_signals, str):
        signals = [s.strip() for s in buying_signals.split(",") if s.strip()]
    elif isinstance(buying_signals, list):
        signals = [str(s).strip() for s in buying_signals if str(s).strip()]
    else:
        signals = []
    points = min(10, len(signals) * 3)
    return points, signals


def compute_weighted_lead_score(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute a weighted lead score (1–10) from analysis fields.

    Weighting (100 raw points → scaled to 1–10):
      - Industry fit: 30
      - Company size: 40
      - B2B buyer: 20
      - Buying signals: 10

    Returns dict with lead_score, status_tag, score_breakdown, score_reason.
    """
    industry = str(analysis.get("industry") or "").strip()
    size = str(analysis.get("size_estimate") or "").strip()
    b2b = analysis.get("b2b_buyer", False)
    signal_pts, signals = _signal_points(analysis.get("buying_signals"))

    ind_pts = _industry_points(industry)
    size_pts = _size_points(size)
    b2b_pts = _b2b_points(b2b)
    raw = ind_pts + size_pts + b2b_pts + signal_pts

    # Scale 0–100 → 1–10 (floor at 1 if any data present)
    lead_score = max(1, min(10, round(raw / 10))) if raw > 0 else 1

    status = _status_tag(lead_score, size, bool(b2b), len(signals))
    reason_parts = []
    if ind_pts:
        reason_parts.append(f"industry={industry} (+{ind_pts})")
    if size_pts:
        reason_parts.append(f"size={size} (+{size_pts})")
    if b2b_pts:
        reason_parts.append("B2B buyer (+20)")
    if signals:
        reason_parts.append(f"signals={len(signals)} (+{signal_pts})")

    ai_reason = str(
        analysis.get("lead_score_rationale")
        or analysis.get("score_reason")
        or ""
    ).strip()
    if ai_reason.lower() == "not stated on website":
        ai_reason = ""
    score_reason = (
        f"{ai_reason} | Weighted: {', '.join(reason_parts)} → {lead_score}/10"
        if ai_reason
        else f"Weighted score from {', '.join(reason_parts) or 'limited signals'} → {lead_score}/10"
    )

    breakdown = {
        "industry_points": ind_pts,
        "size_points": size_pts,
        "b2b_points": b2b_pts,
        "signal_points": signal_pts,
        "raw_total": raw,
        "buying_signals": signals,
    }

    logger.info(
        "Lead score for industry=%s size=%s b2b=%s signals=%s → %s (%s)",
        industry,
        size,
        b2b,
        len(signals),
        lead_score,
        status,
    )

    return {
        "lead_score": lead_score,
        "status_tag": status,
        "score_breakdown": breakdown,
        "score_reason": score_reason,
        "buying_signals": signals,
    }


def _status_tag(lead_score: int, size: str, b2b: bool, signal_count: int) -> str:
    """
    Hot (8–10): Enterprise + B2B + 2+ signals, or score ≥ 8 with strong fit.
    Warm (5–7): Mid-market or good fit.
    Cold (1–4): Poor match.
    """
    is_enterprise = size in ENTERPRISE_SIZES

    if lead_score >= 8:
        if b2b and (is_enterprise or signal_count >= 2):
            return "Hot"
        return "Hot" if lead_score >= 8 else "Warm"
    if lead_score >= 5:
        return "Warm"
    if lead_score >= 1:
        return "Cold"
    return "Unknown"
