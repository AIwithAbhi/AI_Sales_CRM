"""Nightly Airtable feedback job: learn weights, re-score, report."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pipeline.crm import fetch_from_airtable, update_airtable_record
from services.email_alerts import send_html_to_recipients, smtp_configured
from services.feedback_loop import (
    append_feedback_log,
    calculate_dynamic_weights,
    format_weight_deltas,
    group_by_outcome,
    is_decision_changing,
    load_scoring_weights,
    normalize_outcome,
    save_scoring_weights,
    weights_path,
)
from utils.email_recipients import parse_recipient_emails
from utils.lead_scoring import compute_weighted_lead_score, reload_scoring_weights

logger = logging.getLogger(__name__)


def _record_for_scoring(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "industry": rec.get("industry") or rec.get("Industry") or "",
        "size_estimate": rec.get("size_estimate")
        or rec.get("Company Size")
        or "",
        "b2b_buyer": rec.get("b2b_buyer", rec.get("B2B Buyer", False)),
        "buying_signals": rec.get("buying_signals")
        or rec.get("Buying Signals")
        or [],
        "score_reason": rec.get("score_reason") or rec.get("Score Reason") or "",
        "lead_score_rationale": rec.get("lead_score_rationale") or "",
    }


def rescore_records(
    records: List[Dict[str, Any]],
    weights: Dict[str, Any],
    *,
    write_airtable: bool = True,
) -> Dict[str, Any]:
    """
    Re-score every Airtable lead with the new weights.

    Flags confidence shifts when |Δscore| > threshold (default 2) or
    Hot/Warm/Cold band changes with a meaningful delta.
    """
    threshold = int(weights.get("score_shift_threshold") or 2)
    shifted: List[Dict[str, Any]] = []
    updated = 0
    skipped = 0
    errors = 0

    for rec in records:
        try:
            old_score = int(rec.get("lead_score") or rec.get("Lead Score") or 0)
        except (TypeError, ValueError):
            old_score = 0
        old_status = str(rec.get("status_tag") or rec.get("Status") or "")

        scored = compute_weighted_lead_score(_record_for_scoring(rec), weights=weights)
        new_score = int(scored["lead_score"])
        new_status = scored["status_tag"]
        delta = new_score - old_score

        decision_change = is_decision_changing(
            old_score, new_score, threshold, old_status, new_status
        )

        patch = {
            "Lead Score": new_score,
            "Status": new_status,
            "Score Reason": scored["score_reason"],
            "Previous Lead Score": old_score if old_score else None,
            "Score Delta": delta,
            "Confidence Shift": decision_change,
        }

        record_id = rec.get("_airtable_id")
        if write_airtable and record_id:
            ok = update_airtable_record(record_id, patch)
            if ok:
                updated += 1
            else:
                errors += 1
        else:
            skipped += 1

        if decision_change:
            shifted.append({
                "company": rec.get("company_name") or rec.get("Name") or "",
                "industry": rec.get("industry") or rec.get("Industry") or "",
                "old_score": old_score,
                "new_score": new_score,
                "delta": delta,
                "old_status": old_status,
                "new_status": new_status,
                "outcome": normalize_outcome(rec),
            })

    return {
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "confidence_shifts": shifted,
        "shift_count": len(shifted),
    }


def build_feedback_report_html(summary: Dict[str, Any]) -> str:
    deltas = summary.get("weight_delta_lines") or []
    delta_text = ", ".join(deltas) if deltas else "No material industry weight changes"
    groups = summary.get("outcome_counts") or {}
    shifts = summary.get("confidence_shifts") or []

    shift_rows = ""
    for s in shifts[:25]:
        shift_rows += (
            f"<tr><td>{s.get('company')}</td><td>{s.get('industry')}</td>"
            f"<td>{s.get('old_score')} → {s.get('new_score')}</td>"
            f"<td>{s.get('old_status')} → {s.get('new_status')}</td></tr>"
        )
    if not shift_rows:
        shift_rows = "<tr><td colspan='4'>No confidence shifts (&gt;2 pts)</td></tr>"

    industry_rates = summary.get("industry_win_rates") or {}
    rate_rows = "".join(
        f"<tr><td>{k}</td><td>{v:.2f}</td>"
        f"<td>{(summary.get('industry_samples') or {}).get(k, 0)}</td></tr>"
        for k, v in sorted(industry_rates.items(), key=lambda x: -x[1])
    ) or "<tr><td colspan='3'>Insufficient labeled outcomes</td></tr>"

    return f"""
    <!DOCTYPE html>
    <html><body style="font-family:Arial,sans-serif;color:#111;margin:20px;">
      <h2>Daily scoring feedback report</h2>
      <p>{summary.get('log_message', '')}</p>
      <p><strong>New scoring weights:</strong> {delta_text}</p>
      <h3>Outcomes</h3>
      <ul>
        <li>Won: {groups.get('Won', 0)}</li>
        <li>Lost: {groups.get('Lost', 0)}</li>
        <li>Contacted: {groups.get('Contacted', 0)}</li>
        <li>No Response: {groups.get('No Response', 0)}</li>
        <li>Unlabeled: {groups.get('Unknown', 0)}</li>
      </ul>
      <h3>Industry win rates</h3>
      <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
        <tr><th>Industry</th><th>Win rate</th><th>Samples</th></tr>
        {rate_rows}
      </table>
      <h3>Confidence shifts</h3>
      <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
        <tr><th>Company</th><th>Industry</th><th>Score</th><th>Band</th></tr>
        {shift_rows}
      </table>
      <p style="color:#666;font-size:12px;">Generated {summary.get('ran_at')}</p>
    </body></html>
    """


def send_feedback_report(summary: Dict[str, Any]) -> Dict[str, Any]:
    if not smtp_configured():
        return {"ok": False, "error": "SMTP not configured"}
    primary = (
        os.getenv("FEEDBACK_REPORT_EMAIL")
        or os.getenv("RESEND_ACCOUNT_EMAIL")
        or os.getenv("EMAIL_SENDER")
        or ""
    )
    recipients = parse_recipient_emails(primary)
    if not recipients:
        return {"ok": False, "error": "No feedback report recipients"}

    deltas = summary.get("weight_delta_lines") or []
    delta_text = ", ".join(deltas[:6]) if deltas else "no material changes"
    subject = f"New scoring weights: {delta_text}"
    html = build_feedback_report_html(summary)
    return send_html_to_recipients(recipients, subject, html)


def run_feedback_loop(*, write_airtable: bool = True, send_email: bool = True) -> Dict[str, Any]:
    """
    Full nightly job:

    1. Read Airtable
    2. Group by Won/Lost/Contacted/No Response
    3. Calculate industry/size win rates
    4. Persist weights to data/scoring_weights.json
    5. Re-score all leads
    6. Flag confidence shifts
    7. Log + email daily report
    """
    ran_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    previous = load_scoring_weights()
    records = fetch_from_airtable()
    if not records:
        msg = "Feedback loop skipped — no Airtable records (or credentials missing)"
        append_feedback_log(msg)
        return {"ok": False, "error": msg, "ran_at": ran_at}

    groups = group_by_outcome(records)
    outcome_counts = {k: len(v) for k, v in groups.items()}
    wins = outcome_counts.get("Won", 0)

    weights = calculate_dynamic_weights(records, previous=previous)
    save_scoring_weights(weights)
    reload_scoring_weights()

    rescore = rescore_records(records, weights, write_airtable=write_airtable)
    weight_delta_lines = format_weight_deltas(weights)

    log_message = (
        f"Re-ranked {rescore['updated']} leads based on {wins} recent wins "
        f"({weights.get('labeled_count', 0)} labeled outcomes; "
        f"{rescore['shift_count']} confidence shifts)"
    )
    append_feedback_log(log_message)

    summary: Dict[str, Any] = {
        "ok": True,
        "ran_at": ran_at,
        "log_message": log_message,
        "outcome_counts": outcome_counts,
        "industry_win_rates": weights.get("industry_win_rates") or {},
        "industry_samples": weights.get("industry_samples") or {},
        "size_win_rates": weights.get("size_win_rates") or {},
        "weight_delta_lines": weight_delta_lines,
        "significant_industry_changes": weights.get("significant_industry_changes") or [],
        "confidence_shifts": rescore.get("confidence_shifts") or [],
        "rescore": {
            "updated": rescore["updated"],
            "errors": rescore["errors"],
            "shift_count": rescore["shift_count"],
        },
        "weights_path": str(weights_path()),
        "email": None,
    }

    weights["last_run"] = summary
    # Avoid huge nested previous.last_run growth
    weights["last_run"] = {
        "ran_at": ran_at,
        "log_message": log_message,
        "outcome_counts": outcome_counts,
        "weight_delta_lines": weight_delta_lines,
        "shift_count": rescore["shift_count"],
        "updated": rescore["updated"],
    }
    save_scoring_weights(weights)

    if send_email:
        summary["email"] = send_feedback_report(summary)

    return summary
