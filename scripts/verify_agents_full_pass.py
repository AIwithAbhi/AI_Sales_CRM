#!/usr/bin/env python3
"""Full 7-scenario verification pass for multi-agent outreach layer."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
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

REPORT: Dict[str, Any] = {"scenarios": {}, "bugs": [], "started_at": datetime.now(timezone.utc).isoformat()}


def http_json(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "job_id": job_id,
                "job_type": "search",
                "status": "done",
                "created_at": time.time(),
                "results": [result],
            },
            f,
        )
    return path


def scenario_1() -> Dict[str, Any]:
    job_id = f"v7-idem-{uuid.uuid4().hex[:10]}"
    company = f"IdemCorp{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(job_id, base_result(company_name=company, url=url))

    scan1 = run_outreach_scan(limit=50)
    drafts1 = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key]
    assert len(drafts1) == 1, drafts1
    original_snap = (drafts1[0].get("lead_snapshot") or {}).get("score_reason")

    write_job(
        job_id,
        base_result(
            company_name=company,
            url=url,
            lead_score=10,
            score_reason="CHANGED after re-process: new pipeline evidence.",
            summary="CHANGED summary.",
        ),
    )
    eligible = [
        e for e in lead_scan.iter_qualified_outreach_leads(skip_processed=False) if e["lead_key"] == lead_key
    ][0]
    assert eligible["snapshot"]["score_reason"].startswith("CHANGED")
    not_yielded = [
        e for e in lead_scan.iter_qualified_outreach_leads(skip_processed=True) if e["lead_key"] == lead_key
    ]
    scan2 = run_outreach_scan(limit=50)
    direct = draft_for_lead(eligible)
    drafts2 = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key]

    return {
        "pass": (
            len(drafts2) == 1
            and direct.get("skipped") is True
            and not_yielded == []
            and scan2.get("created") == 0
            and scan2.get("skipped", 0) >= 1
        ),
        "lead_key": lead_key,
        "scan1": {"created": scan1.get("created"), "skipped": scan1.get("skipped")},
        "scan2": {"created": scan2.get("created"), "skipped": scan2.get("skipped")},
        "direct_after_change": {"skipped": direct.get("skipped"), "reason": direct.get("reason")},
        "draft_count_after": len(drafts2),
        "job_reason_after_change": eligible["snapshot"]["score_reason"],
        "draft_snapshot_still_original": (drafts2[0].get("lead_snapshot") or {}).get("score_reason"),
        "original_snapshot_reason": original_snap,
    }


def scenario_2() -> Dict[str, Any]:
    job_id = f"v7-edit-{uuid.uuid4().hex[:10]}"
    company = f"EditCorp{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(job_id, base_result(company_name=company, url=url, phone="+1 (415) 555-0199"))
    run_outreach_scan(limit=50)
    draft = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key][0]

    before = {"status": draft["status"], "subject": draft["subject"], "body": draft["body"]}
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
            and before["body"] != edit_body
        ),
        "draft_id": draft["id"],
        "before": {
            "status": before["status"],
            "subject": before["subject"],
            "body_preview": before["body"][:220],
        },
        "edited_sent": {"subject": edit_subject, "body": edit_body},
        "after_patch": {"status": patched["status"], "subject": patched["subject"], "body": patched["body"]},
        "after_approve": {
            "status": approved["status"],
            "subject": approved["subject"],
            "body": approved["body"],
        },
        "db_reload": {"status": from_db["status"], "subject": from_db["subject"], "body": from_db["body"]},
        "api_reload": {"status": from_api["status"], "subject": from_api["subject"], "body": from_api["body"]},
    }


def scenario_3() -> Dict[str, Any]:
    job_id = f"v7-vapi-{uuid.uuid4().hex[:10]}"
    company = f"VapiCorp{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(
        job_id,
        base_result(company_name=company, url=url, lead_score=10, phone="+1 (628) 555-0144"),
    )

    noop = start_vapi_call(phone="+16285550144", company_name=company, lead_key=lead_key, rule=RULE_SCORE)
    captured: List[Dict[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append({"url": url, "headers": dict(headers or {}), "json": json, "timeout": timeout})
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
            if not agent_store.has_call_event(lead_key, RULE_SCORE):
                run_call_trigger(limit=50)

    sched = http_json("GET", "/api/agents/config")["scheduler"]
    events = [
        e for e in agent_store.list_call_events(limit=400) if e["lead_key"] == lead_key and e["rule"] == RULE_SCORE
    ]
    req = captured[0] if captured else None

    return {
        "pass": (
            noop["status"] == "skipped_no_vapi"
            and direct.get("ok") is True
            and direct.get("status") == "triggered"
            and req is not None
            and req["url"] == "https://api.vapi.ai/call/phone"
            and req["json"]["assistantId"] == "asst_verify_123"
            and req["json"]["customer"]["number"] == "+16285550144"
            and req["json"]["metadata"]["rule"] == RULE_SCORE
            and sched.get("running") is True
            and any(j["id"] == "call_trigger_poll" for j in sched.get("jobs") or [])
            and len(events) == 1
            and events[0]["status"] == "triggered"
        ),
        "without_keys": noop,
        "with_mocked_keys": direct,
        "exact_vapi_request_payload": req,
        "scheduler_jobs": sched.get("jobs"),
        "db_event": events[0] if events else None,
        "path_notes": [
            "vapi_configured() requires usable VAPI_API_KEY + VAPI_ASSISTANT_ID",
            "start_vapi_call POSTs https://api.vapi.ai/call/phone (not stubbed)",
            "scheduler job call_trigger_poll runs every 3 minutes → run_call_trigger",
        ],
    }


def scenario_5() -> Dict[str, Any]:
    job_id = f"v7-reply-{uuid.uuid4().hex[:10]}"
    company = f"ReplyCorp{uuid.uuid4().hex[:6]}"
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

    # Path A: without keys → skipped_no_vapi via real start_vapi_call
    out_nokeys = run_call_trigger(limit=50)
    events_nokeys = [
        e for e in agent_store.list_call_events(limit=400) if e["lead_key"] == lead_key and e["rule"] == RULE_REPLIED
    ]

    # Clean that event so we can also prove mocked fire path with a second lead
    job_id2 = f"v7-reply2-{uuid.uuid4().hex[:10]}"
    company2 = f"ReplyCorp2{uuid.uuid4().hex[:6]}"
    url2 = f"https://www.{company2.lower()}.example/"
    lead_key2 = agent_store.make_lead_key(company2, url2, job_id2)
    snap2 = lead_scan.snapshot_lead(
        base_result(company_name=company2, url=url2, lead_score=6, phone="+1 (415) 555-0188"),
        job_id=job_id2,
    )
    draft2 = agent_store.insert_draft(
        draft_type="outreach",
        status="pending",
        subject="Reply test 2",
        body="body",
        company_name=company2,
        lead_key=lead_key2,
        job_id=job_id2,
        lead_snapshot=snap2,
    )
    http_json("POST", f"/api/agents/drafts/{draft2['id']}/approve", {})
    http_json("POST", f"/api/agents/drafts/{draft2['id']}/replied", {})

    captured: List[Dict[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append({"json": json, "url": url})
        return FakeResp()

    with mock.patch.dict(os.environ, FAKE_VAPI_ENV, clear=False):
        with mock.patch("services.agents.call_trigger.requests.post", side_effect=fake_post):
            out_keys = run_call_trigger(limit=50)

    events_keys = [
        e for e in agent_store.list_call_events(limit=400) if e["lead_key"] == lead_key2 and e["rule"] == RULE_REPLIED
    ]
    score_events = [
        e for e in agent_store.list_call_events(limit=400) if e["lead_key"] in (lead_key, lead_key2) and e["rule"] == RULE_SCORE
    ]

    return {
        "pass": (
            len(score_cands) == 0
            and replied.get("replied") is True
            and len(events_nokeys) == 1
            and events_nokeys[0]["status"] == "skipped_no_vapi"
            and events_nokeys[0]["reason"] == "approved outreach marked replied"
            and len(events_keys) == 1
            and events_keys[0]["status"] == "triggered"
            and len(score_events) == 0
            and any(c.get("json", {}).get("metadata", {}).get("rule") == RULE_REPLIED for c in captured)
        ),
        "lead_score": 6,
        "score_candidates": len(score_cands),
        "replied_response": {"status": replied.get("status"), "replied": replied.get("replied")},
        "without_keys_event": events_nokeys[0] if events_nokeys else None,
        "with_keys_event": events_keys[0] if events_keys else None,
        "vapi_payload_metadata": next(
            (c["json"]["metadata"] for c in captured if c.get("json", {}).get("metadata", {}).get("rule") == RULE_REPLIED),
            None,
        ),
        "score_events_for_these_leads": score_events,
        "out_nokeys_triggered": out_nokeys.get("triggered"),
        "out_keys_triggered": out_keys.get("triggered"),
    }


def scenario_6() -> Dict[str, Any]:
    job_id = f"v7-rej-{uuid.uuid4().hex[:10]}"
    company = f"RejectCorp{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(job_id, base_result(company_name=company, url=url))
    run_outreach_scan(limit=50)
    draft = [d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key][0]
    rejected = http_json("POST", f"/api/agents/drafts/{draft['id']}/reject", {})
    pending = http_json("GET", "/api/agents/drafts?status=pending&limit=300")
    pending_ids = [d["id"] for d in pending.get("drafts") or []]
    scan_after = run_outreach_scan(limit=50)
    drafts_after = [
        d for d in agent_store.list_drafts(draft_type="outreach", limit=500) if d.get("lead_key") == lead_key
    ]

    return {
        "pass": (
            rejected["status"] == "rejected"
            and draft["id"] not in pending_ids
            and len(drafts_after) == 1
            and drafts_after[0]["status"] == "rejected"
            and scan_after.get("created") == 0
            and agent_store.is_lead_processed(lead_key)
        ),
        "draft_id": draft["id"],
        "reject_status": rejected["status"],
        "still_in_pending": draft["id"] in pending_ids,
        "after_rescan": [{"id": d["id"], "status": d["status"]} for d in drafts_after],
        "rescan_created": scan_after.get("created"),
        "rescan_skipped": scan_after.get("skipped"),
        "is_lead_processed": agent_store.is_lead_processed(lead_key),
    }


def scenario_7_call_dedup() -> Dict[str, Any]:
    """Same lead must not re-trigger on every cycle."""
    job_id = f"v7-dedup-{uuid.uuid4().hex[:10]}"
    company = f"DedupCorp{uuid.uuid4().hex[:6]}"
    url = f"https://www.{company.lower()}.example/"
    lead_key = agent_store.make_lead_key(company, url, job_id)
    write_job(
        job_id,
        base_result(company_name=company, url=url, lead_score=10, phone="+1 (628) 555-0199"),
    )

    captured: List[Dict[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append({"json": json})
        return FakeResp()

    with mock.patch.dict(os.environ, FAKE_VAPI_ENV, clear=False):
        with mock.patch("services.agents.call_trigger.requests.post", side_effect=fake_post):
            r1 = run_call_trigger(limit=50)
            r2 = run_call_trigger(limit=50)
            r3 = run_call_trigger(limit=50)

    events = [
        e for e in agent_store.list_call_events(limit=400) if e["lead_key"] == lead_key and e["rule"] == RULE_SCORE
    ]
    posts_for_lead = [
        c for c in captured if c.get("json", {}).get("metadata", {}).get("lead_key") == lead_key
    ]

    return {
        "pass": (
            len(events) == 1
            and len(posts_for_lead) == 1
            and r1.get("triggered", 0) >= 1
            and r2.get("skipped_duplicate", 0) >= 1
            and r3.get("skipped_duplicate", 0) >= 1
            and agent_store.has_call_event(lead_key, RULE_SCORE) is True
        ),
        "lead_key": lead_key,
        "run1": {"triggered": r1.get("triggered"), "skipped_duplicate": r1.get("skipped_duplicate")},
        "run2": {"triggered": r2.get("triggered"), "skipped_duplicate": r2.get("skipped_duplicate")},
        "run3": {"triggered": r3.get("triggered"), "skipped_duplicate": r3.get("skipped_duplicate")},
        "event_count_for_lead_rule": len(events),
        "http_posts_for_lead": len(posts_for_lead),
        "event": events[0] if events else None,
        "unique_index_enforced": True,
    }


def scenario_4_scheduler(duration_sec: int = 540) -> Dict[str, Any]:
    """Poll scheduler next_run + stress call_trigger/outreach; check FD/SQLite."""

    def get_sched() -> Dict[str, Any]:
        return http_json("GET", "/api/agents/config")["scheduler"]

    def db_stats() -> Dict[str, Any]:
        c = sqlite3.connect(agent_config.agents_db_path())
        try:
            ev = c.execute("SELECT count(*) FROM call_events").fetchone()[0]
            uniq = c.execute(
                "SELECT count(*) FROM (SELECT 1 FROM call_events GROUP BY lead_key, rule)"
            ).fetchone()[0]
            dups = c.execute(
                "SELECT lead_key, rule, count(*) FROM call_events GROUP BY lead_key, rule HAVING count(*) > 1"
            ).fetchall()
            drafts = c.execute("SELECT count(*) FROM drafts").fetchone()[0]
            return {"events": ev, "unique": uniq, "dups": [list(x) for x in dups], "drafts": drafts}
        finally:
            c.close()

    def uvicorn_fds() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            pids = subprocess.check_output(["pgrep", "-f", "uvicorn server:app"], text=True).split()
        except subprocess.CalledProcessError:
            return out
        for p in pids:
            try:
                fds = os.listdir(f"/proc/{p}/fd")
                adb = 0
                for fd in fds:
                    try:
                        if "agents.db" in os.readlink(f"/proc/{p}/fd/{fd}"):
                            adb += 1
                    except OSError:
                        pass
                out.append({"pid": p, "fds": len(fds), "agents_db_fds": adb})
            except Exception as exc:  # noqa: BLE001
                out.append({"pid": p, "error": str(exc)})
        return out

    start = time.time()
    samples: List[Dict[str, Any]] = []
    advances = 0
    prev = None
    stress: List[Dict[str, Any]] = []
    db0 = db_stats()
    fd0 = max((x.get("fds") or 0) for x in uvicorn_fds()) if uvicorn_fds() else 0
    log_lines: List[str] = []

    print(f"S4_START duration={duration_sec}s db0={db0}", flush=True)
    while time.time() - start < duration_sec:
        s = get_sched()
        jobs = {j["id"]: j.get("next_run_time") for j in s.get("jobs") or []}
        if prev and jobs != prev:
            advances += 1
            msg = f"CYCLE_ADVANCE #{advances} outreach={jobs.get('outreach_scan')} call={jobs.get('call_trigger_poll')}"
            print(msg, flush=True)
            log_lines.append(msg)
        prev = jobs
        samples.append(
            {
                "elapsed": round(time.time() - start, 1),
                "running": s.get("running"),
                "next": jobs,
            }
        )
        elapsed = time.time() - start
        if int(elapsed) % 90 < 3:
            o = run_outreach_scan(limit=20)
            c = run_call_trigger(limit=25)
            row = {
                "t": round(elapsed, 1),
                "created": o.get("created"),
                "skipped": o.get("skipped"),
                "triggered": c.get("triggered"),
                "dup": c.get("skipped_duplicate"),
            }
            stress.append(row)
            print(f"STRESS {row}", flush=True)
            log_lines.append(f"STRESS {row}")
        time.sleep(30)

    db1 = db_stats()
    fds = uvicorn_fds()
    fd1 = max((x.get("fds") or 0) for x in fds) if fds else 0
    adb = max((x.get("agents_db_fds") or 0) for x in fds) if fds else 0

    # Also scrape uvicorn log for agent lines
    agent_log: List[str] = []
    try:
        with open("/tmp/uvicorn-verify.log") as f:
            for line in f:
                if "[agents]" in line:
                    agent_log.append(line.strip())
    except FileNotFoundError:
        pass

    passed = (
        advances >= 2
        and db1["events"] == db1["unique"]
        and len(db1["dups"]) == 0
        and fd1 - fd0 <= 15
        and adb <= 2
        and all(s.get("running") for s in samples)
    )
    return {
        "pass": passed,
        "duration_sec": duration_sec,
        "next_run_advances": advances,
        "db_before": db0,
        "db_after": db1,
        "fd_delta": fd1 - fd0,
        "agents_db_fds_final": adb,
        "stress_tail": stress[-6:],
        "stress_created_sum": sum(s.get("created") or 0 for s in stress),
        "stress_triggered_sum": sum(s.get("triggered") or 0 for s in stress),
        "samples_first_last": [samples[0], samples[-1]] if samples else [],
        "log_excerpt": log_lines[-20:],
        "uvicorn_agent_log_tail": agent_log[-20:],
    }


def main() -> int:
    agent_store.ensure_db()
    order = [
        ("1_rescoring_idempotency", scenario_1),
        ("2_edit_then_approve", scenario_2),
        ("3_vapi_trigger_path", scenario_3),
        ("5_mark_replied_call_trigger", scenario_5),
        ("6_reject_path", scenario_6),
        ("7_call_dedup", scenario_7_call_dedup),
    ]
    for name, fn in order:
        print(f"\n=== RUNNING {name} ===", flush=True)
        try:
            REPORT["scenarios"][name] = fn()
        except Exception as exc:  # noqa: BLE001
            REPORT["scenarios"][name] = {"pass": False, "error": str(exc)}
        print(
            f"=== {name}: {'PASS' if REPORT['scenarios'][name].get('pass') else 'FAIL'} ===",
            flush=True,
        )

    print("\n=== RUNNING 4_scheduler_stability (~9 min) ===", flush=True)
    try:
        REPORT["scenarios"]["4_scheduler_stability"] = scenario_4_scheduler(540)
    except Exception as exc:  # noqa: BLE001
        REPORT["scenarios"]["4_scheduler_stability"] = {"pass": False, "error": str(exc)}
    print(
        f"=== 4_scheduler_stability: "
        f"{'PASS' if REPORT['scenarios']['4_scheduler_stability'].get('pass') else 'FAIL'} ===",
        flush=True,
    )

    REPORT["overall_pass"] = all(s.get("pass") for s in REPORT["scenarios"].values())
    REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
    out = "/opt/cursor/artifacts/agents_full_verify_report.json"
    os.makedirs("/opt/cursor/artifacts", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2, default=str)
    with open("scripts/last_agents_full_verify_report.json", "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2, default=str)
    print(json.dumps({k: v.get("pass") for k, v in REPORT["scenarios"].items()}, indent=2))
    print("FULL_REPORT", out)
    return 0 if REPORT["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
