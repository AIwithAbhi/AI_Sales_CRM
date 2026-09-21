import json
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from utils.stdio import configure_stdio_utf8

configure_stdio_utf8()

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import fetch_from_airtable, generate_icp, push_to_airtable, recommend_companies
from pipeline.crm import push_regulatory_sales_opportunity
from services.lead_insights import (
    filter_public_email,
    generate_lead_explanation,
    get_lead_qualification_breakdown,
    validate_and_format_phone,
)
from services.alert_processing import process_company_alerts
from services.analytics import (
    clear_analytics_cache,
    compute_funnel,
    compute_industries,
    compute_recent_activity,
    compute_summary,
    compute_trend,
)
from services.email_alerts import send_test_email, smtp_configured
from services.lead_processing import process_company
from utils.email_recipients import (
    parse_recipient_emails,
    resend_account_email,
    resend_domain_verified,
    resend_test_mode_message,
)
from utils.funnel_log import log_funnel_stage

load_dotenv(override=True)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
JOBS_DIR = os.path.join(ROOT, "data", "jobs")


def _env_key_usable(name: str) -> bool:
    """True when an env var is set and not an obvious placeholder."""
    value = (os.getenv(name) or "").strip()
    if not value:
        return False
    low = value.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return False
    return True


def check_env_vars() -> Dict[str, bool]:
    return {
        "FIRECRAWL_API_KEY": _env_key_usable("FIRECRAWL_API_KEY"),
        "NVIDIA_API_KEY": _env_key_usable("NVIDIA_API_KEY"),
        "AIRTABLE_API_KEY": _env_key_usable("AIRTABLE_API_KEY"),
        "AIRTABLE_BASE_ID": _env_key_usable("AIRTABLE_BASE_ID"),
        "EMAIL_SENDER": _env_key_usable("EMAIL_SENDER"),
        "EMAIL_PASSWORD": _env_key_usable("EMAIL_PASSWORD"),
    }


def _validate_env() -> None:
    # Firecrawl/NVIDIA improve quality; URL lookup and ICP have heuristic fallbacks
    # when keys are missing or still placeholders (your_*_here).
    missing = [
        k for k in ("FIRECRAWL_API_KEY", "NVIDIA_API_KEY")
        if not _env_key_usable(k)
    ]
    if missing:
        print(
            "Warning: missing or placeholder env vars (using fallbacks): "
            + ", ".join(missing)
        )


def _validate_airtable_env() -> None:
    required = ["AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"]
    missing = [k for k in required if not _env_key_usable(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "Airtable sync needs real credentials for "
                + ", ".join(missing)
                + ". Search and scoring still work without Airtable — "
                "add keys to .env only when you want to push leads to CRM "
                "(values like your_*_here do not work)."
            ),
        )


def _apply_icp_scores(results: List[Dict[str, Any]], icp: Dict[str, Any]) -> None:
    target_industries = icp.get("target_industries", [])
    target_size = icp.get("target_size", "")
    for r in results:
        if r.get("error") is not None:
            r["icp_match_score"] = 0
            continue
        score = 0
        if r.get("industry") in target_industries:
            score += 3
        company_size = r.get("size_estimate", "")
        if target_size and company_size:
            ts, cs = target_size.lower(), company_size.lower()
            if ts in cs or cs in ts:
                score += 2
        if r.get("b2b_buyer"):
            score += 2
        if r.get("lead_score", 0) >= 7:
            score += 3
        r["icp_match_score"] = score


def _search_result_for_api(r: Dict[str, Any]) -> None:
    if r.get("error"):
        return
    r["score_explanation"] = r.get("score_reason") or generate_lead_explanation(r)
    r["qualification_breakdown"] = get_lead_qualification_breakdown(r)
    r["email_display"] = filter_public_email(r.get("email", "") or "")
    r["phone_display"] = validate_and_format_phone(r.get("phone", "") or "")


