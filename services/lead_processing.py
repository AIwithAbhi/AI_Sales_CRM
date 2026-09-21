"""Company search pipeline for FastAPI jobs."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pipeline import analyze_company, scrape_homepage
from pipeline.search import discover_company_match
from services.lead_insights import extract_contact_fallback
from utils.funnel_log import log_funnel_stage
from utils.helpers import load_headcount_data, normalize_company_size
from utils.lead_scoring import (
    compute_enterprise_readiness_tier,
    compute_weighted_lead_score,
)
from utils.record_validation import apply_review_flag
from utils.company_match import is_news_or_media_url

logger = logging.getLogger(__name__)


def process_company(
    company_name: str,
    run_id: Optional[str] = None,
    *,
    preselected_url: Optional[str] = None,
    match_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a validated URL, scrape, analyze, score, and flag for review if needed.
    """
    try:
        return _process_company_impl(
            company_name,
            run_id=run_id,
            preselected_url=preselected_url,
            match_meta=match_meta,
        )
    except Exception as exc:
        if run_id:
            log_funnel_stage(
                company_name,
                run_id,
                "failed",
                failure_reason=str(exc),
            )
        raise


def _process_company_impl(
    company_name: str,
    *,
    run_id: Optional[str] = None,
    preselected_url: Optional[str] = None,
    match_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "company_name": company_name,
        "url": "",
        "summary": "",
        "industry": "",
        "size_estimate": "",
        "b2b_buyer": False,
        "b2b_evidence": "",
        "business_model": "",
        "buying_signals": [],
        "lead_score": 0,
        "status_tag": "Unknown",
        "score_reason": "",
        "lead_score_rationale": "",
        "confidence": "LOW",
        "score_breakdown": {},
        "error": None,
        "growth_label": "No data",
        "growth_rate": 0.0,
        "headquarters": "",
        "country": "",
        "phone": "",
        "email": "",
        "linkedin": "",
        "contact_page": "",
        "contact_reason": "",
        "review_needed": False,
        "validation_errors": [],
        "ai_maturity_score": 1,
        "ai_maturity_reason": "",
        "ai_maturity_confidence": "low",
        "transformation_readiness_score": 1,
        "transformation_readiness_reason": "",
        "transformation_readiness_confidence": "low",
        "enterprise_readiness_tier": "Low",
        "enterprise_readiness_avg": 1.0,
        "match_confidence": "Low",
        "match_ambiguous": False,
        "match_reason": "",
        "match_domain": "",
        "match_candidates": [],
    }

    headcount_data = load_headcount_data()
    company_key = company_name.strip().lower()
    headcount_info = headcount_data.get(company_key, {})
    result["growth_label"] = headcount_info.get("growth_label", "No data")
    result["growth_rate"] = headcount_info.get("growth_rate", 0.0)

    growth_label = result["growth_label"]
    growth_rate = result["growth_rate"]
    headcount_context = (
        f"LinkedIn headcount trend: {growth_label} ({growth_rate:.1f}% over 4 weeks)"
    )

    # Step 1: ranked company match (prefer official domain even if scrape may fail)
    meta = match_meta or {}
    if preselected_url:
        discovered = discover_company_match(company_name, probe=False)
        match = {
            "url": preselected_url,
            "search_context": meta.get("search_context")
            or discovered.get("search_context")
            or "",
            "match_confidence": meta.get("match_confidence") or "High",
            "match_ambiguous": bool(meta.get("match_ambiguous", False)),
            "match_reason": meta.get("match_reason") or "User-selected company match",
            "selected_domain": meta.get("match_domain")
            or discovered.get("selected_domain")
            or "",
            "candidates": meta.get("candidates")
            or discovered.get("candidates")
            or [],
        }
    else:
        match = discover_company_match(company_name)

    url = match.get("url")
    search_context = match.get("search_context") or ""
    result["match_confidence"] = match.get("match_confidence") or "Low"
    result["match_ambiguous"] = bool(match.get("match_ambiguous"))
    result["match_reason"] = match.get("match_reason") or ""
    result["match_domain"] = match.get("selected_domain") or ""
    result["match_candidates"] = match.get("candidates") or []

    if not url:
        result["error"] = "Website not found or failed URL validation"
        result["review_needed"] = True
        result["validation_errors"] = ["No valid company URL"]
        if run_id:
            log_funnel_stage(
                company_name,
                run_id,
                "failed",
                failure_reason=result["error"],
            )
        return result

    result["url"] = url
    homepage_text = scrape_homepage(url)
    # If scrape fails OR landed on news/off-topic page, prefer search/wiki context
    use_context = False
    if not homepage_text:
        use_context = True
    elif is_news_or_media_url(url):
        use_context = True
    elif search_context and len(homepage_text) < 400:
        use_context = True

    if use_context and search_context:
        homepage_text = (
            f"[Using search/Wikipedia context for analysis — "
            f"homepage scrape insufficient or unreachable]\n\n{search_context}"
            + (f"\n\n[Partial page text]\n{homepage_text[:1200]}" if homepage_text else "")
        )
    elif not homepage_text:
        result["error"] = "Failed to scrape website"
        result["review_needed"] = True
        result["validation_errors"] = ["Scrape failed"]
        if run_id:
            log_funnel_stage(
                company_name,
                run_id,
                "failed",
                failure_reason=result["error"],
            )
        return result

    if run_id:
        log_funnel_stage(company_name, run_id, "scraped")

    text_for_ai = homepage_text[:3000]
    analysis = analyze_company(company_name, text_for_ai, headcount_context)

    headcount = (
        headcount_info.get("headcount_week4")
        or headcount_info.get("headcount_week1")
        or 0
    )
    size_estimate = normalize_company_size(
        analysis.get("size_estimate", ""),
        headcount if headcount > 0 else None,
    )
    # Enterprise scale cues from Wikipedia/search context when AI left size Unknown
    if size_estimate in ("Unknown", "", "Not stated on website"):
        ctx_low = (search_context or homepage_text or "").lower()
        if any(
            k in ctx_low
            for k in (
                "multinational",
                "fortune 500",
                "billion parcels",
                "100,000",
                "employees worldwide",
                "global logistics",
                "dhl group",
            )
        ):
            size_estimate = "1001+"
            if not str(analysis.get("lead_score_rationale") or "").strip() or analysis.get(
                "lead_score_rationale"
            ) in ("Not stated on website", "Analysis failed - no data available."):
                analysis["lead_score_rationale"] = (
                    "Enterprise scale inferred from search/Wikipedia context "
                    "(multinational / global workforce signals)."
                )
                analysis["score_reason"] = analysis["lead_score_rationale"]


    result.update({
        "summary": analysis.get("summary", ""),
        "industry": analysis.get("industry", ""),
        "size_estimate": size_estimate,
        "b2b_buyer": bool(analysis.get("b2b_buyer", False)),
        "b2b_evidence": analysis.get("b2b_evidence", ""),
        "business_model": analysis.get("business_model", "Not stated on website"),
        "buying_signals": analysis.get("buying_signals") or [],
        "score_reason": analysis.get("score_reason")
            or analysis.get("lead_score_rationale", ""),
        "lead_score_rationale": analysis.get("lead_score_rationale")
            or analysis.get("score_reason", ""),
        "confidence": analysis.get("confidence", "LOW"),
        "headquarters": analysis.get("headquarters", ""),
        "country": analysis.get("country", ""),
        "phone": analysis.get("phone", ""),
        "email": analysis.get("email", ""),
        "linkedin": analysis.get("linkedin", ""),
        "contact_page": analysis.get("contact_page", ""),
        "contact_reason": analysis.get("contact_reason", ""),
        "ai_maturity_score": analysis.get("ai_maturity_score", 1),
        "ai_maturity_reason": analysis.get("ai_maturity_reason", ""),
        "ai_maturity_confidence": analysis.get("ai_maturity_confidence", "low"),
        "transformation_readiness_score": analysis.get(
            "transformation_readiness_score", 1
        ),
        "transformation_readiness_reason": analysis.get(
            "transformation_readiness_reason", ""
        ),
        "transformation_readiness_confidence": analysis.get(
            "transformation_readiness_confidence", "low"
        ),
    })

    # Treat "Not stated on website" as missing for contact enrichment
    for key in ("phone", "email", "linkedin", "contact_page"):
        val = str(result.get(key) or "").strip().lower()
        if val in ("", "not stated on website", "n/a", "none"):
            result[key] = ""
    if not all(result.get(k) for k in ("phone", "email", "linkedin", "contact_page")):
        fallback = extract_contact_fallback(homepage_text, url, company_name)
        for key in ("phone", "email", "linkedin", "contact_page"):
            if not result.get(key) and fallback.get(key):
                result[key] = fallback[key]

    # Step 3: weighted lead score (not AI guess)
    scored = compute_weighted_lead_score(result)
    result["lead_score"] = scored["lead_score"]
    result["status_tag"] = scored["status_tag"]
    result["score_reason"] = scored["score_reason"]
    result["lead_score_rationale"] = (
        result.get("lead_score_rationale") or analysis.get("lead_score_rationale") or ""
    )
    result["score_breakdown"] = scored["score_breakdown"]
    result["buying_signals"] = scored["buying_signals"]

    # Additive enterprise scorecard (does not affect Hot/Warm/Cold)
    readiness = compute_enterprise_readiness_tier(
        result.get("ai_maturity_score"),
        result.get("transformation_readiness_score"),
    )
    result["ai_maturity_score"] = readiness["ai_maturity_score"]
    result["transformation_readiness_score"] = readiness[
        "transformation_readiness_score"
    ]
    result["enterprise_readiness_tier"] = readiness["enterprise_readiness_tier"]
    result["enterprise_readiness_avg"] = readiness["enterprise_readiness_avg"]
    if not str(result.get("ai_maturity_reason") or "").strip():
        result["ai_maturity_reason"] = (
            "limited evidence available from homepage content"
        )
    if not str(result.get("transformation_readiness_reason") or "").strip():
        result["transformation_readiness_reason"] = (
            "limited evidence available from homepage content"
        )
    for conf_key in (
        "ai_maturity_confidence",
        "transformation_readiness_confidence",
    ):
        conf = str(result.get(conf_key) or "low").strip().lower()
        result[conf_key] = conf if conf in ("high", "medium", "low") else "low"

    if run_id:
        log_funnel_stage(
            company_name,
            run_id,
            "scored",
            lead_score=result.get("lead_score"),
            status_tag=result.get("status_tag"),
            industry=result.get("industry"),
        )

    # Step 4: QA flag (Airtable push will skip review_needed)
    result = apply_review_flag(result)
    if result.get("confidence") == "LOW" and not result.get("error"):
        logger.info(
            "Low-confidence analysis for '%s' — flagging for review", company_name
        )
        result["review_needed"] = True
        errs = list(result.get("validation_errors") or [])
        if "Low confidence analysis" not in errs:
            errs.append("Low confidence analysis")
        result["validation_errors"] = errs
    if result.get("match_ambiguous") or result.get("match_confidence") == "Low":
        errs = list(result.get("validation_errors") or [])
        flag = "Ambiguous company match — auto-resolved; verify domain"
        if flag not in errs:
            errs.append(flag)
        result["validation_errors"] = errs
        # Don't block push solely for Medium ambiguous; flag for visibility
        if result.get("match_confidence") == "Low":
            result["review_needed"] = True
    if result.get("review_needed"):
        logger.warning(
            "Company '%s' marked review_needed: %s",
            company_name,
            result.get("validation_errors"),
        )

    return result
