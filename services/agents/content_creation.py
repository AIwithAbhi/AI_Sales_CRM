"""Content Creation Agent — pattern analysis → LinkedIn/blog draft for approval."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
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


def analyze_patterns(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect simple patterns across recent lead snapshots."""
    industries = Counter()
    tiers = Counter()
    low_dims: Counter = Counter()
    hot = warm = cold = 0
    for snap in results:
        ind = (snap.get("industry") or "Unknown").strip() or "Unknown"
        industries[ind] += 1
        tier = (snap.get("enterprise_readiness_tier") or "n/a").strip() or "n/a"
        tiers[tier] += 1
        tag = (snap.get("status_tag") or "").strip().lower()
        if tag == "hot":
            hot += 1
        elif tag == "warm":
            warm += 1
        elif tag == "cold":
            cold += 1
        for reason in snap.get("profile_reasons") or []:
            # "AI Maturity (1/10): ..." → capture low scores
            m = re.search(r"^(.*?)\s*\((\d+)/10\)", str(reason))
            if m and int(m.group(2)) <= 3:
                low_dims[m.group(1).strip()] += 1

    top_industries = industries.most_common(5)
    top_gaps = low_dims.most_common(5)
    pattern_bits: List[str] = []
    if top_industries:
        names = ", ".join(f"{n} ({c})" for n, c in top_industries[:3])
        pattern_bits.append(f"Industry cluster: {names}")
    if top_gaps:
        gaps = ", ".join(f"{n} low ({c} leads)" for n, c in top_gaps[:3])
        pattern_bits.append(f"Recurring readiness gaps: {gaps}")
    if tiers:
        tstr = ", ".join(f"{n}={c}" for n, c in tiers.most_common())
        pattern_bits.append(f"Enterprise tiers: {tstr}")
    pattern_bits.append(f"Temperature mix: Hot={hot}, Warm={warm}, Cold={cold}")

    return {
        "lead_count": len(results),
        "top_industries": top_industries,
        "top_gaps": top_gaps,
        "tiers": dict(tiers),
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "summary_lines": pattern_bits,
    }


def _heuristic_content(patterns: Dict[str, Any]) -> Tuple[str, str, str]:
    lines = patterns.get("summary_lines") or []
    top_ind = patterns.get("top_industries") or []
    industry = top_ind[0][0] if top_ind else "enterprise"
    gaps = patterns.get("top_gaps") or []
    gap = gaps[0][0] if gaps else "AI maturity"
    title = f"What {patterns.get('lead_count', 0)} recent {industry} leads reveal about {gap}"
    body = (
        f"Over the last lookback window we reviewed {patterns.get('lead_count', 0)} scored leads.\n\n"
        + "\n".join(f"• {l}" for l in lines)
        + f"\n\nAngle: Publish a LinkedIn / short blog piece on why {gap.lower()} keeps showing up "
        f"in {industry} accounts — and what buying signals appear before teams invest in automation.\n\n"
        "CTA: Invite operators to share how they score readiness internally."
    )
    return title, f"Pattern insight: {industry} × {gap}", body


def _call_nvidia(patterns: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    api_key = _nvidia_key()
    if not api_key:
        return None
    summary = "\n".join(f"- {l}" for l in (patterns.get("summary_lines") or []))
    system = (
        "You are a B2B content strategist. Given lead-pattern stats, draft ONE LinkedIn/blog "
        "angle. Return ONLY JSON: "
        '{"title":"...","subject":"short teaser","body":"full draft 150-250 words"}.'
    )
    user = (
        f"Lead count: {patterns.get('lead_count')}\n"
        f"Patterns:\n{summary}\n\n"
        "Draft practical, non-hype content grounded in these patterns. No fabricated stats."
    )
    try:
        resp = requests.post(
            NVIDIA_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 900,
                "temperature": 0.4,
            },
            timeout=90,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(_strip_fences(text))
        title = (data.get("title") or "").strip()
        subject = (data.get("subject") or title).strip()
        body = (data.get("body") or "").strip()
        if title and body:
            return title, subject, body
    except Exception as exc:  # noqa: BLE001
        print(f"[agents/content] NVIDIA draft failed: {exc}")
    return None


def run_content_creation(*, lookback_days: Optional[int] = None) -> Dict[str, Any]:
    """Aggregate recent leads, detect patterns, insert a content draft."""
    if not agent_config.agents_enabled():
        return {"ok": False, "reason": "agents_disabled"}
    agent_store.ensure_db()
    results = lead_scan.collect_recent_results(lookback_days=lookback_days)
    if len(results) < 2:
        return {
            "ok": False,
            "reason": "insufficient_leads",
            "lead_count": len(results),
            "message": "Need at least 2 scored leads in the lookback window.",
        }
    patterns = analyze_patterns(results)
    pair = _call_nvidia(patterns) or _heuristic_content(patterns)
    title, subject, body = pair
    context = " | ".join(patterns.get("summary_lines") or [])[:400]
    draft = agent_store.insert_draft(
        draft_type="content",
        status="pending",
        title=title,
        subject=subject,
        body=body,
        company_name="",
        lead_key="",
        job_id="",
        lead_snapshot={"patterns": patterns, "sample_size": len(results)},
        context_summary=context,
    )
    return {
        "ok": True,
        "draft": draft,
        "patterns": patterns,
        "lead_count": len(results),
    }
