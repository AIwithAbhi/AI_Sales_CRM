"""Scan completed search job JSON for leads eligible for agent actions."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from services.agents import config as agent_config
from services.agents import store as agent_store
from services.lead_insights import validate_and_format_phone


def _iter_done_search_jobs(
    *,
    lookback_days: Optional[int] = None,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    jobs_dir = agent_config.jobs_dir()
    if not os.path.isdir(jobs_dir):
        return
    cutoff: Optional[float] = None
    if lookback_days is not None and lookback_days > 0:
        cutoff = time.time() - (lookback_days * 86400)
    try:
        names = sorted(os.listdir(jobs_dir))
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(jobs_dir, name)
        try:
            if cutoff is not None:
                mtime = os.path.getmtime(path)
                if mtime < cutoff:
                    continue
            with open(path, "r", encoding="utf-8") as fh:
                job = json.load(fh)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(job, dict):
            continue
        if job.get("job_type") and job.get("job_type") != "search":
            continue
        if job.get("status") != "done":
            continue
        job_id = str(job.get("job_id") or name[:-5])
        yield job_id, job


def _profile_dimension_reasons(result: Dict[str, Any], *, limit: int = 6) -> List[str]:
    reasons: List[str] = []
    profiles = result.get("profile_scores") or {}
    if not isinstance(profiles, dict):
        return reasons
    for _pid, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        dims = profile.get("dimensions") or {}
        if not isinstance(dims, dict):
            continue
        for _did, dim in dims.items():
            if not isinstance(dim, dict):
                continue
            reason = (dim.get("reason") or "").strip()
            name = (dim.get("name") or _did or "").strip()
            score = dim.get("score")
            if reason:
                label = f"{name} ({score}/10): {reason}" if score is not None else f"{name}: {reason}"
                reasons.append(label)
            if len(reasons) >= limit:
                return reasons
    return reasons


def lead_has_evidence(result: Dict[str, Any]) -> bool:
    """True when the lead has enough text for an evidence-grounded draft."""
    if (result.get("score_reason") or "").strip():
        return True
    if (result.get("lead_score_rationale") or "").strip():
        return True
    if (result.get("summary") or "").strip():
        return True
    if _profile_dimension_reasons(result, limit=1):
        return True
    return False


def is_outreach_eligible(result: Dict[str, Any]) -> bool:
    if result.get("error"):
        return False
    if agent_config.skip_review_needed() and result.get("review_needed"):
        return False
    try:
        score = int(result.get("lead_score") or 0)
    except (TypeError, ValueError):
        score = 0
    if score < agent_config.outreach_score_threshold():
        return False
    return lead_has_evidence(result)


def extract_phone(result: Dict[str, Any]) -> str:
    raw = (
        result.get("phone")
        or result.get("phone_display")
        or ""
    )
    if isinstance(raw, str) and raw.strip().lower() in (
        "not available",
        "not found",
        "not stated on website",
        "",
    ):
        raw = ""
    formatted = validate_and_format_phone(str(raw or ""))
    if not formatted:
        return ""
    # validate_and_format_phone returns the sentinel string for rejects
    if formatted.strip().lower() in (
        "not available",
        "not found",
        "not stated on website",
    ):
        return ""
    return formatted


def snapshot_lead(result: Dict[str, Any], *, job_id: str = "") -> Dict[str, Any]:
    """Compact lead payload stored with drafts / used for prompts."""
    urls = result.get("scrape_page_urls") or []
    if not isinstance(urls, list):
        urls = []
    return {
        "company_name": result.get("company_name") or "",
        "url": result.get("url") or "",
        "industry": result.get("industry") or "",
        "size_estimate": result.get("size_estimate") or "",
        "lead_score": result.get("lead_score"),
        "status_tag": result.get("status_tag") or "",
        "score_reason": result.get("score_reason") or "",
        "lead_score_rationale": result.get("lead_score_rationale") or "",
        "summary": result.get("summary") or "",
        "buying_signals": result.get("buying_signals") or [],
        "phone": extract_phone(result),
        "email": result.get("email") or "",
        "scrape_page_urls": [u for u in urls if isinstance(u, str)][:8],
        "scrape_fallback": bool(result.get("scrape_fallback")),
        "profile_reasons": _profile_dimension_reasons(result),
        "enterprise_readiness_tier": result.get("enterprise_readiness_tier") or "",
        "job_id": job_id,
        "review_needed": bool(result.get("review_needed")),
    }


def iter_qualified_outreach_leads(
    *,
    skip_processed: bool = True,
) -> Iterator[Dict[str, Any]]:
    """Yield eligible leads from done search jobs (idempotent when skip_processed)."""
    agent_store.ensure_db()
    for job_id, job in _iter_done_search_jobs():
        results = job.get("results") or []
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            if not is_outreach_eligible(result):
                continue
            company = (result.get("company_name") or "").strip()
            url = (result.get("url") or "").strip()
            if not company:
                continue
            lead_key = agent_store.make_lead_key(company, url, job_id)
            if skip_processed and agent_store.is_lead_processed(lead_key):
                continue
            yield {
                "lead_key": lead_key,
                "job_id": job_id,
                "result": result,
                "snapshot": snapshot_lead(result, job_id=job_id),
            }


def iter_call_score_candidates() -> Iterator[Dict[str, Any]]:
    """Leads meeting CALL_SCORE_THRESHOLD with a usable phone."""
    threshold = agent_config.call_score_threshold()
    for job_id, job in _iter_done_search_jobs():
        results = job.get("results") or []
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            if result.get("error"):
                continue
            try:
                score = int(result.get("lead_score") or 0)
            except (TypeError, ValueError):
                score = 0
            if score < threshold:
                continue
            phone = extract_phone(result)
            if not phone:
                continue
            company = (result.get("company_name") or "").strip()
            url = (result.get("url") or "").strip()
            if not company:
                continue
            lead_key = agent_store.make_lead_key(company, url, job_id)
            yield {
                "lead_key": lead_key,
                "job_id": job_id,
                "company_name": company,
                "phone": phone,
                "lead_score": score,
                "result": result,
                "snapshot": snapshot_lead(result, job_id=job_id),
            }


def collect_recent_results(
    *,
    lookback_days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """All result rows from done search jobs in the lookback window."""
    days = (
        lookback_days
        if lookback_days is not None
        else agent_config.content_lookback_days()
    )
    out: List[Dict[str, Any]] = []
    for job_id, job in _iter_done_search_jobs(lookback_days=days):
        results = job.get("results") or []
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            if result.get("error"):
                continue
            snap = snapshot_lead(result, job_id=job_id)
            snap["_job_created_at"] = job.get("created_at")
            out.append(snap)
    return out
