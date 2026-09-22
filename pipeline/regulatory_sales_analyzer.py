"""NVIDIA analysis: regulatory event → business problem → solution → sales email."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from pipeline.analyzer import NVIDIA_API_URL
from utils.helpers import retry

logger = logging.getLogger(__name__)

SOLUTION_CATEGORIES = [
    "AI Automation",
    "AI Agents",
    "Agentic AI",
    "AI Chatbots",
    "Workflow Automation",
    "Document Processing",
    "Data Extraction",
    "CRM Automation",
    "Regulatory Monitoring",
    "Compliance Automation",
    "Lead Intelligence",
    "Custom AI Solutions",
]

REGULATORY_SALES_SYSTEM_PROMPT = """You are a B2B regulatory intelligence and sales research analyst.

Analyze the supplied regulatory article about a company.

Separate verified facts from inference.
Identify the specific regulatory issue.
Determine what operational or business problem this development
could create for the company.
Identify which department is most likely affected.
Determine whether there is a credible opportunity for AI,
automation, workflow automation, AI agents, document processing,
CRM automation or compliance automation.

Only recommend a solution when it is logically connected to the
identified problem.
Never invent facts.
Never claim that a company has a problem unless the source
explicitly confirms it.
When the problem is inferred, clearly phrase the sales message
as a possibility rather than a fact.

Generate a concise, professional B2B sales email that references
the actual regulatory event and connects it naturally to the
relevant solution.
Do not make the email sound like mass marketing.

SOLUTION CATEGORIES (pick at most one primary when sales_opportunity=true):
AI Automation, AI Agents, Agentic AI, AI Chatbots, Workflow Automation,
Document Processing, Data Extraction, CRM Automation, Regulatory Monitoring,
Compliance Automation, Lead Intelligence, Custom AI Solutions.

Do NOT force a solution. If there is no credible opportunity, set
sales_opportunity=false and leave email fields empty.

EMAIL RULES:
- Mention company name, specific regulatory event, regulator when known,
  specific issue, likely operational impact, relevant solution, specific use case.
- Use tentative language for inferences: "may create", "could require",
  "appears relevant", "one area worth looking at".
- No fake statistics, fake customers, compliance guarantees, or spammy CTAs.
- Generate exactly 3 subject_options (professional, not spammy).
- Pick one recommended_subject.
- email_body should be plain text, ready to send. Use placeholders
  {{contact_greeting}}, {{sales_person}}, {{our_company}}, {{our_website}}
  which the system will fill in.
- Close with exactly:
  Best regards,
  {{sales_person}}
  Include {{our_company}} / {{our_website}} only if those placeholders are
  non-empty after filling — never invent a company name in the sign-off.

