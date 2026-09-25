#!/usr/bin/env python3
"""Offline tests for regulatory → sales email helpers (no live API calls)."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_alert_store_dedup():
    store = load("alert_store", str(ROOT / "utils" / "alert_store.py"))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "alerts.json")
        os.environ["ALERTS_DB_PATH"] = path
        key_url = "https://news.example/article/1"
        assert store.was_sales_opportunity_processed("Acme", key_url) is False
        store.save_sales_opportunity_event("Acme", key_url)
        assert store.was_sales_opportunity_processed("Acme", key_url) is True
        assert store.was_alert_sent("Acme", "Fine announced") is False
        store.save_alert("Acme", "Fine announced")
        assert store.was_alert_sent("Acme", "Fine announced") is True
    print("OK alert_store_dedup")


def test_public_email_extract_and_score():
    discovery = load(
        "public_email_discovery",
        str(ROOT / "services" / "public_email_discovery.py"),
    )
    html = (
        '<a href="mailto:compliance@acme-bank.com">x</a>'
        "<p>contact@acme-bank.com</p>"
        '<img src="https://cdn.example.com/logo@2x.png"/>'
    )
    emails = discovery._extract_emails_from_html(html)
    assert "compliance@acme-bank.com" in emails
    assert "contact@acme-bank.com" in emails
    score, conf = discovery._score_email("compliance@acme-bank.com", "acme-bank.com")
    assert score >= 70 and conf == "high"
    # Invented personal email should score lower than role inbox on same domain
    score2, conf2 = discovery._score_email("jane.doe@acme-bank.com", "acme-bank.com")
    assert score2 < score

    # Full discover with stubs — no invented address when page empty
    search_mod = types.ModuleType("pipeline.search")
    search_mod.search_company_info = lambda _n: ("https://www.acme-bank.com", "")
    sys.modules["pipeline"] = types.ModuleType("pipeline")
    sys.modules["pipeline.search"] = search_mod

    with patch.object(
        discovery,
        "_candidate_contact_urls",
        return_value=["https://www.acme-bank.com/contact"],
    ), patch.object(
        discovery,
        "_fetch_page_text",
        return_value=(html, "company_website"),
    ):
        result = discovery.discover_public_business_email("Acme Bank")
    assert result["email"] in ("compliance@acme-bank.com", "contact@acme-bank.com")
    assert result["email_publicly_available"] is True

    with patch.object(
        discovery,
        "_candidate_contact_urls",
        return_value=["https://www.acme-bank.com/contact"],
    ), patch.object(
        discovery,
        "_fetch_page_text",
        return_value=("<html><body>Call us</body></html>", "company_website"),
    ):
        result2 = discovery.discover_public_business_email("Acme Bank")
    assert result2["email"] is None
    assert result2["email_publicly_available"] is False
    print("OK public_email_extract_and_score")


def test_sales_analyzer_placeholders_and_no_force():
    analyzer_mod = types.ModuleType("pipeline.analyzer")
    analyzer_mod.NVIDIA_API_URL = "https://example.com"
    helpers_mod = types.ModuleType("utils.helpers")
    helpers_mod.retry = lambda *a, **k: (lambda fn: fn)
    sys.modules["pipeline"] = types.ModuleType("pipeline")
    sys.modules["pipeline.analyzer"] = analyzer_mod
    sys.modules["utils"] = types.ModuleType("utils")
    sys.modules["utils.helpers"] = helpers_mod

    analyzer = load(
        "regulatory_sales_analyzer",
        str(ROOT / "pipeline" / "regulatory_sales_analyzer.py"),
    )
    os.environ["SALES_PERSON_NAME"] = "Alex Rivera"
    os.environ["SALES_COMPANY_NAME"] = "Hawk"
    os.environ["SALES_COMPANY_WEBSITE"] = "https://hawk.example"
    os.environ["NVIDIA_API_KEY"] = "test-key"

    fake = {
        "company_name": "Acme Bank",
        "event_title": "FCA AML investigation",
        "sales_opportunity": True,
        "problem_identified": "Manual KYC review may increase",
        "business_impact": "Compliance workload",
        "recommended_solution": "Compliance Automation",
        "solution_reason": "Automates KYC document review",
        "subject_options": [
            "A thought on AML compliance workload",
            "Reducing KYC review friction",
            "Acme Bank + compliance automation",
        ],
        "recommended_subject": "A thought on AML compliance workload",
        "email_body": (
            "{{contact_greeting}}\n\n"
            "I came across the FCA AML investigation involving Acme Bank.\n\n"
            "Best,\n{{sales_person}}\n{{our_company}}\n{{our_website}}"
        ),
        "contact_role_suggestion": "Compliance Team",
    }
    with patch.object(analyzer, "_nvidia_json", return_value=fake):
        out = analyzer.analyze_regulatory_sales_opportunity(
            "Acme Bank",
            "FCA AML investigation",
            "Regulator opened an AML investigation.",
            source_url="https://news.example/acme",
            contact_greeting="Hi Compliance Team,",
        )
    assert out["sales_opportunity"] is True
    assert "Alex Rivera" in out["email_body"]
    assert "Hawk" in out["email_body"]
    assert "Hi Compliance Team," in out["email_body"]

    fake_no = dict(fake, sales_opportunity=False, email_body="", subject_options=[])
    with patch.object(analyzer, "_nvidia_json", return_value=fake_no):
        out2 = analyzer.analyze_regulatory_sales_opportunity(
            "Acme Bank", "Minor hiring news", "Company hired a marketer."
        )
    assert out2["sales_opportunity"] is False
    assert out2["email_body"] == ""
    print("OK sales_analyzer_placeholders")


def test_digest_includes_sales_section():
    recipients = types.ModuleType("utils.email_recipients")
    recipients.resend_blocked_recipients = lambda x: []
    recipients.resend_test_mode_message = lambda: ""
    sys.modules["utils"] = types.ModuleType("utils")
    sys.modules["utils.email_recipients"] = recipients

    alerts = load("email_alerts", str(ROOT / "services" / "email_alerts.py"))
    captured = {}

    def fake_send(recips, subject, html):
        captured["subject"] = subject
        captured["html"] = html
        return {"ok": True, "sent_to": recips, "failed_to": {}}

    alerts.send_html_to_recipients = fake_send
    html_result = alerts.send_company_digest(
        ["rep@example.com"],
        "Acme Bank",
        {"summary": "Overview", "priority_actions": "Call them"},
        [{
            "headline": "FCA AML investigation",
            "url": "https://news.example/acme",
            "urgency": "urgent",
            "why_matters": "Compliance pressure",
            "talking_points": "Ask about KYC backlog",
        }],
        sales_opportunities=[{
            "company_name": "Acme Bank",
            "regulatory_event": "FCA AML investigation",
            "problem": "Manual KYC workload may rise",
            "business_impact": "Ops pressure",
            "solution": "Compliance Automation",
            "email": None,
            "email_publicly_available": False,
            "recommended_subject": "A thought on AML compliance workload",
            "email_body": "Hi Compliance Team,\n\nDraft body",
        }],
    )
    assert html_result["ok"]
    assert "Sales Opportunities" in captured["html"]
    assert "No publicly verified business email" in captured["html"]
    assert "A thought on AML compliance workload" in captured["html"]
    print("OK digest_includes_sales_section")


def test_news_query_selection():
    # Stub firecrawl so news_search can import
    fc = types.ModuleType("firecrawl")
    fc.Firecrawl = object
    sys.modules["firecrawl"] = fc
    news = load("news_search", str(ROOT / "pipeline" / "news_search.py"))
    queries = news._build_query_list("Acme", "finance")
    assert any("AML" in q for q in queries)
    assert any("banking regulation" in q for q in queries)
    print("OK news_query_selection")


if __name__ == "__main__":
    test_alert_store_dedup()
    test_public_email_extract_and_score()
    test_sales_analyzer_placeholders_and_no_force()
    test_digest_includes_sales_section()
    test_news_query_selection()
    print("ALL_PASSED")
