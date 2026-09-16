"""AI analysis of regulatory news articles for sales alerts."""

import json
import os
from typing import Any, Dict, List

import requests
from utils.helpers import retry

from pipeline.analyzer import NVIDIA_API_URL

ALERT_SYSTEM_PROMPT = """You are a senior B2B regulatory-intelligence analyst for Hawk, an AML/KYC and \
anti-financial-crime compliance software vendor. Your job is to read one news item about a company and decide \
whether it is a sales trigger for Hawk.

RELEVANT topics (set is_relevant=true) — anything touching financial-crime compliance, e.g.:
- AML / KYC / CDD / sanctions / FinCEN / OFAC / FATF / transaction monitoring
- Regulatory fines, penalties, consent orders, enforcement actions, or formal investigations
- Compliance failures, control gaps, remediation programs, or new licensing/regulatory requirements
NOT relevant (set is_relevant=false): generic business news, funding, product launches, hiring, marketing, \
earnings, or anything unrelated to financial-crime compliance.

URGENCY:
- "urgent" — an active or recent enforcement action, fine, sanction, investigation, or a publicly disclosed \
  compliance gap. These mean the company is under pressure NOW and sales should reach out immediately.
- "not_relevant" — use for everything that is not urgent (including relevant-but-background news). \
  (The system only acts on urgent items.)

Return JSON with EXACTLY these fields and nothing else:
- is_relevant: boolean
- urgency: "urgent" or "not_relevant"
- why_matters: string — 2-3 crisp points (one paragraph) on why this signals a Hawk opportunity, naming the \
  specific risk (e.g. weak transaction monitoring, sanctions exposure, manual KYC, alert backlog).
- talking_points: string — 2-4 sentences a Hawk rep can say on a call. Be concrete and consultative, tie to \
  the news, and where it fits reference Hawk strengths (reducing false positives, real-time monitoring, \
  explainable AI screening). No fluff, no generic openers.

Be strict: if it is not clearly financial-crime/compliance related, set is_relevant=false and \
urgency="not_relevant". Return ONLY valid JSON. No markdown, no code fences, no commentary."""

DIGEST_SYSTEM_PROMPT = """You are a senior B2B regulatory-intelligence analyst for Hawk (AML/KYC and \
anti-financial-crime compliance software). You are given a company name and a set of recent, relevant \
regulatory news items about it (each with a headline and why it matters). Synthesize them into a single \
brief for a Hawk sales rep.

Return JSON with EXACTLY these fields:
- summary: string — 2-4 sentences giving the overall picture of this company's current regulatory/compliance \
  situation across ALL the items. Lead with the most material development.
- priority_actions: string — 2-4 sentences telling the rep what to do and say next, prioritized. Connect the \
  company's situation to Hawk's value (fewer false positives, real-time transaction monitoring, sanctions/KYC \
  screening, explainable AI). Be specific and actionable.

Return ONLY valid JSON. No markdown, no code fences, no commentary."""

DEFAULT_ALERT_ANALYSIS = {
    "is_relevant": False,
    "urgency": "not_relevant",
    "why_matters": "",
    "talking_points": "",
}


@retry(max_attempts=2, delay=2.0)
def analyze_article(company_name: str, headline: str, summary: str) -> Dict[str, Any]:
    """Classify article relevance and urgency for regulatory sales alerts."""
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        return DEFAULT_ALERT_ANALYSIS.copy()

    user_message = (
        f"Company: {company_name}\n"
        f"Headline: {headline}\n"
        f"Summary: {summary}"
    )

    payload = {
        "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
        "messages": [
            {"role": "system", "content": ALERT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 512,
        "temperature": 0,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            NVIDIA_API_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        response_text = response.json()["choices"][0]["message"]["content"]

        cleaned = response_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:].strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

        result = json.loads(cleaned)
        urgency = str(result.get("urgency", "not_relevant")).lower()
        if result.get("is_relevant") and urgency not in ("urgent", "not_relevant"):
            urgency = "urgent" if "urgent" in urgency else "not_relevant"

        return {
            "is_relevant": bool(result.get("is_relevant", False)),
            "urgency": urgency if result.get("is_relevant") else "not_relevant",
            "why_matters": str(result.get("why_matters", "")),
            "talking_points": str(result.get("talking_points", "")),
        }
    except Exception as e:
        print(f"Alert analysis error for '{company_name}': {e}")
        return DEFAULT_ALERT_ANALYSIS.copy()


def _nvidia_json(system_prompt: str, user_message: str, max_tokens: int = 512) -> Dict[str, Any]:
    """Call the NVIDIA chat API and return the parsed JSON dict. Raises on failure."""
    api_key = os.getenv("NVIDIA_API_KEY")
    payload = {
        "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
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
    response_text = response.json()["choices"][0]["message"]["content"]

    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return json.loads(cleaned)


def summarize_company_alerts(company_name: str, articles: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Synthesize a company's relevant regulatory articles into one digest:
    an overall summary plus prioritized sales actions for the consolidated email.
    """
    if not articles:
        return {"summary": "", "priority_actions": ""}

    # Fallback from the first article if the LLM is unavailable.
    fallback = {
        "summary": articles[0].get("why_matters", "") or articles[0].get("headline", ""),
        "priority_actions": articles[0].get("talking_points", ""),
    }
    if not os.getenv("NVIDIA_API_KEY"):
        return fallback

    lines = []
    for i, a in enumerate(articles[:8], 1):
        lines.append(
            f"{i}. [{a.get('urgency', '')}] {a.get('headline', '')}\n"
            f"   Why it matters: {a.get('why_matters', '')}"
        )
    user_message = (
        f"Company: {company_name}\n"
        "Relevant regulatory news items:\n" + "\n".join(lines)
    )

    try:
        result = _nvidia_json(DIGEST_SYSTEM_PROMPT, user_message, max_tokens=600)
        return {
            "summary": str(result.get("summary", "")) or fallback["summary"],
            "priority_actions": str(result.get("priority_actions", "")) or fallback["priority_actions"],
        }
    except Exception as e:
        print(f"Company digest summary error for '{company_name}': {e}")
        return fallback
