import json
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from utils.stdio import configure_stdio_utf8

configure_stdio_utf8()

from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import fetch_from_airtable, generate_icp, push_to_airtable, recommend_companies
from pipeline.analyzer import probe_nvidia_api
from services.lead_insights import (
    filter_public_email,
    generate_lead_explanation,
    get_lead_qualification_breakdown,
    validate_and_format_phone,
)
from services.alert_processing import process_company_alerts
from services.email_alerts import send_test_email, smtp_configured
from services.lead_processing import process_company
from utils.email_recipients import (
    parse_recipient_emails,
    resend_account_email,
    resend_domain_verified,
    resend_test_mode_message,
)

load_dotenv(override=True)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
JOBS_DIR = os.path.join(ROOT, "data", "jobs")


def check_env_vars() -> Dict[str, bool]:
    return {
        "FIRECRAWL_API_KEY": bool(os.getenv("FIRECRAWL_API_KEY")),
        "NVIDIA_API_KEY": bool(os.getenv("NVIDIA_API_KEY")),
        "AIRTABLE_API_KEY": bool(os.getenv("AIRTABLE_API_KEY")),
        "AIRTABLE_BASE_ID": bool(os.getenv("AIRTABLE_BASE_ID")),
        "EMAIL_SENDER": bool(os.getenv("EMAIL_SENDER")),
        "EMAIL_PASSWORD": bool(os.getenv("EMAIL_PASSWORD")),
    }


