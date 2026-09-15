"""Company search pipeline for FastAPI jobs."""

from __future__ import annotations

import logging
from typing import Any, Dict

from pipeline import analyze_company, scrape_homepage, search_company_info
from services.lead_insights import extract_contact_fallback
from utils.helpers import load_headcount_data, normalize_company_size
from utils.lead_scoring import compute_weighted_lead_score
from utils.record_validation import apply_review_flag

logger = logging.getLogger(__name__)


def process_company(company_name: str) -> Dict[str, Any]:
    """
    Resolve a validated URL, scrape, analyze, score, and flag for review if needed.
    """
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

    # Step 1: validated URL (redirects + company-name page check)
    url, search_context = search_company_info(company_name)
    if not url:
        result["error"] = "Website not found or failed URL validation"
        result["review_needed"] = True
        result["validation_errors"] = ["No valid company URL"]
        return result

    result["url"] = url
    homepage_text = scrape_homepage(url)
    if not homepage_text:
        if search_context:
            homepage_text = (
                f"[Scraping failed. Using search results fallback]\n\n{search_context}"
            )
        else:
            result["error"] = "Failed to scrape website"
            result["review_needed"] = True
            result["validation_errors"] = ["Scrape failed"]
            return result

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
    if result.get("review_needed"):
        logger.warning(
            "Company '%s' marked review_needed: %s",
            company_name,
            result.get("validation_errors"),
        )

    return result
