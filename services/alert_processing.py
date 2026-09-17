"""Orchestrate regulatory news search, analysis, sales opportunities, and digest email."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from pipeline.alert_analyzer import analyze_article, summarize_company_alerts
from pipeline.news_search import search_regulatory_news
from services.email_alerts import send_company_digest, smtp_configured
from services.regulatory_sales import collect_sales_opportunities
from utils.alert_store import save_alert, was_alert_sent
from utils.email_recipients import parse_recipient_emails


def process_company_alerts(company_name: str, recipient_emails: str) -> Dict[str, Any]:
    """
    Search news, analyze every article, generate sales email drafts for new relevant
    events, then send ONE consolidated digest email per company when there is news
    that hasn't been emailed before.

    Continues processing even if a single stage fails for this company.
    """
    recipients = parse_recipient_emails(recipient_emails)
    stage_errors: List[Dict[str, str]] = []

    try:
        articles = search_regulatory_news(company_name)
    except Exception as exc:
        ts = datetime.now(timezone.utc).isoformat()
        stage_errors.append({
            "company": company_name,
            "pipeline_stage": "firecrawl_search",
            "error": str(exc),
            "timestamp": ts,
        })
        print(
            f"[alert_processing] company={company_name} stage=firecrawl_search "
            f"error={exc} timestamp={ts}"
        )
        articles = []

    rows: List[Dict[str, Any]] = []
    urgent_count = 0

    for article in articles:
        headline = article.get("title", "")
        snippet = article.get("snippet", "")
        try:
            analysis = analyze_article(company_name, headline, snippet)
        except Exception as exc:
            ts = datetime.now(timezone.utc).isoformat()
            stage_errors.append({
                "company": company_name,
                "pipeline_stage": "nvidia_analysis",
                "error": str(exc),
                "timestamp": ts,
            })
            print(
                f"[alert_processing] company={company_name} stage=nvidia_analysis "
                f"error={exc} timestamp={ts}"
            )
            analysis = {
                "is_relevant": False,
                "urgency": "not_relevant",
                "why_matters": "",
                "talking_points": "",
            }

        is_relevant = bool(analysis.get("is_relevant"))
        is_urgent = is_relevant and analysis.get("urgency") == "urgent"
        if is_urgent:
            urgent_count += 1

        rows.append({
            "company_name": company_name,
            "headline": headline,
            "url": article.get("url", ""),
            "snippet": snippet,
            "query": article.get("query", ""),
            "urgency": "urgent" if is_urgent else ("relevant" if is_relevant else "not_relevant"),
            "is_relevant": is_relevant,
            "why_matters": analysis.get("why_matters", ""),
            "talking_points": analysis.get("talking_points", ""),
            "email_status": "skipped_not_urgent",
            "sales_opportunity": None,
        })

    relevant = [r for r in rows if r["is_relevant"]]
    already_sent = [r for r in relevant if was_alert_sent(company_name, r["headline"])]
    new_rows = [r for r in relevant if not was_alert_sent(company_name, r["headline"])]
    new_rows.sort(key=lambda r: 0 if r["urgency"] == "urgent" else 1)

    for r in already_sent:
        r["email_status"] = "skipped_duplicate"

    # Sales opportunities for NEW relevant events only (deduped by company+URL)
    sales_opportunities: List[Dict[str, Any]] = []
    try:
        sales_opportunities = collect_sales_opportunities(company_name, new_rows)
    except Exception as exc:
        ts = datetime.now(timezone.utc).isoformat()
        stage_errors.append({
            "company": company_name,
            "pipeline_stage": "sales_opportunity",
            "error": str(exc),
            "timestamp": ts,
        })
        print(
            f"[alert_processing] company={company_name} stage=sales_opportunity "
            f"error={exc} timestamp={ts}"
        )

    emails_sent = 0

    if new_rows:
        if not smtp_configured():
            for r in new_rows:
                r["email_status"] = "failed"
                r["email_error"] = "SMTP not configured (EMAIL_SENDER / EMAIL_PASSWORD)"
        else:
            try:
                summary = summarize_company_alerts(company_name, new_rows)
            except Exception as exc:
                summary = {
                    "summary": new_rows[0].get("why_matters", ""),
                    "priority_actions": new_rows[0].get("talking_points", ""),
                }
                stage_errors.append({
                    "company": company_name,
                    "pipeline_stage": "digest_summary",
                    "error": str(exc),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            result = send_company_digest(
                recipients,
                company_name,
                summary,
                new_rows,
                sales_opportunities=sales_opportunities,
            )
            sent_to = result.get("sent_to") or []
            failed_to = result.get("failed_to") or {}

            if result.get("ok"):
                for r in new_rows:
                    save_alert(company_name, r["headline"])
                emails_sent += len(sent_to)
                status = "partial" if (result.get("partial") or failed_to) else "sent"
                for r in new_rows:
                    r["email_status"] = status
                    r["email_sent_to"] = sent_to
                    if status == "partial":
                        r["email_error"] = result.get("error", "")
            else:
                for r in new_rows:
                    r["email_status"] = "failed"
                    r["email_error"] = result.get("error", "Send failed")

    return {
        "company_name": company_name,
        "articles_found": len(articles),
        "urgent_count": urgent_count,
        "emails_sent": emails_sent,
        "articles": rows,
        "sales_opportunities": sales_opportunities,
        "stage_errors": stage_errors,
    }