class JobStore:
    """In-memory cache backed by JSON files (survives uvicorn --reload)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        os.makedirs(JOBS_DIR, exist_ok=True)
        self._load_from_disk()

    def _job_path(self, job_id: str) -> str:
        return os.path.join(JOBS_DIR, f"{job_id}.json")

    def _save_unlocked(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        path = self._job_path(job_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _load_disk(self, job_id: str) -> Optional[Dict[str, Any]]:
        path = self._job_path(job_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _ensure_loaded(self, job_id: str) -> None:
        if job_id in self._jobs:
            return
        loaded = self._load_disk(job_id)
        if loaded:
            self._jobs[job_id] = loaded

    def _load_from_disk(self) -> None:
        try:
            names = os.listdir(JOBS_DIR)
        except OSError:
            return
        for name in names:
            if not name.endswith(".json"):
                continue
            job_id = name[:-5]
            job = self._load_disk(job_id)
            if not job:
                continue
            self._jobs[job_id] = job

    def create(self, companies: List[str], job_type: str = "search") -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "job_type": job_type,
                "created_at": time.time(),
                "status": "queued",
                "progress": 0.0,
                "processed": 0,
                "total": len(companies),
                "companies": companies,
                "results": [],
                "icp": None,
                "recommendations": None,
                "alerts_summary": None,
                "recipient_email": None,
                "log": [],
                "error": None,
                "cancel_requested": False,
            }
            self._save_unlocked(job_id)
        return job_id

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            self._ensure_loaded(job_id)
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            if job["status"] not in ("queued", "running"):
                return False
            job["cancel_requested"] = True
            self._save_unlocked(job_id)
            return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            self._ensure_loaded(job_id)
            return bool(self._jobs.get(job_id, {}).get("cancel_requested"))

    def create_alerts(self, companies: List[str], recipient_email: str) -> str:
        job_id = self.create(companies, job_type="alerts")
        with self._lock:
            self._jobs[job_id]["recipient_email"] = recipient_email
            self._jobs[job_id]["sales_opportunities"] = []
            self._jobs[job_id]["alerts_summary"] = {
                "emails_sent": 0,
                "urgent_count": 0,
                "articles_found": 0,
                "sales_opportunities": 0,
            }
            self._save_unlocked(job_id)
        return job_id

    def get(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded(job_id)
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return dict(self._jobs[job_id])

    def update(self, job_id: str, **kwargs) -> None:
        with self._lock:
            self._ensure_loaded(job_id)
            if job_id not in self._jobs:
                raise KeyError(job_id)
            self._jobs[job_id].update(kwargs)
            self._save_unlocked(job_id)


def _parse_companies_csv(raw: bytes) -> List[str]:
    import csv
    import io

    text = raw.decode("utf-8", errors="ignore")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValueError("Empty CSV")

    header = [h.strip() for h in rows[0]]
    name_idx = header.index("company_name") if "company_name" in header else 0

    companies = []
    for row in rows[1:]:
        if len(row) <= name_idx:
            continue
        v = row[name_idx].strip()
        if v:
            companies.append(v)

    seen: set = set()
    return [c for c in companies if not (c.lower() in seen or seen.add(c.lower()))]


def _parse_companies_text(raw: str) -> List[str]:
    """Parse typed company name(s) — one per line or comma/semicolon separated."""
    companies = [
        piece.strip()
        for piece in re.split(r"[\n,;]", raw or "")
        if piece.strip()
    ]
    seen: set = set()
    return [c for c in companies if not (c.lower() in seen or seen.add(c.lower()))]


def _run_search_job(job_id: str) -> None:
    configure_stdio_utf8()
    try:
        job = store.get(job_id)
        companies: List[str] = job.get("companies") or []
        if not companies:
            store.update(job_id, status="failed", error="Job has no companies to process")
            return

        interactive = bool(job.get("interactive_disambiguation"))
        # Single-company interactive: pause for "Did you mean?" when ambiguous
        if interactive and len(companies) == 1 and not job.get("resolved_url"):
            from pipeline.search import discover_company_match

            match = discover_company_match(companies[0])
            if match.get("needs_user_pick") and match.get("candidates"):
                store.update(
                    job_id,
                    status="needs_disambiguation",
                    progress=0.05,
                    error=None,
                    disambiguation={
                        "company_name": companies[0],
                        "candidates": match.get("candidates") or [],
                        "match_confidence": match.get("match_confidence"),
                        "match_reason": match.get("match_reason"),
                        "proposed_url": match.get("url"),
                        "search_context": match.get("search_context") or "",
                    },
                )
                return
            # High-confidence auto path — stash match for processing
            store.update(
                job_id,
                resolved_url=match.get("url"),
                match_meta={
                    "match_confidence": match.get("match_confidence"),
                    "match_ambiguous": match.get("match_ambiguous"),
                    "match_reason": match.get("match_reason"),
                    "match_domain": match.get("selected_domain"),
                    "candidates": match.get("candidates") or [],
                    "search_context": match.get("search_context") or "",
                },
            )

        store.update(job_id, status="running", error=None)
        results: List[Dict[str, Any]] = list(job.get("results") or [])
        start_idx = int(job.get("processed") or len(results))
        total = len(companies)
        job = store.get(job_id)
        resolved_url = job.get("resolved_url")
        match_meta = job.get("match_meta") or {}

        for i in range(start_idx, total):
            if store.is_cancelled(job_id):
                store.update(job_id, status="cancelled", progress=i / total if total else 0.0)
                return
            if total == 1 and resolved_url:
                res = process_company(
                    companies[i],
                    run_id=job_id,
                    preselected_url=resolved_url,
                    match_meta=match_meta,
                )
            else:
                res = process_company(companies[i], run_id=job_id)
            if i < len(results):
                results[i] = res
            else:
                results.append(res)
            store.update(
                job_id,
                progress=(i + 1) / total,
                processed=i + 1,
                results=results,
            )

        successful = [r for r in results if r.get("error") is None]
        icp = None
        recommendations = None
        if successful:
            icp = generate_icp(successful)
            _apply_icp_scores(results, icp)
            recommendations = recommend_companies(icp, num_recommendations=5)

        for r in results:
            _search_result_for_api(r)

        store.update(
            job_id,
            status="done",
            progress=1.0,
            icp=icp,
            recommendations=recommendations,
        )
    except Exception as e:
        store.update(job_id, status="failed", error=str(e))


def _run_alerts_job(job_id: str) -> None:
    configure_stdio_utf8()
    try:
        job = store.get(job_id)
        companies: List[str] = job.get("companies") or []
        recipient_email = (job.get("recipient_email") or "").strip()
        if not companies:
            store.update(job_id, status="failed", error="Job has no companies to process")
            return
        if not recipient_email:
            store.update(job_id, status="failed", error="Job has no recipient email")
            return

        store.update(job_id, status="running", error=None)
        all_rows: List[Dict[str, Any]] = list(job.get("results") or [])
        all_opportunities: List[Dict[str, Any]] = list(job.get("sales_opportunities") or [])
        log: List[str] = list(job.get("log") or [])
        summary = dict(job.get("alerts_summary") or {})
        total_sent = int(summary.get("emails_sent", 0))
        total_urgent = int(summary.get("urgent_count", 0))
        total_articles = int(summary.get("articles_found", 0))
        total_opps = int(summary.get("sales_opportunities", 0))
        start_idx = int(job.get("processed") or 0)
        total = len(companies)

        for i in range(start_idx, total):
            if store.is_cancelled(job_id):
                log.append("Stopped by user.")
                store.update(
                    job_id,
                    status="cancelled",
                    progress=i / total if total else 0.0,
                    log=log,
                )
                return
            company = companies[i]
            log.append(f"Searching {company}...")
            try:
                outcome = process_company_alerts(company, recipient_email)
            except Exception as company_err:
                log.append(f"{company}: ERROR — {company_err}")
                store.update(
                    job_id,
                    progress=(i + 1) / total,
                    processed=i + 1,
                    results=all_rows,
                    sales_opportunities=all_opportunities,
                    log=log,
                    alerts_summary={
                        "emails_sent": total_sent,
                        "urgent_count": total_urgent,
                        "articles_found": total_articles,
                        "sales_opportunities": total_opps,
                    },
                )
                continue
            all_rows.extend(outcome.get("articles", []))
            opps = outcome.get("sales_opportunities") or []
            all_opportunities.extend(opps)
            total_sent += outcome.get("emails_sent", 0)
            total_urgent += outcome.get("urgent_count", 0)
            total_articles += outcome.get("articles_found", 0)
            total_opps += len(opps)
            log.append(
                f"{company}: {outcome.get('articles_found', 0)} articles, "
                f"{outcome.get('emails_sent', 0)} emails sent, "
                f"{len(opps)} sales draft(s)"
            )
            for err in outcome.get("stage_errors") or []:
                log.append(
                    f"  stage={err.get('pipeline_stage')} error={err.get('error')}"
                )
            store.update(
                job_id,
                progress=(i + 1) / total,
                processed=i + 1,
                results=all_rows,
                sales_opportunities=all_opportunities,
                log=log,
                alerts_summary={
                    "emails_sent": total_sent,
                    "urgent_count": total_urgent,
                    "articles_found": total_articles,
                    "sales_opportunities": total_opps,
                },
            )

        store.update(
            job_id,
            status="done",
            progress=1.0,
            sales_opportunities=all_opportunities,
            alerts_summary={
                "emails_sent": total_sent,
                "urgent_count": total_urgent,
                "articles_found": total_articles,
                "sales_opportunities": total_opps,
            },
        )
    except Exception as e:
        store.update(job_id, status="failed", error=str(e))


def _resume_pending_jobs() -> None:
    try:
        names = os.listdir(JOBS_DIR)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        job_id = name[:-5]
        try:
            job = store.get(job_id)
        except KeyError:
            continue
        status = job.get("status")
        if status not in ("running", "queued", "interrupted"):
            continue
        processed = int(job.get("processed") or 0)
        total = int(job.get("total") or 0)
        if total and processed >= total:
            continue
        store.update(job_id, status="queued", error=None)
        if job.get("job_type") == "alerts":
            target = _run_alerts_job
        else:
            target = _run_search_job
        threading.Thread(target=target, args=(job_id,), daemon=True).start()


store = JobStore()
_resume_pending_jobs()
app = FastAPI(title="AI Sales Intelligence", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.isdir(WEB_DIR):
    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.get("/")
def index():
    index_path = os.path.join(WEB_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_path)


@app.get("/api/health")
def health():
    env = check_env_vars()
    return {"ok": all(env.values()), "env": env}


@app.get("/api/airtable")
def airtable_records():
    _validate_airtable_env()
    records = fetch_from_airtable()
    return {"records": records or []}


@app.post("/api/jobs/search")
@app.post("/api/jobs/enrich")  # legacy route for cached browsers
async def search_csv(
    file: UploadFile = File(None),
    companies_text: str = Form(""),
):
    _validate_env()

    companies: List[str] = []
    from_file = False
    if file is not None and getattr(file, "filename", None):
        raw = await file.read()
        try:
            companies = _parse_companies_csv(raw)
            from_file = True
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid CSV: {e}") from e
    elif companies_text.strip():
        companies = _parse_companies_text(companies_text)
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide a company name or upload a CSV file",
        )

    if not companies:
        raise HTTPException(status_code=400, detail="No valid companies found")

    job_id = store.create(companies)
    # Single typed name → allow Did you mean? picker; CSV/batch always auto-resolves
    interactive = (not from_file) and len(companies) == 1
    store.update(job_id, interactive_disambiguation=interactive)
    for company in companies:
        log_funnel_stage(company, job_id, "uploaded")
    threading.Thread(target=_run_search_job, args=(job_id,), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.post("/api/jobs/{job_id}/resolve")
async def resolve_disambiguation(job_id: str, payload: Dict[str, Any] = Body(...)):
    """Resume a paused single-company job after the user picks a candidate URL."""
    try:
        job = store.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "needs_disambiguation":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not awaiting disambiguation (status={job.get('status')})",
        )
    url = str(payload.get("url") or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url must be an http(s) address")

    dis = job.get("disambiguation") or {}
    store.update(
        job_id,
        resolved_url=url,
        match_meta={
            "match_confidence": "High",
            "match_ambiguous": False,
            "match_reason": "User-selected company match",
            "match_domain": url.split("/")[2] if "://" in url else "",
            "candidates": dis.get("candidates") or [],
            "search_context": dis.get("search_context") or "",
        },
        disambiguation=None,
        status="queued",
        error=None,
    )
    threading.Thread(target=_run_search_job, args=(job_id,), daemon=True).start()
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/api/jobs/alerts")
async def alerts_csv(
    recipient_email: str = Form(...),
    file: UploadFile = File(None),
    companies_text: str = Form(""),
):
    _validate_env()

    recipients = parse_recipient_emails(recipient_email)
    if not recipients:
        raise HTTPException(status_code=400, detail="Enter at least one valid email")

    if file is not None and (file.filename or "").strip():
        raw = await file.read()
        try:
            companies = _parse_companies_csv(raw)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid CSV: {e}") from e
    elif companies_text.strip():
        companies = _parse_companies_text(companies_text)
    else:
        raise HTTPException(
            status_code=400, detail="Upload a CSV or enter at least one company name"
        )

    if not companies:
        raise HTTPException(status_code=400, detail="No valid companies found")

    job_id = store.create_alerts(companies, recipient_email.strip())
    threading.Thread(target=_run_alerts_job, args=(job_id,), daemon=True).start()
    return JSONResponse({"job_id": job_id, "smtp_configured": smtp_configured()})


@app.get("/api/alerts/resend-hint")
def resend_hint():
    host = os.getenv("EMAIL_SMTP_HOST", "")
    if "resend" not in host.lower() or resend_domain_verified():
        return {"show_hint": False}
    acct = resend_account_email()
    return {
        "show_hint": True,
        "allowed_test_recipient": acct,
        "message": resend_test_mode_message(),
    }


@app.post("/api/alerts/test-email")
async def test_alert_email(recipient_email: str = Form(...)):
    recipients = parse_recipient_emails(recipient_email)
    if not recipients:
        raise HTTPException(status_code=400, detail="Enter at least one valid email")
    result = send_test_email(recipients)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Send failed"))
    return JSONResponse({
        "ok": True,
        "sent_to": result.get("sent_to", []),
        "failed_to": result.get("failed_to", {}),
        "partial": bool(result.get("partial")),
        "message": result.get("error"),
    })


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        if not store.request_cancel(job_id):
            job = store.get(job_id)
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel job with status '{job.get('status')}'",
            )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    return JSONResponse({"ok": True, "job_id": job_id})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        job = store.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("job_type", "search") in ("search", "enrich"):
        for r in job.get("results", []):
            _search_result_for_api(r)

    return JSONResponse(job)


@app.post("/api/jobs/{job_id}/push")
def push_job(job_id: str):
    _validate_env()
    _validate_airtable_env()
    try:
        job = store.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")

    successful = [r for r in job.get("results", []) if r.get("error") is None]
    if not successful:
        raise HTTPException(status_code=400, detail="No successful results to push")

    pushed = failed = skipped_review = 0
    for r in successful:
        payload = dict(r)
        payload["_run_id"] = job_id
        if r.get("review_needed"):
            skipped_review += 1
            failed += 1
            log_funnel_stage(
                r.get("company_name") or "Unknown",
                job_id,
                "failed",
                failure_reason="review_needed: "
                + str(r.get("validation_errors") or ""),
                lead_score=r.get("lead_score"),
                status_tag=r.get("status_tag"),
                industry=r.get("industry"),
            )
            continue
        if push_to_airtable(payload):
            pushed += 1
        else:
            failed += 1

    clear_analytics_cache()
    return JSONResponse({
        "pushed": pushed,
        "failed": failed,
        "skipped_review": skipped_review,
    })


@app.post("/api/alerts/sales-opportunity/push")
async def push_sales_opportunity(payload: Dict[str, Any] = Body(...)):
    """Push one regulatory sales opportunity draft to Airtable."""
    _validate_airtable_env()
    if not payload or not payload.get("company_name"):
        raise HTTPException(status_code=400, detail="company_name is required")
    ok = push_regulatory_sales_opportunity(payload)
    if not ok:
        raise HTTPException(status_code=400, detail="Airtable push failed or duplicate")
    clear_analytics_cache()
    return JSONResponse({"ok": True})


@app.get("/api/airtable/url")
def airtable_url():
    base_id = os.getenv("AIRTABLE_BASE_ID", "")
    return {"url": f"https://airtable.com/{base_id}" if base_id else ""}


@app.get("/api/analytics/summary")
def analytics_summary():
    """KPI summary from Airtable (Hot/Warm/Cold counts + avg score)."""
    try:
        return JSONResponse(compute_summary())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Analytics summary failed: {e}") from e


@app.get("/api/analytics/industries")
def analytics_industries():
    """Industry breakdown from Airtable leads."""
    try:
        return JSONResponse(compute_industries())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Analytics industries failed: {e}") from e


@app.get("/api/analytics/trend")
def analytics_trend(days: int = 30):
    """Leads created per day (Airtable createdTime)."""
    try:
        return JSONResponse(compute_trend(days=days))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Analytics trend failed: {e}") from e


@app.get("/api/analytics/funnel")
def analytics_funnel():
    """Conversion funnel from local SQLite stage log (no Airtable round-trip required)."""
    try:
        return JSONResponse(compute_funnel())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Analytics funnel failed: {e}") from e


@app.get("/api/analytics/recent")
def analytics_recent(limit: int = 20):
    """Recent lead activity (Airtable + funnel log fallback)."""
    try:
        lim = max(1, min(int(limit or 20), 100))
        return JSONResponse(compute_recent_activity(limit=lim))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Analytics recent failed: {e}") from e
