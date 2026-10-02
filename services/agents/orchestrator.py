"""APScheduler orchestrator for marketing agents (wired in FastAPI lifespan)."""

from __future__ import annotations

import atexit
import threading
from typing import Any, Dict, Optional

from services.agents import config as agent_config
from services.agents import store as agent_store

_scheduler = None
_lock = threading.Lock()


def _safe_outreach() -> None:
    try:
        from services.agents.outreach_copywriter import run_outreach_scan

        out = run_outreach_scan(limit=15)
        print(
            f"[agents] outreach_scan created={out.get('created')} "
            f"skipped={out.get('skipped')}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[agents] outreach_scan error: {exc}")


def _safe_call_trigger() -> None:
    try:
        from services.agents.call_trigger import run_call_trigger

        out = run_call_trigger(limit=20)
        print(
            f"[agents] call_trigger triggered={out.get('triggered')} "
            f"vapi={out.get('vapi_configured')}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[agents] call_trigger error: {exc}")


def _safe_content() -> None:
    try:
        from services.agents.content_creation import run_content_creation

        out = run_content_creation()
        if out.get("ok"):
            print(f"[agents] content_weekly draft={out.get('draft', {}).get('id')}")
        else:
            print(f"[agents] content_weekly skipped: {out.get('reason')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[agents] content_weekly error: {exc}")


def start_scheduler() -> Optional[Any]:
    """Start background jobs. Returns scheduler or None if disabled/unavailable."""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return _scheduler
        if not agent_config.agents_enabled():
            print("[agents] AGENTS_ENABLED=false — orchestrator not started")
            return None
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError:
            print("[agents] apscheduler not installed — orchestrator disabled")
            return None

        agent_store.ensure_db()
        sched = BackgroundScheduler(timezone="UTC")
        sched.add_job(
            _safe_outreach,
            IntervalTrigger(minutes=3),
            id="outreach_scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.add_job(
            _safe_call_trigger,
            IntervalTrigger(minutes=3),
            id="call_trigger_poll",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.add_job(
            _safe_content,
            CronTrigger(day_of_week="sun", hour=9, minute=0),
            id="content_weekly",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.start()
        _scheduler = sched
        atexit.register(stop_scheduler)
        print(
            "[agents] orchestrator started "
            "(outreach + call every 3m, content Sun 09:00 UTC)"
        )
        return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[agents] scheduler shutdown: {exc}")
        _scheduler = None
        print("[agents] orchestrator stopped")


def scheduler_status() -> Dict[str, Any]:
    jobs = []
    if _scheduler is not None:
        for job in _scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "next_run_time": str(job.next_run_time) if job.next_run_time else None,
                }
            )
    return {
        "running": _scheduler is not None and bool(getattr(_scheduler, "running", False)),
        "agents_enabled": agent_config.agents_enabled(),
        "jobs": jobs,
        "config": agent_config.public_config(),
    }
