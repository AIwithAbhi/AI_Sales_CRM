"""Orchestrate regulatory event → sales opportunity → public email → draft."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pipeline.regulatory_sales_analyzer import analyze_regulatory_sales_opportunity
from services.public_email_discovery import discover_public_business_email
from utils.alert_store import (
    event_key,
    save_sales_opportunity_event,
    was_sales_opportunity_processed,
)

logger = logging.getLogger(__name__)


def _log_stage(company: str, stage: str, error: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    msg = f"[regulatory_sales] company={company} stage={stage} error={error} timestamp={ts}"
    logger.warning(msg)
    print(msg)


def build_sales_opportunity_from_article(
    company_name: str,
    article_row: Dict[str, Any],
    *,
    force: bool = False,
    email_info: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    For a relevant regulatory article, analyze opportunity, find public email,
    and produce a sales email draft structure.

    Returns None when not a sales opportunity or already processed (deduped).
    """
    url = (article_row.get("url") or "").strip()
    headline = (article_row.get("headline") or article_row.get("title") or "").strip()
    snippet = (
        article_row.get("snippet")
        or article_row.get("why_matters")
        or ""
    ).strip()

    if not headline and not url:
        return None

    key = event_key(company_name, url or headline)
    if not force and was_sales_opportunity_processed(company_name, url or headline):
        logger.info("Skipping duplicate sales opportunity %s", key)
        return None

    try:
        analysis = analyze_regulatory_sales_opportunity(
            company_name=company_name,
            headline=headline,
            snippet=snippet,
            source_url=url,
        )
    except Exception as exc:
        _log_stage(company_name, "nvidia_sales_analysis", str(exc))
        return None

    if analysis.get("error") and not analysis.get("sales_opportunity"):
        _log_stage(company_name, "nvidia_sales_analysis", str(analysis.get("error")))
        return None

    if not analysis.get("sales_opportunity"):
        # Mark processed so we don't re-analyze forever for non-opportunities
        save_sales_opportunity_event(company_name, url or headline)
        return {
            "company_name": company_name,
            "sales_opportunity": False,
            "regulatory_event": analysis.get("event_title") or headline,
            "problem": analysis.get("problem_identified") or "",
            "business_impact": analysis.get("business_impact") or "",
            "solution": "",
            "solution_reason": "",
            "skipped_reason": "No credible sales opportunity",
            "source_url": url,
            "event_key": key,
        }

    # Public email discovery (never invent) — reuse company-level result when provided
    if email_info is None:
        try:
            email_info = discover_public_business_email(company_name)
        except Exception as exc:
            _log_stage(company_name, "email_discovery", str(exc))
            email_info = {
                "email": None,
                "email_source_url": "",
                "email_source_name": "",
                "email_publicly_available": False,
                "email_confidence": "low",
                "contact_name": "",
                "contact_role": "",
                "discovery_notes": str(exc),
            }

    email = email_info.get("email")
    publicly = bool(email_info.get("email_publicly_available")) and bool(email)
    confidence = str(email_info.get("email_confidence") or "low")

    # Greeting
    role = (
        email_info.get("contact_role")
        or analysis.get("contact_role_suggestion")
        or analysis.get("affected_department")
        or "Team"
    )
    contact_name = email_info.get("contact_name") or ""
    if contact_name:
        greeting = f"Hi {contact_name},"
    else:
        greeting = f"Hi {role}," if role and role.lower() != "team" else "Hi there,"

    body = analysis.get("email_body") or ""
    if "{{contact_greeting}}" in body:
        body = body.replace("{{contact_greeting}}", greeting)
    elif body and not body.lower().startswith("hi"):
        body = f"{greeting}\n\n{body}"

    if not publicly:
        notice = (
            "\n\n---\n⚠ No publicly verified business email was found. "
            "Do not invent an address — locate a public contact before sending.\n"
        )
        body = (body or "").rstrip() + notice

    opportunity = {
        "company_name": company_name,
        "sales_opportunity": True,
        "regulatory_event": analysis.get("event_title") or headline,
        "regulatory_body": analysis.get("regulatory_body") or "",
        "regulatory_topic": analysis.get("regulatory_topic") or "",
        "event_type": analysis.get("event_type") or "",
        "problem": analysis.get("problem_identified") or "",
        "business_impact": analysis.get("business_impact") or "",
        "affected_department": analysis.get("affected_department") or "",
        "potential_risk": analysis.get("potential_risk") or "",
        "solution": analysis.get("recommended_solution") or "",
        "solution_reason": analysis.get("solution_reason") or "",
        "urgency": analysis.get("urgency") or article_row.get("urgency") or "",
        "contact_name": contact_name,
        "contact_role": role if not contact_name else email_info.get("contact_role") or role,
        "email": email if publicly else None,
        "email_source_url": email_info.get("email_source_url") or "",
        "email_source_name": email_info.get("email_source_name") or "",
        "email_publicly_available": publicly,
        "email_confidence": confidence if publicly else "low",
        "email_discovery_notes": email_info.get("discovery_notes") or "",
        "subject_options": analysis.get("subject_options") or [],
        "recommended_subject": analysis.get("recommended_subject") or "",
        "email_body": body,
        "source_url": url or analysis.get("source_url") or "",
        "source_name": analysis.get("source_name") or "",
        "fact_vs_inference": analysis.get("fact_vs_inference") or {},
        "event_key": key,
        "why_matters": article_row.get("why_matters") or "",
    }

    save_sales_opportunity_event(company_name, url or headline)
    return opportunity


def collect_sales_opportunities(
    company_name: str,
    article_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build sales opportunities for new relevant articles (skips non-opportunities in UI list)."""
    # Prefer urgent items; cap to keep Industry Updates responsive.
    max_opps = int(os.getenv("ALERT_MAX_SALES_DRAFTS", "3"))
    candidates = [r for r in article_rows if r.get("is_relevant")]
    candidates.sort(key=lambda r: 0 if r.get("urgency") == "urgent" else 1)

    # Discover public email once per company (was re-scraped for every article).
    shared_email = discover_public_business_email(company_name)

    opportunities: List[Dict[str, Any]] = []
    for row in candidates[: max(1, max_opps)]:
        try:
            opp = build_sales_opportunity_from_article(
                company_name, row, email_info=shared_email
            )
        except Exception as exc:
            _log_stage(company_name, "sales_opportunity_build", str(exc))
            continue
        if opp and opp.get("sales_opportunity"):
            opportunities.append(opp)
    return opportunities
