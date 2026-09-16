"""Weighted lead scoring based on industry, size, B2B fit, buying signals, and learned weights."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

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

# Cached learned weights (refreshed from data/scoring_weights.json)
_weights_cache: Optional[Dict[str, Any]] = None


def reload_scoring_weights() -> Dict[str, Any]:
    """Reload dynamic weights from JSON (call after feedback loop runs)."""
    global _weights_cache
    try:
        from services.feedback_loop import load_scoring_weights

        _weights_cache = load_scoring_weights()
    except Exception as exc:  # noqa: BLE001 — scoring must not fail closed
        logger.warning("Could not load dynamic scoring weights: %s", exc)
        _weights_cache = {}
    return _weights_cache or {}


def get_scoring_weights() -> Dict[str, Any]:
    global _weights_cache
    if _weights_cache is None:
        return reload_scoring_weights()
    return _weights_cache


def _industry_points(industry: str, weights: Optional[Dict[str, Any]] = None) -> int:
    """
    Base industry points, optionally scaled by learned win-rate multiplier.

    If feedback data has a win rate for this industry:
      points = round(30 * win_rate)   # e.g. Tech 0.8 → 24
    Else fall back to static HIGH/MEDIUM buckets, then apply multiplier if present.
    """
    w = weights if weights is not None else get_scoring_weights()
    rates = (w or {}).get("industry_win_rates") or {}
    mults = (w or {}).get("industry_multipliers") or {}

    if industry in rates:
        base = int(round(30 * float(rates[industry])))
        return max(0, min(30, base))

    if industry in HIGH_FIT_INDUSTRIES:
        base = 30
    elif industry in MEDIUM_FIT_INDUSTRIES:
        base = 18
    elif industry and industry != "Other" and industry.lower() != "unknown":
        base = 8
    else:
        base = 0

    if industry in mults and base > 0:
        scaled = int(round(base * float(mults[industry])))
        return max(0, min(40, scaled))  # allow slight boost above 30 via multiplier
    return base


def _size_points(size_estimate: str, weights: Optional[Dict[str, Any]] = None) -> int:
    w = weights if weights is not None else get_scoring_weights()
    rates = (w or {}).get("size_win_rates") or {}
    mults = (w or {}).get("size_multipliers") or {}
    size = str(size_estimate).strip()

    if size in rates:
        return max(0, min(40, int(round(40 * float(rates[size])))))

    base = SIZE_POINTS.get(size, 5)
    if size in mults and base > 0:
        return max(0, min(50, int(round(base * float(mults[size])))))
    return base


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


def compute_weighted_lead_score(
    analysis: Dict[str, Any],
    weights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute a weighted lead score (1–10) from analysis fields.

    Weighting (≈100 raw points → scaled to 1–10):
      - Industry fit: up to 30 (or learned win-rate × 30)
      - Company size: up to 40 (or learned win-rate × 40)
      - B2B buyer: 20
      - Buying signals: 10

    Pass `weights` to re-score with a specific feedback snapshot.
    """
    w = weights if weights is not None else get_scoring_weights()
    industry = str(analysis.get("industry") or "").strip()
    size = str(analysis.get("size_estimate") or "").strip()
    b2b = analysis.get("b2b_buyer", False)
    signal_pts, signals = _signal_points(analysis.get("buying_signals"))

    ind_pts = _industry_points(industry, w)
    size_pts = _size_points(size, w)
    b2b_pts = _b2b_points(b2b)
    raw = ind_pts + size_pts + b2b_pts + signal_pts

    lead_score = max(1, min(10, round(raw / 10))) if raw > 0 else 1

    status = _status_tag(lead_score, size, bool(b2b), len(signals))
    reason_parts = []
    if ind_pts:
        rate = ((w or {}).get("industry_win_rates") or {}).get(industry)
        if rate is not None:
            reason_parts.append(f"industry={industry} win_rate={rate:.2f} (+{ind_pts})")
        else:
            reason_parts.append(f"industry={industry} (+{ind_pts})")
    if size_pts:
        rate = ((w or {}).get("size_win_rates") or {}).get(size)
        if rate is not None:
            reason_parts.append(f"size={size} win_rate={rate:.2f} (+{size_pts})")
        else:
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
    # Strip prior "Weighted:" suffix when re-scoring
    if " | Weighted:" in ai_reason:
        ai_reason = ai_reason.split(" | Weighted:")[0].strip()
    if ai_reason.startswith("Weighted score from"):
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
        "industry_win_rate": ((w or {}).get("industry_win_rates") or {}).get(industry),
        "size_win_rate": ((w or {}).get("size_win_rates") or {}).get(size),
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
