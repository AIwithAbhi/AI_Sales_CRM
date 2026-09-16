"""Airtable outcome feedback → dynamic scoring weights → re-rank leads."""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEIGHTS_PATH = ROOT / "data" / "scoring_weights.json"
DEFAULTS_TEMPLATE_PATH = ROOT / "config" / "scoring_weights.defaults.json"
FEEDBACK_LOG_PATH = ROOT / "logs" / "feedback_loop.log"

OUTCOME_WON = "Won"
OUTCOME_LOST = "Lost"
OUTCOME_CONTACTED = "Contacted"
OUTCOME_NO_RESPONSE = "No Response"
OUTCOME_LABELS = {OUTCOME_WON, OUTCOME_LOST, OUTCOME_CONTACTED, OUTCOME_NO_RESPONSE}

# Outcomes counted as decisive wins/losses for win-rate (Contacted = soft positive)
POSITIVE_OUTCOMES = {OUTCOME_WON}
NEGATIVE_OUTCOMES = {OUTCOME_LOST, OUTCOME_NO_RESPONSE}
SOFT_POSITIVE = {OUTCOME_CONTACTED}


def weights_path() -> Path:
    raw = (os.getenv("SCORING_WEIGHTS_PATH") or "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else ROOT / path
    return DEFAULT_WEIGHTS_PATH


def load_scoring_weights() -> Dict[str, Any]:
    path = weights_path()
    defaults: Dict[str, Any] = {
        "version": 1,
        "updated_at": None,
        "global_win_rate": None,
        "min_samples": int(os.getenv("FEEDBACK_MIN_SAMPLES", "5")),
        "prior_alpha": float(os.getenv("FEEDBACK_PRIOR_ALPHA", "1")),
        "prior_beta": float(os.getenv("FEEDBACK_PRIOR_BETA", "1")),
        "significant_rate_delta": float(os.getenv("FEEDBACK_RATE_DELTA", "0.1")),
        "score_shift_threshold": int(os.getenv("FEEDBACK_SCORE_SHIFT", "2")),
        "industry_win_rates": {},
        "industry_multipliers": {},
        "industry_samples": {},
        "size_win_rates": {},
        "size_multipliers": {},
        "size_samples": {},
        "previous": None,
        "last_run": None,
    }
    # Seed from committed defaults template when present
    if DEFAULTS_TEMPLATE_PATH.is_file():
        try:
            with open(DEFAULTS_TEMPLATE_PATH, encoding="utf-8") as f:
                template = json.load(f)
            if isinstance(template, dict):
                defaults = {**defaults, **template}
                # Env always wins for tunables
                defaults["min_samples"] = int(os.getenv("FEEDBACK_MIN_SAMPLES", str(defaults["min_samples"])))
                defaults["prior_alpha"] = float(os.getenv("FEEDBACK_PRIOR_ALPHA", str(defaults["prior_alpha"])))
                defaults["prior_beta"] = float(os.getenv("FEEDBACK_PRIOR_BETA", str(defaults["prior_beta"])))
                defaults["significant_rate_delta"] = float(
                    os.getenv("FEEDBACK_RATE_DELTA", str(defaults["significant_rate_delta"]))
                )
                defaults["score_shift_threshold"] = int(
                    os.getenv("FEEDBACK_SCORE_SHIFT", str(defaults["score_shift_threshold"]))
                )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load weights template: %s", exc)

    if not path.is_file():
        return defaults
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {**defaults, **data}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load scoring weights from %s: %s", path, exc)
    return defaults


def save_scoring_weights(weights: Dict[str, Any]) -> Path:
    path = weights_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(weights, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def normalize_outcome(record: Dict[str, Any]) -> Optional[str]:
    """
    Resolve pipeline outcome from Airtable.

    Prefer dedicated Outcome / Pipeline Status fields so Hot/Warm/Cold Status
    (lead temperature) is not confused with Won/Lost.
    """
    for key in (
        "Outcome",
        "outcome",
        "Pipeline Status",
        "pipeline_status",
        "Deal Status",
        "deal_status",
    ):
        raw = record.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        label = _canonical_outcome(str(raw))
        if label:
            return label

    # Fallback: Status only if it is an outcome label (not Hot/Warm/Cold)
    status = record.get("Status") or record.get("status_tag") or ""
    return _canonical_outcome(str(status))


def _canonical_outcome(raw: str) -> Optional[str]:
    text = raw.strip().lower().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    mapping = {
        "won": OUTCOME_WON,
        "win": OUTCOME_WON,
        "closed won": OUTCOME_WON,
        "lost": OUTCOME_LOST,
        "lose": OUTCOME_LOST,
        "closed lost": OUTCOME_LOST,
        "contacted": OUTCOME_CONTACTED,
        "contact": OUTCOME_CONTACTED,
        "no response": OUTCOME_NO_RESPONSE,
        "noresponse": OUTCOME_NO_RESPONSE,
        "no reply": OUTCOME_NO_RESPONSE,
        "unresponsive": OUTCOME_NO_RESPONSE,
    }
    return mapping.get(text)


def group_by_outcome(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {label: [] for label in OUTCOME_LABELS}
    groups["Unknown"] = []
    for rec in records:
        outcome = normalize_outcome(rec)
        if outcome:
            groups[outcome].append(rec)
        else:
            groups["Unknown"].append(rec)
    return groups


def _outcome_weight(outcome: str) -> Tuple[float, float]:
    """Return (wins, losses) contribution for one record."""
    if outcome in POSITIVE_OUTCOMES:
        return 1.0, 0.0
    if outcome in SOFT_POSITIVE:
        return 0.35, 0.0  # contacted counts as partial win signal
    if outcome in NEGATIVE_OUTCOMES:
        return 0.0, 1.0
    return 0.0, 0.0


def smoothed_win_rate(
    wins: float,
    losses: float,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
) -> float:
    """
    Beta-Binomial (Laplace) smoothed win rate.

    win_rate = (wins + α) / (wins + losses + α + β)

    With α=β=1 this pulls sparse buckets toward 0.5 so one lucky win
    does not jump a weight from 0 → 1.0.
    """
    return (wins + prior_alpha) / (wins + losses + prior_alpha + prior_beta)


def _bucket_stats(
    records: List[Dict[str, Any]],
    key_fn,
    prior_alpha: float,
    prior_beta: float,
) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"wins": 0.0, "losses": 0.0, "n": 0.0}
    )
    for rec in records:
        outcome = normalize_outcome(rec)
        if not outcome:
            continue
        key = key_fn(rec)
        if not key:
            continue
        w, l = _outcome_weight(outcome)
        buckets[key]["wins"] += w
        buckets[key]["losses"] += l
        buckets[key]["n"] += 1

    out: Dict[str, Dict[str, Any]] = {}
    for key, stats in buckets.items():
        rate = smoothed_win_rate(
            stats["wins"], stats["losses"], prior_alpha, prior_beta
        )
        out[key] = {
            "wins": round(stats["wins"], 3),
            "losses": round(stats["losses"], 3),
            "samples": int(stats["n"]),
            "win_rate": round(rate, 4),
        }
    return out


def calculate_dynamic_weights(
    records: List[Dict[str, Any]],
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Derive industry/size win rates and multipliers from Airtable outcomes.

    Multiplier = bucket_win_rate / global_win_rate
    (1.0 = average converter; >1 favored; <1 down-weighted)
    """
    prev = previous or load_scoring_weights()
    min_samples = int(prev.get("min_samples") or 5)
    prior_alpha = float(prev.get("prior_alpha") or 1)
    prior_beta = float(prev.get("prior_beta") or 1)
    sig_delta = float(prev.get("significant_rate_delta") or 0.1)

    labeled = [r for r in records if normalize_outcome(r)]
    industry_stats = _bucket_stats(
        labeled,
        lambda r: str(r.get("industry") or r.get("Industry") or "").strip(),
        prior_alpha,
        prior_beta,
    )
    size_stats = _bucket_stats(
        labeled,
        lambda r: str(
            r.get("size_estimate") or r.get("Company Size") or ""
        ).strip(),
        prior_alpha,
        prior_beta,
    )

    total_wins = sum(s["wins"] for s in industry_stats.values())
    total_losses = sum(s["losses"] for s in industry_stats.values())
    global_rate = smoothed_win_rate(total_wins, total_losses, prior_alpha, prior_beta)

    def _multipliers(
        stats: Dict[str, Dict[str, Any]],
        prev_rates: Dict[str, float],
    ) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, int], List[str]]:
        rates: Dict[str, float] = {}
        mults: Dict[str, float] = {}
        samples: Dict[str, int] = {}
        significant: List[str] = []
        for key, st in stats.items():
            samples[key] = st["samples"]
            if st["samples"] < min_samples:
                # Keep previous learned rate if available; else skip (static scoring)
                if key in (prev.get("industry_win_rates") or {}) or key in (
                    prev.get("size_win_rates") or {}
                ):
                    old = float(prev_rates.get(key) or global_rate)
                    rates[key] = old
                    mults[key] = round(old / global_rate if global_rate else 1.0, 4)
                continue
            rates[key] = st["win_rate"]
            mults[key] = round(
                st["win_rate"] / global_rate if global_rate else 1.0, 4
            )
            old = float(prev_rates.get(key) or 0)
            if old and abs(st["win_rate"] - old) >= sig_delta:
                significant.append(key)
            elif not old and abs(st["win_rate"] - global_rate) >= sig_delta:
                significant.append(key)
        return rates, mults, samples, significant

    prev_ind = dict(prev.get("industry_win_rates") or {})
    prev_size = dict(prev.get("size_win_rates") or {})
    ind_rates, ind_mults, ind_samples, ind_sig = _multipliers(industry_stats, prev_ind)
    size_rates, size_mults, size_samples, size_sig = _multipliers(size_stats, prev_size)

    now = datetime.now(timezone.utc).isoformat()
    weights = {
        "version": int(prev.get("version") or 1) + (1 if prev.get("updated_at") else 0),
        "updated_at": now,
        "global_win_rate": round(global_rate, 4),
        "min_samples": min_samples,
        "prior_alpha": prior_alpha,
        "prior_beta": prior_beta,
        "significant_rate_delta": sig_delta,
        "score_shift_threshold": int(prev.get("score_shift_threshold") or 2),
        "industry_win_rates": ind_rates,
        "industry_multipliers": ind_mults,
        "industry_samples": ind_samples,
        "industry_stats": industry_stats,
        "size_win_rates": size_rates,
        "size_multipliers": size_mults,
        "size_samples": size_samples,
        "size_stats": size_stats,
        "labeled_count": len(labeled),
        "total_records": len(records),
        "significant_industry_changes": ind_sig,
        "significant_size_changes": size_sig,
        "previous": {
            "industry_win_rates": prev_ind,
            "size_win_rates": prev_size,
            "industry_multipliers": dict(prev.get("industry_multipliers") or {}),
            "size_multipliers": dict(prev.get("size_multipliers") or {}),
            "global_win_rate": prev.get("global_win_rate"),
            "updated_at": prev.get("updated_at"),
        },
        "last_run": None,
    }
    return weights


def format_weight_deltas(weights: Dict[str, Any]) -> List[str]:
    """Human lines like 'Tech +0.2, Finance -0.1' from win-rate deltas."""
    lines: List[str] = []
    prev = (weights.get("previous") or {}).get("industry_win_rates") or {}
    for industry, rate in sorted((weights.get("industry_win_rates") or {}).items()):
        old = prev.get(industry)
        if old is None:
            lines.append(f"{industry} {rate:.2f} (new)")
            continue
        delta = rate - float(old)
        if abs(delta) < 0.01:
            continue
        sign = "+" if delta >= 0 else ""
        lines.append(f"{industry} {sign}{delta:.2f}")
    return lines


def is_decision_changing(
    old_score: int,
    new_score: int,
    threshold: int = 2,
    old_status: str = "",
    new_status: str = "",
) -> bool:
    """
    True when the re-score is likely to change sales decisions (not noise).

    Requires |Δscore| > threshold OR Hot/Warm/Cold band change with |Δ| >= 1.
    """
    delta = abs(int(new_score) - int(old_score))
    if delta > threshold:
        return True
    if old_status and new_status and old_status != new_status and delta >= 1:
        return True
    return False


def append_feedback_log(message: str) -> None:
    FEEDBACK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{stamp}] {message}\n"
    with open(FEEDBACK_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    logger.info(message)
    print(f"[feedback] {message}")
