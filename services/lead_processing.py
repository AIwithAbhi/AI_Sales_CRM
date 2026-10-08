"""Company search pipeline for FastAPI jobs."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pipeline import scrape_homepage
from pipeline.analyzer import analyze_company
from pipeline.search import discover_company_search
from services.lead_insights import extract_contact_fallback
from utils.helpers import load_headcount_data, normalize_company_size
from utils.lead_scoring import compute_weighted_lead_score
from utils.record_validation import apply_review_flag

logger = logging.getLogger(__name__)


def _base_result(company_name: str) -> Dict[str, Any]:
    return {
        "company_name": company_name,
        "url": "",
        "summary": "",
        "industry": "Unknown",
        "size_estimate": "Unknown",
        "b2b_buyer": None,
        "b2b_evidence": "",
        "business_model": "",
        "buying_signals": [],
        "lead_score": None,
        "status_tag": "Not scored",
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
        "match_confidence": "Low",
        "match_reason": "",
        "match_candidates": [],
        "match_ambiguous": False,
        "search_rejections": [],
        "search_source": "",
        "typo_suggestion": None,
        "scrape_status": "none",
        "scored": False,
        "insufficient_data": False,
        "analysis_failed": False,
        "analysis_error": "",
    }


def _mark_unscored(
    result: Dict[str, Any],
    reason: str,
    *,
    review: bool = True,
) -> Dict[str, Any]:
    result["scored"] = False
    result["insufficient_data"] = True
    result["lead_score"] = None
    result["status_tag"] = "Not scored"
    result["score_reason"] = f"Not scored - insufficient data: {reason}"
    result["lead_score_rationale"] = reason
    result["industry"] = result.get("industry") or "Unknown"
    if str(result.get("industry") or "").strip().lower() in ("other", ""):
        result["industry"] = "Unknown"
    result["size_estimate"] = "Unknown"
    if result.get("b2b_buyer") is False and not result.get("b2b_evidence"):
        result["b2b_buyer"] = None
    result["score_breakdown"] = {
        "industry_points": 0,
        "size_points": 0,
        "b2b_points": 0,
        "signal_points": 0,
        "raw_total": 0,
        "buying_signals": [],
        "scored": False,
    }
    if review:
        result["review_needed"] = True
        errs = list(result.get("validation_errors") or [])
        if reason not in errs:
            errs.append(reason)
        result["validation_errors"] = errs
    return result


def process_company(
    company_name: str,
    *,
    preselected_url: Optional[str] = None,
    confirmed_name: Optional[str] = None,
    force_analysis_fail: bool = False,
) -> Dict[str, Any]:
    """
    Resolve a validated URL, scrape, analyze, score, and flag for review if needed.

    preselected_url / confirmed_name: only set after explicit user confirmation.
    force_analysis_fail: test hook to simulate NVIDIA 401.
    """
    effective_name = (confirmed_name or company_name).strip() or company_name
    result = _base_result(effective_name)
    if confirmed_name and confirmed_name.strip().lower() != company_name.strip().lower():
        result["original_query"] = company_name

    headcount_data = load_headcount_data()
    company_key = effective_name.strip().lower()
    headcount_info = headcount_data.get(company_key, {})
    result["growth_label"] = headcount_info.get("growth_label", "No data")
    result["growth_rate"] = headcount_info.get("growth_rate", 0.0)

    growth_label = result["growth_label"]
    growth_rate = result["growth_rate"]
    headcount_context = (
        f"LinkedIn headcount trend: {growth_label} ({growth_rate:.1f}% over 4 weeks)"
    )

    # Step 1: discovery unless user already confirmed a URL
    if preselected_url:
        url = preselected_url
        search_context = ""
        result["match_confidence"] = "High"
        result["match_reason"] = "User-confirmed company match"
        result["search_source"] = "user_confirmed"
        result["match_ambiguous"] = False
        discovered: Dict[str, Any] = {}
    else:
        discovered = discover_company_search(effective_name)
        url = discovered.get("url")
        search_context = discovered.get("search_context") or ""
        result["search_source"] = discovered.get("source") or ""
        result["match_candidates"] = discovered.get("candidates") or []
        result["search_rejections"] = discovered.get("rejections") or []
        result["typo_suggestion"] = discovered.get("typo_suggestion")
        result["match_ambiguous"] = bool(discovered.get("match_ambiguous"))
        result["selectable_candidates"] = discovered.get("selectable_candidates") or []
        result["entity_labels"] = discovered.get("entity_labels") or []

        if result["match_ambiguous"]:
            selectable = result.get("selectable_candidates") or []
            # Ambiguous requires 2+ plausible candidates; otherwise it's no match
            if len(selectable) < 2:
                result["match_ambiguous"] = False
                result["match_confidence"] = "Low"
                result["match_reason"] = "No confident match"
                # Fall through to no-URL / typo handling below
            else:
                # Cap: never High when 2+ distinct entities appear
                result["match_confidence"] = "Low"
                labels = ", ".join(result["entity_labels"][:5]) or "multiple entities"
                result["match_reason"] = (
                    f"Did you mean? Multiple plausible companies "
                    f"({labels}). Confirm which one you meant."
                )
                result["display_message"] = result["match_reason"]
                result["url"] = url or ""
                result["proposed_url"] = url or ""
                result = _mark_unscored(
                    result,
                    result["match_reason"],
                    review=True,
                )
                result["status_tag"] = "Needs review"
                result["error"] = None  # not a hard fail — awaiting choice
                return result

        if url:
            result["match_confidence"] = "High"
            result["match_reason"] = (
                f"Validated homepage via {result['search_source'] or 'search'}"
            )
        else:
            result["match_confidence"] = "Low"
            typo = discovered.get("typo_suggestion")
            if typo:
                suggested = typo.get("suggested_name") or typo.get("suggested_brand") or ""
                result["match_reason"] = (
                    typo.get("reason")
                    or (
                        f"Did you mean {suggested}?"
                        if suggested
                        else "No validated homepage; nearby brand suggested"
                    )
                )
                result["display_message"] = result["match_reason"]
            else:
                result["match_reason"] = "No confident match"
                result["display_message"] = "No confident match"

    if not url:
        typo = result.get("typo_suggestion") or discovered.get("typo_suggestion")
        if typo and typo.get("suggested_name"):
            # Near-miss spelling — needs review, never auto-switch, never score
            suggested = typo.get("suggested_name")
            result["error"] = None
            result["scrape_status"] = "none"
            result["display_message"] = (
                result.get("match_reason")
                or f"Did you mean {suggested}?"
            )
            result["match_reason"] = result["display_message"]
            result = _mark_unscored(
                result,
                result["display_message"],
                review=True,
            )
            result["status_tag"] = "Needs review"
            result["typo_suggestion"] = typo
            return result

        result["error"] = "No confident match"
        result["scrape_status"] = "none"
        result["display_message"] = "No confident match"
        result["match_reason"] = "No confident match"
        result = _mark_unscored(
            result,
            "No confident match",
            review=True,
        )
        result["status_tag"] = "Error"
        if result.get("search_rejections"):
            result["validation_errors"].append(
                "Website not found or failed URL validation"
            )
        return result
    result["url"] = url
    homepage_text = scrape_homepage(url)
    if not homepage_text:
        if search_context:
            homepage_text = (
                f"[Scraping failed. Using search results fallback]\n\n{search_context}"
            )
            result["scrape_status"] = "fallback_context"
        else:
            result["error"] = "Failed to scrape website"
            result["scrape_status"] = "none"
            result["summary"] = "No website data retrieved"
            result["display_message"] = "No website data retrieved"
            result = _mark_unscored(result, "No website data retrieved", review=True)
            result["status_tag"] = "Error"
            return result
    else:
        result["scrape_status"] = "scraped"

    if force_analysis_fail:
        from pipeline.analyzer import _failed_analysis

        analysis = _failed_analysis("NVIDIA API rejected the key (401)")
    else:
        text_for_ai = homepage_text[:3000]
        analysis = analyze_company(effective_name, text_for_ai, headcount_context)

    result["analysis_failed"] = bool(analysis.get("analysis_failed"))
    result["analysis_error"] = str(analysis.get("analysis_error") or "")

    headcount = (
        headcount_info.get("headcount_week4")
        or headcount_info.get("headcount_week1")
        or 0
    )
    size_estimate = normalize_company_size(
        analysis.get("size_estimate", ""),
        headcount if headcount > 0 else None,
    )

    # Honest field labels based on scrape vs analysis outcome
    if result["analysis_failed"]:
        unknown_label = (
            "No website data retrieved"
            if result["scrape_status"] == "none"
            else (result["analysis_error"] or "Analysis failed - insufficient data")
        )
        result.update({
            "summary": unknown_label,
            "industry": "Unknown",
            "size_estimate": "Unknown",
            "b2b_buyer": None,
            "b2b_evidence": "Unknown",
            "business_model": "Unknown",
            "buying_signals": [],
            "score_reason": unknown_label,
            "lead_score_rationale": unknown_label,
            "confidence": "LOW",
            "headquarters": "Unknown",
            "country": "Unknown",
            "phone": "",
            "email": "",
            "linkedin": "",
            "contact_page": "",
            "contact_reason": unknown_label,
        })
        result["display_message"] = unknown_label
        result = _mark_unscored(result, unknown_label, review=True)
        result["status_tag"] = "Needs review"
        return apply_review_flag(result)

    result.update({
        "summary": analysis.get("summary", ""),
        "industry": analysis.get("industry", "Unknown") or "Unknown",
        "size_estimate": size_estimate,
        "b2b_buyer": analysis.get("b2b_buyer"),
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

    for key in ("phone", "email", "linkedin", "contact_page"):
        val = str(result.get(key) or "").strip().lower()
        if val in ("", "not stated on website", "n/a", "none", "unknown"):
            result[key] = ""
    if not all(result.get(k) for k in ("phone", "email", "linkedin", "contact_page")):
        fallback = extract_contact_fallback(homepage_text, url, effective_name)
        for key in ("phone", "email", "linkedin", "contact_page"):
            if not result.get(key) and fallback.get(key):
                result[key] = fallback[key]

    scored = compute_weighted_lead_score(result)
    result["lead_score"] = scored["lead_score"]
    result["status_tag"] = scored["status_tag"]
    result["score_reason"] = scored["score_reason"]
    result["scored"] = bool(scored.get("scored"))
    result["insufficient_data"] = not result["scored"]
    result["lead_score_rationale"] = (
        result.get("lead_score_rationale") or analysis.get("lead_score_rationale") or ""
    )
    result["score_breakdown"] = scored["score_breakdown"]
    result["buying_signals"] = scored["buying_signals"]

    result = apply_review_flag(result)
    if result.get("confidence") == "LOW" and not result.get("error"):
        result["review_needed"] = True
        errs = list(result.get("validation_errors") or [])
        if "Low confidence analysis" not in errs:
            errs.append("Low confidence analysis")
        result["validation_errors"] = errs
    if not result.get("scored"):
        result["review_needed"] = True
        result["status_tag"] = "Needs review"
        errs = list(result.get("validation_errors") or [])
        note = "Not scored - insufficient data"
        if note not in errs:
            errs.append(note)
        result["validation_errors"] = errs
    if result.get("review_needed") and result.get("status_tag") not in (
        "Hot", "Warm", "Cold", "Error",
    ):
        result["status_tag"] = "Needs review"
    if result.get("review_needed"):
        logger.warning(
            "Company '%s' marked review_needed: %s",
            effective_name,
            result.get("validation_errors"),
        )

    return result
