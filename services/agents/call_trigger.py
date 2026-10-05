"""Call Trigger Agent — score/replied rules → Vapi outbound POST + event log."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from services.agents import config as agent_config
from services.agents import leads as lead_scan
from services.agents import store as agent_store
from services.lead_insights import validate_and_format_phone

VAPI_CALL_URL = "https://api.vapi.ai/call/phone"

RULE_SCORE = "score_threshold"
RULE_REPLIED = "replied"


def _normalize_phone(phone: str) -> str:
    if not phone or str(phone).strip().lower() in (
        "not available",
        "not found",
        "not stated on website",
    ):
        return ""
    formatted = validate_and_format_phone(phone or "")
    if not formatted or str(formatted).strip().lower() in (
        "not available",
        "not found",
        "not stated on website",
    ):
        return ""
    # Prefer E.164-ish for Vapi
    digits = "".join(c for c in formatted if c.isdigit())
    if formatted.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) >= 11:
        return "+" + digits
    return formatted


def start_vapi_call(
    *,
    phone: str,
    company_name: str,
    lead_key: str,
    rule: str,
) -> Dict[str, Any]:
    """POST outbound call to Vapi. No-ops with skipped status if unset."""
    if not agent_config.vapi_configured():
        return {
            "ok": False,
            "skipped": True,
            "status": "skipped_no_vapi",
            "error": "VAPI_API_KEY / VAPI_ASSISTANT_ID not configured",
        }
    number = _normalize_phone(phone)
    if not number:
        return {
            "ok": False,
            "skipped": True,
            "status": "skipped_no_phone",
            "error": "No valid phone number",
        }
    payload: Dict[str, Any] = {
        "assistantId": agent_config.vapi_assistant_id(),
        "customer": {"number": number},
        "metadata": {
            "company": company_name,
            "lead_key": lead_key,
            "rule": rule,
            "source": "ai_sales_crm_call_trigger",
        },
    }
    phone_number_id = agent_config.vapi_phone_number_id()
    if phone_number_id:
        payload["phoneNumberId"] = phone_number_id

    try:
        resp = requests.post(
            VAPI_CALL_URL,
            headers={
                "Authorization": f"Bearer {agent_config.vapi_api_key()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
        body: Any
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {"raw": resp.text[:500]}
        if resp.status_code >= 400:
            err = ""
            if isinstance(body, dict):
                err = str(body.get("message") or body.get("error") or body)[:400]
            else:
                err = str(body)[:400]
            return {
                "ok": False,
                "skipped": False,
                "status": "error",
                "error": err or f"HTTP {resp.status_code}",
                "response": body if isinstance(body, dict) else {},
            }
        call_id = ""
        if isinstance(body, dict):
            call_id = str(body.get("id") or body.get("callId") or "")
        return {
            "ok": True,
            "skipped": False,
            "status": "triggered",
            "vapi_call_id": call_id,
            "response": body if isinstance(body, dict) else {},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "skipped": False,
            "status": "error",
            "error": str(exc)[:400],
        }


def _trigger_once(
    *,
    lead_key: str,
    rule: str,
    company_name: str,
    phone: str,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if agent_store.has_call_event(lead_key, rule):
        return None
    result = start_vapi_call(
        phone=phone,
        company_name=company_name,
        lead_key=lead_key,
        rule=rule,
    )
    event = agent_store.insert_call_event(
        lead_key=lead_key,
        rule=rule,
        company_name=company_name,
        phone=phone,
        reason=reason,
        vapi_call_id=str(result.get("vapi_call_id") or ""),
        status=str(result.get("status") or "error"),
        error=str(result.get("error") or ""),
        metadata={
            **(metadata or {}),
            "vapi_ok": bool(result.get("ok")),
            "skipped": bool(result.get("skipped")),
        },
    )
    return event


def run_call_trigger(*, limit: int = 25) -> Dict[str, Any]:
    """Poll score-threshold leads and replied outreach; trigger Vapi once per rule."""
    if not agent_config.agents_enabled():
        return {"ok": False, "reason": "agents_disabled", "triggered": 0}
    agent_store.ensure_db()
    triggered: List[Dict[str, Any]] = []
    skipped_dup = 0
    max_n = max(1, min(int(limit), 50))

    for cand in lead_scan.iter_call_score_candidates():
        if len(triggered) >= max_n:
            break
        event = _trigger_once(
            lead_key=cand["lead_key"],
            rule=RULE_SCORE,
            company_name=cand["company_name"],
            phone=cand["phone"],
            reason=f"lead_score={cand['lead_score']} >= {agent_config.call_score_threshold()}",
            metadata={"job_id": cand.get("job_id"), "lead_score": cand.get("lead_score")},
        )
        if event is None:
            skipped_dup += 1
        else:
            triggered.append(event)

    for draft in agent_store.list_replied_outreach():
        if len(triggered) >= max_n:
            break
        snap = draft.get("lead_snapshot") or {}
        phone = _normalize_phone(str(snap.get("phone") or ""))
        if not phone:
            # Still log a skipped event once so we don't spin forever
            if agent_store.has_call_event(draft["lead_key"] or draft["id"], RULE_REPLIED):
                skipped_dup += 1
                continue
            event = agent_store.insert_call_event(
                lead_key=draft.get("lead_key") or draft["id"],
                rule=RULE_REPLIED,
                company_name=draft.get("company_name") or "",
                phone="",
                reason="replied but no phone on lead snapshot",
                status="skipped_no_phone",
                error="No phone on approved replied outreach",
                metadata={"draft_id": draft["id"]},
            )
            triggered.append(event)
            continue
        event = _trigger_once(
            lead_key=draft.get("lead_key") or draft["id"],
            rule=RULE_REPLIED,
            company_name=draft.get("company_name") or "",
            phone=phone,
            reason="approved outreach marked replied",
            metadata={"draft_id": draft["id"]},
        )
        if event is None:
            skipped_dup += 1
        else:
            triggered.append(event)

    return {
        "ok": True,
        "triggered": len(triggered),
        "skipped_duplicate": skipped_dup,
        "events": triggered,
        "vapi_configured": agent_config.vapi_configured(),
        "call_score_threshold": agent_config.call_score_threshold(),
    }