Return ONLY valid JSON with these fields:
{
  "company_name": "",
  "event_title": "",
  "event_date": "",
  "source_url": "",
  "source_name": "",
  "regulatory_body": "",
  "regulatory_topic": "",
  "event_type": "",
  "problem_identified": "",
  "business_impact": "",
  "affected_department": "",
  "urgency": "urgent|monitor|low",
  "potential_risk": "",
  "recommended_solution": "",
  "solution_reason": "",
  "sales_opportunity": false,
  "fact_vs_inference": {
    "confirmed_facts": [],
    "regulatory_requirements": [],
    "reported_allegations": [],
    "company_statements": [],
    "analyst_interpretation": []
  },
  "subject_options": ["", "", ""],
  "recommended_subject": "",
  "email_body": "",
  "contact_role_suggestion": ""
}
"""


def _sender_identity() -> Dict[str, str]:
    """Sender fields for email drafts. Empty company/website are allowed (omit from sign-off)."""
    if "SALES_PERSON_NAME" in os.environ:
        sales_person = (os.environ.get("SALES_PERSON_NAME") or "").strip()
    else:
        sales_person = (
            os.getenv("COLD_EMAIL_SENDER_NAME") or "Abhishek Hingu"
        ).strip()

    if "SALES_COMPANY_NAME" in os.environ:
        our_company = (os.environ.get("SALES_COMPANY_NAME") or "").strip()
    else:
        our_company = (os.getenv("COLD_EMAIL_PRODUCT_NAME") or "").strip()

    our_website = (os.getenv("SALES_COMPANY_WEBSITE") or "").strip()
    return {
        "sales_person": sales_person or "Abhishek Hingu",
        "our_company": our_company,
        "our_website": our_website,
    }


def _clean_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def _nvidia_json(system_prompt: str, user_message: str, max_tokens: int = 1400) -> Dict[str, Any]:
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY not set")

    payload = {
        "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(NVIDIA_API_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return json.loads(_clean_json_text(content))


def _normalize_signoff(body: str, identity: Dict[str, str]) -> str:
    """Ensure closing is 'Best regards,' + person name, with no company unless configured."""
    out = (body or "").strip()
    person = identity["sales_person"]
    company = identity["our_company"]
    website = identity["our_website"]

    # Drop leftover empty placeholder lines / accidental company-only lines after fill
    cleaned_lines: List[str] = []
    for line in out.splitlines():
        stripped = line.strip()
        if stripped in ("{{our_company}}", "{{our_website}}", "[Company]", "[Website]"):
            continue
        if not company and stripped and stripped.lower() in ("hawk", "our team", "our company"):
            # Avoid injecting a default vendor name when company is intentionally blank
            if person and person not in stripped:
                continue
        cleaned_lines.append(line.rstrip())
    out = "\n".join(cleaned_lines).strip()

    if person and person not in out:
        out = out.rstrip() + f"\n\nBest regards,\n{person}"
        if company:
            out += f"\n{company}"
        if website:
            out += f"\n{website}"
    return out.strip()


def _fill_email_placeholders(body: str, contact_greeting: str) -> str:
    identity = _sender_identity()
    website_line = identity["our_website"]
    replacements = {
        "{{contact_greeting}}": contact_greeting,
        "{{sales_person}}": identity["sales_person"],
        "{{our_company}}": identity["our_company"],
        "{{our_website}}": website_line,
        "[Sales Person]": identity["sales_person"],
        "[Company]": identity["our_company"],
        "[Website]": website_line,
    }
    out = body or ""
    for key, val in replacements.items():
        out = out.replace(key, val)
    return _normalize_signoff(out, identity)


@retry(max_attempts=2, delay=2.0)
def analyze_regulatory_sales_opportunity(
    company_name: str,
    headline: str,
    snippet: str,
    source_url: str = "",
    contact_greeting: str = "Hi there,",
) -> Dict[str, Any]:
    """
    Analyze a regulatory article and optionally produce a personalized sales email draft.

    Never invent emails/contacts — those are attached by the caller after discovery.
    """
    empty = {
        "company_name": company_name,
        "sales_opportunity": False,
        "event_title": headline,
        "source_url": source_url,
        "problem_identified": "",
        "business_impact": "",
        "recommended_solution": "",
        "solution_reason": "",
        "subject_options": [],
        "recommended_subject": "",
        "email_body": "",
        "error": None,
    }

    if not os.getenv("NVIDIA_API_KEY"):
        empty["error"] = "NVIDIA_API_KEY not set"
        return empty

    user_message = (
        f"Company: {company_name}\n"
        f"Headline: {headline}\n"
        f"Article summary/snippet: {snippet}\n"
        f"Source URL: {source_url or 'unknown'}\n\n"
        "Produce the structured regulatory sales analysis JSON."
    )

    try:
        result = _nvidia_json(REGULATORY_SALES_SYSTEM_PROMPT, user_message, max_tokens=1600)
    except Exception as exc:
        logger.warning("Regulatory sales analysis failed for %s: %s", company_name, exc)
        empty["error"] = str(exc)
        return empty

    sales_opp = bool(result.get("sales_opportunity"))
    subjects = result.get("subject_options") or []
    if not isinstance(subjects, list):
        subjects = [str(subjects)]
    subjects = [str(s).strip() for s in subjects if str(s).strip()][:3]
    while len(subjects) < 3 and sales_opp:
        subjects.append(f"A thought on {headline[:60]}")

    recommended = str(result.get("recommended_subject") or (subjects[0] if subjects else "")).strip()
    body = str(result.get("email_body") or "").strip()
    if sales_opp and body:
        body = _fill_email_placeholders(body, contact_greeting)

    solution = str(result.get("recommended_solution") or "").strip()
    if sales_opp and solution:
        # Soft-normalize to known categories when close
        for cat in SOLUTION_CATEGORIES:
            if cat.lower() in solution.lower() or solution.lower() in cat.lower():
                solution = cat
                break

    return {
        "company_name": company_name,
        "sales_opportunity": sales_opp,
        "event_title": str(result.get("event_title") or headline),
        "event_date": str(result.get("event_date") or ""),
        "source_url": source_url or str(result.get("source_url") or ""),
        "source_name": str(result.get("source_name") or ""),
        "regulatory_body": str(result.get("regulatory_body") or ""),
        "regulatory_topic": str(result.get("regulatory_topic") or ""),
        "event_type": str(result.get("event_type") or ""),
        "problem_identified": str(result.get("problem_identified") or ""),
        "business_impact": str(result.get("business_impact") or ""),
        "affected_department": str(result.get("affected_department") or ""),
        "urgency": str(result.get("urgency") or "monitor"),
        "potential_risk": str(result.get("potential_risk") or ""),
        "recommended_solution": solution if sales_opp else "",
        "solution_reason": str(result.get("solution_reason") or "") if sales_opp else "",
        "fact_vs_inference": result.get("fact_vs_inference") or {},
        "subject_options": subjects if sales_opp else [],
        "recommended_subject": recommended if sales_opp else "",
        "email_body": body if sales_opp else "",
        "contact_role_suggestion": str(result.get("contact_role_suggestion") or ""),
        "error": None,
    }