def _validate_env() -> None:
    required = ["FIRECRAWL_API_KEY", "NVIDIA_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))


def _validate_airtable_env() -> None:
    required = ["AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))


def _apply_icp_scores(results: List[Dict[str, Any]], icp: Dict[str, Any]) -> None:
    target_industries = icp.get("target_industries", [])
    target_size = icp.get("target_size", "")
    for r in results:
        if r.get("error") is not None or not r.get("scored"):
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
        lead = r.get("lead_score")
        if isinstance(lead, int) and lead >= 7:
            score += 3
        r["icp_match_score"] = score


def _search_result_for_api(r: Dict[str, Any]) -> None:
    if r.get("error"):
        return
    r["score_explanation"] = r.get("score_reason") or generate_lead_explanation(r)
    r["qualification_breakdown"] = get_lead_qualification_breakdown(r)
    r["email_display"] = filter_public_email(r.get("email", "") or "")
    r["phone_display"] = validate_and_format_phone(r.get("phone", "") or "")
    # Surface Unknown / Not scored fields for the UI
    unknowns: List[str] = []
    for field in ("industry", "size_estimate", "b2b_evidence", "business_model"):
        val = str(r.get(field) or "").strip().lower()
        if val in ("", "unknown", "n/a", "none", "not stated on website"):
            unknowns.append(field)
    if r.get("b2b_buyer") is None:
        unknowns.append("b2b_buyer")
    if r.get("lead_score") is None or r.get("status_tag") == "Not scored":
        unknowns.append("lead_score")
    r["unknown_fields"] = unknowns
    r["scored"] = bool(r.get("scored")) and r.get("lead_score") is not None

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
            self._jobs[job_id]["alerts_summary"] = {
                "emails_sent": 0,
                "urgent_count": 0,
                "articles_found": 0,
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


def _finalize_search_job(job_id: str, results: List[Dict[str, Any]]) -> None:
    # Only scored rows feed ICP — unscored / ambiguous / failed analysis stay out
    successful = [
        r for r in results
        if r.get("error") is None and r.get("scored") and r.get("lead_score") is not None
    ]
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
        typo_suggestion=None,
        disambiguation=None,
        stage="Done",
    )


def _run_search_job(job_id: str) -> None:
    configure_stdio_utf8()
    try:
        job = store.get(job_id)
        companies: List[str] = job.get("companies") or []
        if not companies:
            store.update(job_id, status="failed", error="Job has no companies to process")
            return

        interactive = bool(job.get("interactive_disambiguation"))
        confirmed = job.get("typo_confirmed") or {}
        # Single typed name with a pending typo confirm — never auto-switch.
        if (
            interactive
            and len(companies) == 1
            and not confirmed
            and not job.get("typo_declined")
        ):
            store.update(
                job_id,
                status="running",
                error=None,
                stage=f"Matching {companies[0]}…",
                progress=0.05,
            )
            probe = process_company(companies[0])
            # Ambiguity: pause for Did you mean? with distinct entity candidates
            if probe.get("match_ambiguous") and (probe.get("selectable_candidates") or []):
                cands = probe.get("selectable_candidates") or []
                store.update(
                    job_id,
                    status="needs_disambiguation",
                    progress=0.1,
                    stage="Waiting for company confirmation…",
                    error=None,
                    results=[probe],
                    processed=0,
                    disambiguation={
                        "original_name": companies[0],
                        "reason": probe.get("match_reason") or "",
                        "entity_labels": probe.get("entity_labels") or [],
                        "candidates": cands,
                    },
                    typo_suggestion=None,
                )
                return
            # Typo near-miss: present even when error is cleared (Needs review path)
            typo = probe.get("typo_suggestion") if not probe.get("url") else None
            if typo and typo.get("suggested_name") and not probe.get("match_ambiguous"):
                store.update(
                    job_id,
                    status="needs_typo_confirm",
                    progress=0.1,
                    stage="Waiting for typo confirmation…",
                    error=None,
                    results=[probe],
                    processed=0,
                    typo_suggestion={
                        "original_name": companies[0],
                        "suggested_name": typo.get("suggested_name"),
                        "suggested_url": typo.get("suggested_url") or "",
                        "reason": typo.get("reason") or "",
                        "snippet": typo.get("snippet") or "",
                        "evidence_count": typo.get("evidence_count"),
                        "evidence_total": typo.get("evidence_total"),
                    },
                )
                return
            # No typo / ambiguity prompt — keep this probe result (success or hard fail).
            store.update(
                job_id,
                results=[probe],
                processed=1,
                progress=1.0,
                stage="Done",
            )
            _finalize_search_job(job_id, [probe])
            return

        store.update(job_id, status="running", error=None, stage="Starting…")
        results: List[Dict[str, Any]] = list(job.get("results") or [])
        start_idx = int(job.get("processed") or len(results))
        total = len(companies)

        for i in range(start_idx, total):
            if store.is_cancelled(job_id):
                store.update(job_id, status="cancelled", progress=i / total if total else 0.0)
                return
            name = companies[i]
            kwargs: Dict[str, Any] = {}
            if confirmed and str(confirmed.get("index", 0)) == str(i):
                kwargs["confirmed_name"] = confirmed.get("confirmed_name") or name
                if confirmed.get("url"):
                    kwargs["preselected_url"] = confirmed.get("url")
            res = process_company(name, **kwargs)
            if i < len(results):
                results[i] = res
            else:
                results.append(res)
            store.update(
                job_id,
                progress=(i + 1) / total,
                processed=i + 1,
                results=results,
                stage=f"Processed {name}",
            )

        _finalize_search_job(job_id, results)
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
        log: List[str] = list(job.get("log") or [])
        summary = dict(job.get("alerts_summary") or {})
        total_sent = int(summary.get("emails_sent", 0))
        total_urgent = int(summary.get("urgent_count", 0))
        total_articles = int(summary.get("articles_found", 0))
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
            outcome = process_company_alerts(company, recipient_email)
            all_rows.extend(outcome.get("articles", []))
            total_sent += outcome.get("emails_sent", 0)
            total_urgent += outcome.get("urgent_count", 0)
            total_articles += outcome.get("articles_found", 0)
            log.append(
                f"{company}: {outcome.get('articles_found', 0)} articles, "
                f"{outcome.get('emails_sent', 0)} emails sent"
            )
            store.update(
                job_id,
                progress=(i + 1) / total,
                processed=i + 1,
                results=all_rows,
                log=log,
                alerts_summary={
                    "emails_sent": total_sent,
                    "urgent_count": total_urgent,
                    "articles_found": total_articles,
                },
            )

        store.update(
            job_id,
            status="done",
            progress=1.0,
            alerts_summary={
                "emails_sent": total_sent,
                "urgent_count": total_urgent,
                "articles_found": total_articles,
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


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    try:
        from services.agents.orchestrator import start_scheduler, stop_scheduler
        from services.agents import store as agent_store

        agent_store.ensure_db()
        start_scheduler()
    except Exception as exc:  # noqa: BLE001
        print(f"[agents] lifespan start skipped: {exc}")
    try:
        yield
    finally:
        try:
            from services.agents.orchestrator import stop_scheduler

            stop_scheduler()
        except Exception as exc:  # noqa: BLE001
            print(f"[agents] lifespan stop: {exc}")


app = FastAPI(
    title="AI Sales Intelligence",
    version="2.0.0",
    lifespan=_app_lifespan,
)

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
    nvidia = probe_nvidia_api()
    # Live NVIDIA probe — invalid/placeholder keys fail immediately
    env_ready = all(
        env.get(k) for k in ("FIRECRAWL_API_KEY", "NVIDIA_API_KEY")
    )
    ok = bool(env_ready and nvidia.get("ok"))
    return {
        "ok": ok,
        "env": env,
        "nvidia": nvidia,
    }


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
    """Accept a CSV upload or typed company names (one per line / comma-separated)."""
    _validate_env()

    companies: List[str] = []
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
            status_code=400,
            detail="Provide a company name or upload a CSV file",
        )

    if not companies:
        raise HTTPException(status_code=400, detail="No valid companies found")

    from_file = file is not None and (file.filename or "").strip()
    job_id = store.create(companies)
    # Single typed name → allow "Did you mean?" pause; CSV/batch never pauses.
    interactive = (not from_file) and len(companies) == 1
    store.update(job_id, interactive_disambiguation=interactive)
    threading.Thread(target=_run_search_job, args=(job_id,), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.post("/api/jobs/{job_id}/resolve-typo")
@app.post("/api/jobs/{job_id}/resolve-disambiguation")
async def resolve_typo(job_id: str, payload: Dict[str, Any] = Body(...)):
    """
    Resume after "Did you mean?" — typo confirm or ambiguous-entity pick.

    Never auto-switches: only runs when the user posts accept=true/false.
    """
    try:
        job = store.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None

    status = job.get("status")
    awaiting = status in ("needs_typo_confirm", "needs_disambiguation")
    if not awaiting:
        raise HTTPException(
            status_code=400,
            detail=f"Job is not awaiting confirmation (status={status})",
        )

    accept = bool(payload.get("accept"))
    suggestion = job.get("typo_suggestion") or {}
    disambiguation = job.get("disambiguation") or {}
    results = list(job.get("results") or [])

    if accept:
        suggested_name = str(
            payload.get("suggested_name")
            or suggestion.get("suggested_name")
            or disambiguation.get("original_name")
            or ""
        ).strip()
        # Ambiguity candidates may keep the original query name but pick a URL
        if status == "needs_disambiguation" and not suggested_name:
            suggested_name = str(disambiguation.get("original_name") or "").strip()
        suggested_url = str(
            payload.get("url") or suggestion.get("suggested_url") or ""
        ).strip()
        if not suggested_name and not suggested_url:
            raise HTTPException(
                status_code=400,
                detail="suggested_name or url is required",
            )
        if not suggested_name:
            suggested_name = (job.get("companies") or ["Company"])[0]

        # Only preselect a homepage-looking URL; otherwise rediscover under the
        # confirmed name (never keep Siemens data labeled as the typo).
        preselect = ""
        if suggested_url.startswith("http"):
            from urllib.parse import urlparse as _urlparse

            path = (_urlparse(suggested_url).path or "/").rstrip("/").lower() or "/"
            # Ambiguity picks may be any candidate URL the user chose
            if status == "needs_disambiguation" or path in (
                "", "/", "/en", "/en-us", "/en_us", "/us", "/uk", "/home",
            ):
                preselect = suggested_url
        store.update(
            job_id,
            companies=[suggested_name],
            typo_confirmed={
                "index": 0,
                "confirmed_name": suggested_name,
                "url": preselect,
                "original_name": (
                    suggestion.get("original_name")
                    or disambiguation.get("original_name")
                    or ((job.get("companies") or [""])[0])
                ),
            },
            typo_suggestion=None,
            disambiguation=None,
            results=[],
            processed=0,
            status="queued",
            stage="Confirmed — researching…",
            progress=0.15,
        )
        threading.Thread(target=_run_search_job, args=(job_id,), daemon=True).start()
        return JSONResponse({
            "job_id": job_id,
            "accepted": True,
            "company": suggested_name,
            "url": preselect,
        })

    # Decline: keep unmet / ambiguous row as final (Not scored + needs review)
    store.update(
        job_id,
        typo_declined=True,
        typo_suggestion=None,
        disambiguation=None,
    )
    if not results:
        original = (job.get("companies") or ["Unknown"])[0]
        results = [
            {
                "company_name": original,
                "url": "",
                "error": "No confident match",
                "status_tag": "Error",
                "lead_score": None,
                "scored": False,
                "industry": "Unknown",
                "size_estimate": "Unknown",
                "review_needed": True,
                "match_confidence": "Low",
                "match_reason": "No confident match",
                "display_message": "No confident match",
                "validation_errors": ["No confident match"],
            }
        ]
    else:
        # Preserve ambiguity / fail row — ensure Not scored, not a fake Cold
        for r in results:
            if r.get("match_ambiguous") or r.get("insufficient_data") or not r.get("scored"):
                r["lead_score"] = None
                r["scored"] = False
                r["review_needed"] = True
                if r.get("match_ambiguous") or r.get("typo_suggestion"):
                    r["status_tag"] = "Needs review"
                    r["error"] = None
                else:
                    r["status_tag"] = "Error"
                    r["error"] = r.get("error") or "No confident match"
                    r["display_message"] = "No confident match"
                if not r.get("industry") or str(r.get("industry")).lower() in ("other", ""):
                    r["industry"] = "Unknown"
                r["size_estimate"] = r.get("size_estimate") or "Unknown"
                if str(r.get("size_estimate")).lower() in ("", "other"):
                    r["size_estimate"] = "Unknown"
    _finalize_search_job(job_id, results)
    return JSONResponse({"job_id": job_id, "accepted": False})


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
        valid = (
            not r.get("review_needed")
            and not r.get("match_ambiguous")
            and r.get("scored") is not False
            and r.get("lead_score") is not None
            and r.get("status_tag") not in ("Not scored", "Needs review", "Error", "Unknown")
        )
        if not valid:
            skipped_review += 1
            failed += 1
            continue
        if push_to_airtable(r):
            pushed += 1
        else:
            failed += 1

    return JSONResponse({
        "pushed": pushed,
        "failed": failed,
        "skipped_review": skipped_review,
    })


@app.get("/api/airtable/url")
def airtable_url():
    base_id = os.getenv("AIRTABLE_BASE_ID", "")
    return {"url": f"https://airtable.com/{base_id}" if base_id else ""}


# ---------------------------------------------------------------------------
# Multi-agent marketing / outreach layer
# ---------------------------------------------------------------------------


@app.get("/api/agents/config")
def agents_config():
    from services.agents import config as agent_config
    from services.agents.orchestrator import scheduler_status

    return {
        **agent_config.public_config(),
        "scheduler": scheduler_status(),
    }


@app.get("/api/agents/drafts")
def agents_list_drafts(
    status: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 100,
):
    from services.agents import store as agent_store

    try:
        drafts = agent_store.list_drafts(
            status=status or None,
            draft_type=type or None,
            limit=limit,
        )
        return {"drafts": drafts, "count": len(drafts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"List drafts failed: {e}") from e


@app.get("/api/agents/drafts/{draft_id}")
def agents_get_draft(draft_id: str):
    from services.agents import store as agent_store

    draft = agent_store.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@app.patch("/api/agents/drafts/{draft_id}")
def agents_edit_draft(draft_id: str, payload: Dict[str, Any] = Body(...)):
    from services.agents import store as agent_store

    draft = agent_store.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    updated = agent_store.update_draft(
        draft_id,
        subject=payload.get("subject"),
        body=payload.get("body"),
        title=payload.get("title"),
    )
    return updated


@app.post("/api/agents/drafts/{draft_id}/approve")
def agents_approve_draft(draft_id: str):
    """Approve → approved_ready_to_send (does NOT email prospects)."""
    from services.agents import store as agent_store

    draft = agent_store.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft["status"] not in ("pending", "approved_ready_to_send"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve draft in status {draft['status']}",
        )
    updated = agent_store.update_draft(draft_id, status="approved_ready_to_send")
    return updated


@app.post("/api/agents/drafts/{draft_id}/reject")
def agents_reject_draft(draft_id: str):
    from services.agents import store as agent_store

    draft = agent_store.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    updated = agent_store.update_draft(draft_id, status="rejected")
    return updated


@app.post("/api/agents/drafts/{draft_id}/replied")
def agents_mark_replied(draft_id: str):
    """Manual mark-as-replied on approved outreach (feeds Call Trigger)."""
    from services.agents import store as agent_store

    draft = agent_store.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.get("type") != "outreach":
        raise HTTPException(status_code=400, detail="Only outreach drafts can be marked replied")
    if draft.get("status") != "approved_ready_to_send":
        raise HTTPException(
            status_code=400,
            detail="Approve the draft first (status must be approved_ready_to_send)",
        )
    updated = agent_store.update_draft(draft_id, replied=True)
    return updated


@app.post("/api/agents/outreach/run")
def agents_run_outreach(limit: int = 20):
    from services.agents.outreach_copywriter import run_outreach_scan

    try:
        return run_outreach_scan(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Outreach scan failed: {e}") from e


@app.post("/api/agents/content/run")
def agents_run_content():
    from services.agents.content_creation import run_content_creation

    try:
        return run_content_creation()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Content creation failed: {e}") from e


@app.post("/api/agents/calls/run")
def agents_run_calls(limit: int = 25):
    from services.agents.call_trigger import run_call_trigger

    try:
        return run_call_trigger(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Call trigger failed: {e}") from e


@app.get("/api/agents/calls")
def agents_list_calls(limit: int = 100):
    from services.agents import store as agent_store

    try:
        events = agent_store.list_call_events(limit=limit)
        return {"events": events, "count": len(events)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Call log failed: {e}") from e
