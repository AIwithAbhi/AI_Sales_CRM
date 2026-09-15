"""Orchestrate regulatory news search, analysis, and a consolidated email alert per company."""

from typing import Any, Dict, List

from pipeline.alert_analyzer import analyze_article, summarize_company_alerts
from pipeline.news_search import search_regulatory_news
from services.email_alerts import send_company_digest, smtp_configured
from utils.alert_store import save_alert, was_alert_sent
from utils.email_recipients import parse_recipient_emails


def process_company_alerts(company_name: str, recipient_emails: str) -> Dict[str, Any]:
    """
    Search news, analyze every article, then send ONE consolidated digest email per
    company containing all of its relevant news — but only when there is news that
    hasn't been emailed before.

    Returns a company-level summary plus per-article rows for the UI table.
    """
    recipients = parse_recipient_emails(recipient_emails)
    articles = search_regulatory_news(company_name)

    rows: List[Dict[str, Any]] = []
    urgent_count = 0

    # 1) Analyze each article and build display rows.
    for article in articles:
        headline = article.get("title", "")
        snippet = article.get("snippet", "")

        analysis = analyze_article(company_name, headline, snippet)
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
            "urgency": "urgent" if is_urgent else "not_relevant",
            "is_relevant": is_relevant,
            "why_matters": analysis.get("why_matters", ""),
            "talking_points": analysis.get("talking_points", ""),
            "email_status": "skipped_not_urgent",
        })

    # 2) Gather relevant items; only NEW (not-yet-emailed) ones trigger a digest.
    relevant = [r for r in rows if r["is_relevant"]]
    already_sent = [r for r in relevant if was_alert_sent(company_name, r["headline"])]
    new_rows = [r for r in relevant if not was_alert_sent(company_name, r["headline"])]
    # Urgent items first in the digest.
    new_rows.sort(key=lambda r: 0 if r["urgency"] == "urgent" else 1)

    for r in already_sent:
        r["email_status"] = "skipped_duplicate"

    emails_sent = 0

    # 3) Send a single consolidated email for the new relevant items.
    if new_rows:
        if not smtp_configured():
            for r in new_rows:
                r["email_status"] = "failed"
                r["email_error"] = "SMTP not configured (EMAIL_SENDER / EMAIL_PASSWORD)"
        else:
            summary = summarize_company_alerts(company_name, new_rows)
            result = send_company_digest(recipients, company_name, summary, new_rows)
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
    }
