#!/usr/bin/env python3
"""Verification harness for multi-agent outreach edge cases (run from repo root)."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
import copy
import urllib.request
from typing import Any, Dict
from unittest import mock

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv

load_dotenv(".env", override=True)

from services.agents import store as agent_store
from services.agents import config as agent_config
from services.agents import leads as lead_scan
from services.agents.outreach_copywriter import run_outreach_scan, draft_for_lead
from services.agents.call_trigger import (
    run_call_trigger,
    start_vapi_call,
    RULE_REPLIED,
    RULE_SCORE,
)

FAKE_VAPI_ENV = {
    "VAPI_API_KEY": "sk-test-vapi-verify-key-abc123",
    "VAPI_ASSISTANT_ID": "asst_verify_123",
    "VAPI_PHONE_NUMBER_ID": "pn_verify_456",
}


def http_json(method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = None
    headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:8000{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


class FakeResp:
    status_code = 201

    def json(self) -> Dict[str, Any]:
        return {"id": "call_fake_123", "status": "queued"}


def base_result(**overrides: Any) -> Dict[str, Any]:
    r = {
        "company_name": "X",
        "url": "https://x.example/",
        "summary": "B2B enterprise platform.",
        "industry": "Technology",
        "size_estimate": "1001+",
        "lead_score": 9,
        "status_tag": "Hot",
        "score_reason": "ORIGINAL score reason for verification.",
        "lead_score_rationale": "ORIGINAL rationale",
        "buying_signals": ["mentions AI", "case studies"],
        "phone": "+1 (415) 555-0101",
        "email": "sales@x.example",
        "error": None,
        "review_needed": False,
        "scrape_page_urls": ["https://x.example/", "https://x.example/about"],
        "scrape_fallback": False,
        "profile_scores": {
            "ai_automation_readiness": {
                "dimensions": {
                    "ai_maturity": {
                        "score": 7,
                        "reason": "mentions AI platform",
                        "name": "AI Maturity",
                    }
                }
            }
        },
    }
    r.update(overrides)
    return r


def write_job(job_id: str, result: Dict[str, Any]) -> str:
    jobs_dir = agent_config.jobs_dir()
    os.makedirs(jobs_dir, exist_ok=True)
    path = os.path.join(jobs_dir, f"{job_id}.json")
    job = {
        "job_id": job_id,
        "job_type": "search",
        "status": "done",
        "created_at": time.time(),
        "results": [result],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f)
    return path


def scenario_1() -> Dict[str, Any]:
    job_id = f"verify-idem-{uuid.uuid4().hex[:10]}"
    company = f"VerifyIdem{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(job_id, base_result(company_name=company, url=url))

    scan1 = run_outreach_scan(limit=50)
    drafts1 = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key]
    assert len(drafts1) == 1
    original_body_preview = drafts1[0]["body"][:120]
    original_snap_reason = (drafts1[0].get("lead_snapshot") or {}).get("score_reason")

    mutated = base_result(
        company_name=company,
        url=url,
        lead_score=10,
        score_reason="CHANGED after re-process: new evidence from pipeline update.",
        summary="CHANGED summary after re-score.",
    )
    write_job(job_id, mutated)

    eligible_changed = [
        e for e in lead_scan.iter_qualified_outreach_leads(skip_processed=False) if e["lead_key"] == lead_key
    ][0]
    not_in_skip_iter = [
        e for e in lead_scan.iter_qualified_outreach_leads(skip_processed=True) if e["lead_key"] == lead_key
    ]
    scan2 = run_outreach_scan(limit=50)
    direct = draft_for_lead(eligible_changed)
    drafts2 = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key]

    return {
        "pass": len(drafts2) == 1 and direct.get("skipped") is True and not_in_skip_iter == [],
        "lead_key": lead_key,
        "scan1_created": scan1.get("created"),
        "scan2": {"created": scan2.get("created"), "skipped": scan2.get("skipped")},
        "direct_draft_for_lead": direct,
        "draft_count": len(drafts2),
        "job_score_reason_after_change": eligible_changed["snapshot"]["score_reason"],
        "draft_snapshot_reason_unchanged": (drafts2[0].get("lead_snapshot") or {}).get("score_reason"),
        "original_body_preview": original_body_preview,
    }


def scenario_2() -> Dict[str, Any]:
    job_id = f"verify-edit-{uuid.uuid4().hex[:10]}"
    company = f"VerifyEdit{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(job_id, base_result(company_name=company, url=url, phone="+1 (415) 555-0199"))
    run_outreach_scan(limit=50)
    draft = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key][0]

    original_subject = draft["subject"]
    original_body = draft["body"]
    edit_subject = f"EDIT-SUBJECT-{uuid.uuid4().hex[:8]}"
    edit_body = f"EDIT-BODY-TOKEN-{uuid.uuid4().hex}"

    patched = http_json("PATCH", f"/api/agents/drafts/{draft['id']}", {"subject": edit_subject, "body": edit_body})
    approved = http_json("POST", f"/api/agents/drafts/{draft['id']}/approve", {})
    from_db = agent_store.get_draft(draft["id"])
    from_api = http_json("GET", f"/api/agents/drafts/{draft['id']}")

    return {
        "pass": (
            from_db["status"] == "approved_ready_to_send"
            and from_db["body"] == edit_body
            and from_db["subject"] == edit_subject
            and from_api["body"] == edit_body
            and original_body != edit_body
        ),
        "draft_id": draft["id"],
        "before": {"subject": original_subject, "body_preview": original_body[:200]},
        "edited_sent": {"subject": edit_subject, "body": edit_body},
        "after_patch": {"subject": patched["subject"], "body": patched["body"]},
        "after_approve": {"status": approved["status"], "subject": approved["subject"], "body": approved["body"]},
        "db_reload": {"status": from_db["status"], "subject": from_db["subject"], "body": from_db["body"]},
        "api_reload": {"status": from_api["status"], "subject": from_api["subject"], "body": from_api["body"]},
    }


def scenario_3() -> Dict[str, Any]:
    job_id = f"verify-vapi-{uuid.uuid4().hex[:10]}"
    company = f"VerifyVapi{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(
        job_id,
        base_result(company_name=company, url=url, lead_score=10, phone="+1 (628) 555-0144"),
    )

    noop = start_vapi_call(phone="+16285550144", company_name=company, lead_key=lead_key, rule=RULE_SCORE)
    captured: list[Dict[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append({"url": url, "headers": dict(headers or {}), "json": json})
        return FakeResp()

    with mock.patch.dict(os.environ, FAKE_VAPI_ENV, clear=False):
        assert agent_config.vapi_configured() is True
        with mock.patch("services.agents.call_trigger.requests.post", side_effect=fake_post):
            direct = start_vapi_call(
                phone="+1 (628) 555-0144",
                company_name=company,
                lead_key=lead_key,
                rule=RULE_SCORE,
            )
            if agent_store.has_call_event(lead_key, RULE_SCORE):
                pass  # already logged from prior run
            else:
                run_call_trigger(limit=50)

    sched = http_json("GET", "/api/agents/config")["scheduler"]
    events = [
        e
        for e in agent_store.list_call_events(limit=300)
        if e["lead_key"] == lead_key and e["rule"] == RULE_SCORE
    ]
    req = captured[0] if captured else None

    return {
        "pass": (
            noop["status"] == "skipped_no_vapi"
            and direct.get("ok") is True
            and direct.get("status") == "triggered"
            and req is not None
            and req["url"] == "https://api.vapi.ai/call/phone"
            and sched.get("running") is True
            and any(j["id"] == "call_trigger_poll" for j in sched.get("jobs") or [])
        ),
        "without_keys": noop,
        "with_mocked_keys": direct,
        "http_post_payload": req,
        "scheduler_jobs": sched.get("jobs"),
        "db_event_for_lead": events[0] if events else None,
    }


def scenario_5() -> Dict[str, Any]:
    job_id = f"verify-reply-{uuid.uuid4().hex[:10]}"
    company = f"VerifyReply{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    snap = lead_scan.snapshot_lead(
        base_result(
            company_name=company,
            url=url,
            lead_score=6,
            status_tag="Warm",
            phone="+1 (415) 555-0177",
            score_reason="Below call threshold — reply path only.",
        ),
        job_id=job_id,
    )
    write_job(
        job_id,
        base_result(
            company_name=company,
            url=url,
            lead_score=6,
            status_tag="Warm",
            phone="+1 (415) 555-0177",
        ),
    )
    draft = agent_store.insert_draft(
        draft_type="outreach",
        status="pending",
        subject="Reply test",
        body="Reply test body",
        company_name=company,
        lead_key=lead_key,
        job_id=job_id,
        lead_snapshot=snap,
    )
    score_cands = [c for c in lead_scan.iter_call_score_candidates() if c["lead_key"] == lead_key]
    http_json("POST", f"/api/agents/drafts/{draft['id']}/approve", {})
    replied = http_json("POST", f"/api/agents/drafts/{draft['id']}/replied", {})

    captured: list[Dict[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append({"json": json})
        return FakeResp()

    with mock.patch.dict(os.environ, FAKE_VAPI_ENV, clear=False):
        with mock.patch("services.agents.call_trigger.requests.post", side_effect=fake_post):
            out1 = run_call_trigger(limit=50)
            out2 = run_call_trigger(limit=50)

    reply_events = [
        e for e in agent_store.list_call_events(limit=300) if e["lead_key"] == lead_key and e["rule"] == RULE_REPLIED
    ]
    score_events = [
        e for e in agent_store.list_call_events(limit=300) if e["lead_key"] == lead_key and e["rule"] == RULE_SCORE
    ]

    return {
        "pass": (
            len(score_cands) == 0
            and replied.get("replied") is True
            and len(reply_events) == 1
            and reply_events[0]["status"] == "triggered"
            and len(score_events) == 0
            and len(captured) == 1
        ),
        "lead_score": 6,
        "score_candidates": len(score_cands),
        "replied_response": replied,
        "reply_event": reply_events[0] if reply_events else None,
        "score_events": score_events,
        "vapi_metadata": captured[0]["json"]["metadata"] if captured else None,
        "second_run_triggered": out2.get("triggered"),
    }


def scenario_6() -> Dict[str, Any]:
    job_id = f"verify-reject-{uuid.uuid4().hex[:10]}"
    company = f"VerifyReject{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(job_id, base_result(company_name=company, url=url))
    run_outreach_scan(limit=50)
    draft = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key][0]
    assert draft["status"] == "pending"

    rejected = http_json("POST", f"/api/agents/drafts/{draft['id']}/reject", {})
    pending_list = http_json("GET", "/api/agents/drafts?status=pending&limit=200")
    pending_ids = [d["id"] for d in pending_list.get("drafts") or []]

    scan_after = run_outreach_scan(limit=50)
    drafts_after = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key]

    return {
        "pass": (
            rejected["status"] == "rejected"
            and draft["id"] not in pending_ids
            and len(drafts_after) == 1
            and drafts_after[0]["status"] == "rejected"
            and scan_after.get("created") == 0
        ),
        "draft_id": draft["id"],
        "reject_response_status": rejected["status"],
        "still_in_pending_list": draft["id"] in pending_ids,
        "drafts_for_lead_after_rescan": [{"id": d["id"], "status": d["status"]} for d in drafts_after],
        "rescan_created": scan_after.get("created"),
        "is_lead_processed": agent_store.is_lead_processed(lead_key),
    }


def main() -> int:
    agent_store.ensure_db()
    report: Dict[str, Any] = {"scenarios": {}, "timestamp": time.time()}
    for name, fn in [
        ("1_rescoring_idempotency", scenario_1),
        ("2_edit_then_approve", scenario_2),
        ("3_vapi_trigger_path", scenario_3),
        ("5_mark_replied_call_trigger", scenario_5),
        ("6_reject_path", scenario_6),
    ]:
        try:
            report["scenarios"][name] = fn()
        except Exception as exc:  # noqa: BLE001
            report["scenarios"][name] = {"pass": False, "error": str(exc)}

    report["overall_pass"] = all(s.get("pass") for s in report["scenarios"].values())
    out_path = os.path.join("scripts", "last_agents_verify_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
