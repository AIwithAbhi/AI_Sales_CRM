"""Outreach Copywriter Agent — evidence-grounded cold email drafts via NVIDIA."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from services.agents import config as agent_config
from services.agents import leads as lead_scan
from services.agents import store as agent_store

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def _nvidia_key() -> Optional[str]:
    key = (os.getenv("NVIDIA_API_KEY") or "").strip()
    if not key:
        return None
    low = key.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return None
    return key


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _citations(snapshot: Dict[str, Any]) -> List[str]:
    cites: List[str] = []
    url = (snapshot.get("url") or "").strip()
    if url:
        cites.append(url)
    for u in snapshot.get("scrape_page_urls") or []:
        if isinstance(u, str) and u.strip() and u.strip() not in cites:
            cites.append(u.strip())
    return cites[:6]


def _evidence_lines(snapshot: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    if snapshot.get("score_reason"):
        lines.append(f"Score reason: {snapshot['score_reason']}")
    if snapshot.get("lead_score_rationale"):
        lines.append(f"Rationale: {snapshot['lead_score_rationale']}")
    if snapshot.get("summary"):
        lines.append(f"Summary: {snapshot['summary']}")
    for reason in snapshot.get("profile_reasons") or []:
        lines.append(f"Profile: {reason}")
    signals = snapshot.get("buying_signals") or []
    if signals:
        lines.append("Buying signals: " + ", ".join(str(s) for s in signals[:8]))
    return lines


def _heuristic_draft(snapshot: Dict[str, Any]) -> Tuple[str, str]:
    company = snapshot.get("company_name") or "your team"
    industry = snapshot.get("industry") or "your industry"
    score = snapshot.get("lead_score")
    cites = _citations(snapshot)
    cite_a = cites[0] if cites else (snapshot.get("url") or "your public site")
    cite_b = cites[1] if len(cites) > 1 else cite_a
    evidence = _evidence_lines(snapshot)
    e1 = evidence[0] if evidence else f"{company} operates in {industry}"
    e2 = evidence[1] if len(evidence) > 1 else f"Lead score {score}/10 with public buying signals"
    subject = f"Quick note on {company}'s {industry} priorities"
    body = (
        f"Hi —\n\n"
        f"I was looking at {company} ({cite_a}) and noticed {e1.lower() if not e1[:1].islower() else e1}. "
        f"Separately, {e2} (see also {cite_b}).\n\n"
        f"We help similar {industry} teams tighten AI / automation readiness without a rip-and-replace. "
        f"Open to a short conversation if that maps to what you're exploring?\n\n"
        f"Best regards"
    )
    return subject, body


def _call_nvidia(snapshot: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    api_key = _nvidia_key()
    if not api_key:
        return None
    cites = _citations(snapshot)
    evidence = "\n".join(f"- {line}" for line in _evidence_lines(snapshot))
    cite_block = "\n".join(f"- {c}" for c in cites) or "- (homepage URL only)"
    system = (
        "You write short B2B cold emails grounded ONLY in the provided evidence. "
        "Require at least two specific references (facts, URLs, or quoted signals). "
        "Refuse generic templates. Return ONLY JSON: "
        '{"subject":"...","body":"..."} with no markdown fences.'
    )
    user = (
        f"Company: {snapshot.get('company_name')}\n"
        f"Industry: {snapshot.get('industry')}\n"
        f"Size: {snapshot.get('size_estimate')}\n"
        f"Lead score: {snapshot.get('lead_score')} ({snapshot.get('status_tag')})\n"
        f"Enterprise tier: {snapshot.get('enterprise_readiness_tier')}\n\n"
        f"Evidence:\n{evidence}\n\n"
        f"Citation URLs:\n{cite_block}\n\n"
        "Write a 90–140 word email. Subject ≤ 60 chars. "
        "Mention two concrete evidence points and cite URLs when available."
    )
    payload = {
        "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 700,
        "temperature": 0.3,
    }
    try:
        resp = requests.post(
            NVIDIA_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(_strip_fences(text))
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
        if subject and body:
            return subject, body
    except Exception as exc:  # noqa: BLE001 — fall back to heuristic
        print(f"[agents/outreach] NVIDIA draft failed: {exc}")
    return None


def draft_for_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Create a pending outreach draft for one scanned lead."""
    snapshot = lead["snapshot"]
    lead_key = lead["lead_key"]
    job_id = lead.get("job_id") or ""
    if agent_store.is_lead_processed(lead_key):
        existing = agent_store.list_drafts(draft_type="outreach", limit=200)
        for d in existing:
            if d.get("lead_key") == lead_key:
                return {"skipped": True, "reason": "already_processed", "draft": d}
        return {"skipped": True, "reason": "already_processed"}

    pair = _call_nvidia(snapshot) or _heuristic_draft(snapshot)
    subject, body = pair
    context = (
        f"Score {snapshot.get('lead_score')}/10 · {snapshot.get('status_tag') or '—'} · "
        f"{snapshot.get('industry') or 'Industry n/a'}"
    )
    draft = agent_store.insert_draft(
        draft_type="outreach",
        status="pending",
        subject=subject,
        body=body,
        title=f"Outreach — {snapshot.get('company_name')}",
        company_name=snapshot.get("company_name") or "",
        lead_key=lead_key,
        job_id=job_id,
        lead_snapshot=snapshot,
        context_summary=context,
    )
    return {"skipped": False, "draft": draft}


def run_outreach_scan(*, limit: int = 20) -> Dict[str, Any]:
    """Scan done jobs and draft outreach for newly eligible leads."""
    if not agent_config.agents_enabled():
        return {"ok": False, "reason": "agents_disabled", "created": 0, "skipped": 0}
    agent_store.ensure_db()
    created = 0
    skipped = 0
    drafts: List[Dict[str, Any]] = []
    errors: List[str] = []
    for lead in lead_scan.iter_qualified_outreach_leads(skip_processed=True):
        if created >= max(1, min(int(limit), 50)):
            break
        try:
            out = draft_for_lead(lead)
            if out.get("skipped"):
                skipped += 1
            else:
                created += 1
                if out.get("draft"):
                    drafts.append(out["draft"])
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            print(f"[agents/outreach] draft error: {exc}")
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "drafts": drafts,
        "errors": errors,
        "threshold": agent_config.outreach_score_threshold(),
    }
