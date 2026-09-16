"""APScheduler jobs for overnight automation (feedback loop at midnight)."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_scheduler = None


def feedback_enabled() -> bool:
    return os.getenv("FEEDBACK_SCHEDULER_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def feedback_cron() -> dict:
    """
    Cron trigger kwargs. Defaults to 00:00 local/server time.

    Override with FEEDBACK_CRON_HOUR / FEEDBACK_CRON_MINUTE or
    FEEDBACK_CRON="0 0 * * *" (minute hour day month day_of_week).
    """
    raw = (os.getenv("FEEDBACK_CRON") or "").strip()
    if raw:
        parts = raw.split()
        if len(parts) == 5:
            return {
                "minute": parts[0],
                "hour": parts[1],
                "day": parts[2],
                "month": parts[3],
                "day_of_week": parts[4],
            }
    return {
        "minute": os.getenv("FEEDBACK_CRON_MINUTE", "0"),
        "hour": os.getenv("FEEDBACK_CRON_HOUR", "0"),
        "day": "*",
        "month": "*",
        "day_of_week": "*",
    }


def _run_feedback_job() -> None:
    from services.feedback_runner import run_feedback_loop

    logger.info("Starting scheduled Airtable feedback loop")
    try:
        summary = run_feedback_loop(write_airtable=True, send_email=True)
        logger.info("Feedback loop finished: %s", summary.get("log_message"))
    except Exception:
        logger.exception("Feedback loop failed")


def start_scheduler() -> Optional[Any]:
    """Start background scheduler (no-op if disabled or already running)."""
    global _scheduler
    if not feedback_enabled():
        logger.info("Feedback scheduler disabled (FEEDBACK_SCHEDULER_ENABLED=false)")
        return None
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning(
            "apscheduler not installed — nightly feedback loop unavailable. "
            "pip install apscheduler"
        )
        return None

    sched = BackgroundScheduler(timezone=os.getenv("FEEDBACK_TZ", "UTC"))
    trigger = CronTrigger(**feedback_cron())
    sched.add_job(
        _run_feedback_job,
        trigger=trigger,
        id="airtable_feedback_loop",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    logger.info("Feedback scheduler started with cron %s", feedback_cron())
    print(f"[scheduler] Airtable feedback loop scheduled: {feedback_cron()}")
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
