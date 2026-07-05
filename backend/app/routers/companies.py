import uuid
from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models.enrichment import EnrichmentJob, LeadRecord
from backend.app.schemas.company import CompanyEnrichRequest
from backend.app.schemas.enrichment import (
    EnrichmentJobBase,
    EnrichmentJobResponse,
    HistoryResponse,
    JobStartResponse,
    LeadRecordResponse,
)
from backend.app.services.enrichment import run_enrichment_pipeline
from backend.app.utils.helpers import parse_csv
from backend.app.utils.logging import logger

router = APIRouter(prefix="/enrich", tags=["Enrichment Pipeline"])


@router.post("", response_model=JobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_enrichment_json(
    req: CompanyEnrichRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Start lead enrichment job by submitting a JSON list of company names.
    Returns immediately with a job ID and runs the pipeline in the background.
    """
    job_id = str(uuid.uuid4())[:8]  # Short unique job id
    logger.info(f"Received JSON enrichment request for {len(req.companies)} companies")

    # Save initial job state to DB
    new_job = EnrichmentJob(
        job_id=job_id,
        user_email="anonymous@local",
        status="pending",
        total_companies=len(req.companies),
        processed_companies=0
    )
    db.add(new_job)
    await db.commit()

    # Dispatch to background task worker
    background_tasks.add_task(run_enrichment_pipeline, job_id, req.companies, req.quick_mode)

    return {
        "job_id": job_id,
        "status": "pending",
        "total_companies": len(req.companies),
        "message": "Enrichment pipeline dispatched successfully in the background."
    }


@router.post("/csv", response_model=JobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_enrichment_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    quick_mode: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    """
    Start lead enrichment job by uploading a CSV file.
    Extracts company names automatically and dispatches background runner.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Please upload a valid CSV file."
        )

    try:
        content = await file.read()
        company_names = parse_csv(content)
    except Exception as e:
        logger.error(f"Failed to read/parse uploaded CSV file: {e}")
        raise HTTPException(
            status_code=status.HTTP_420_METHOD_FAILURE,
            detail="Failed to parse the uploaded CSV file."
        )

    if not company_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid company names found in the uploaded CSV. Ensure a 'company_name' or valid first column exists."
        )

    job_id = str(uuid.uuid4())[:8]
    logger.info(f"Received CSV enrichment request for {len(company_names)} companies")

    new_job = EnrichmentJob(
        job_id=job_id,
        user_email="anonymous@local",
        status="pending",
        total_companies=len(company_names),
        processed_companies=0
    )
    db.add(new_job)
    await db.commit()

    background_tasks.add_task(run_enrichment_pipeline, job_id, company_names, quick_mode)

    return {
        "job_id": job_id,
        "status": "pending",
        "total_companies": len(company_names),
        "message": "CSV Enrichment pipeline dispatched successfully in the background."
    }


@router.get("/{job_id}", response_model=EnrichmentJobResponse)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Poll the live status and progress of an enrichment job along with results processed so far."""
    # Find job
    q_job = select(EnrichmentJob).where(EnrichmentJob.job_id == job_id)
    res_job = await db.execute(q_job)
    job = res_job.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Enrichment Job ID '{job_id}' not found"
        )

    # Find processed results
    q_results = select(LeadRecord).where(LeadRecord.job_id == job_id)
    res_leads = await db.execute(q_results)
    leads = res_leads.scalars().all()

    # Calculate progress percent safely
    progress = 0.0
    if job.total_companies > 0:
        progress = float(job.processed_companies) / float(job.total_companies)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "total_companies": job.total_companies,
        "processed_companies": job.processed_companies,
        "current_company": job.current_company,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress": round(progress, 3),
        "results": leads
    }


@router.get("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Cancel a pending or running enrichment job."""
    q_job = select(EnrichmentJob).where(EnrichmentJob.job_id == job_id)
    res_job = await db.execute(q_job)
    job = res_job.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Enrichment Job ID '{job_id}' not found"
        )

    if job.status not in ["pending", "running"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job cannot be cancelled as status is '{job.status}'"
        )

    job.status = "cancelled"
    job.current_company = None
    await db.commit()

    logger.info(f"Cancellation flag set for Enrichment Job ID '{job_id}'")
    return {"message": f"Job '{job_id}' cancelled successfully."}


@router.get("/history/list", response_model=HistoryResponse)
async def list_jobs_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve history of all enrichment jobs executed in the system."""
    # Count total jobs
    q_all = select(EnrichmentJob).order_by(desc(EnrichmentJob.created_at))
    res_all = await db.execute(q_all)
    all_jobs = res_all.scalars().all()

    # Query with skip/limit pagination
    q_page = select(EnrichmentJob).order_by(desc(EnrichmentJob.created_at)).offset(skip).limit(limit)
    res_page = await db.execute(q_page)
    jobs = res_page.scalars().all()

    return {
        "jobs": jobs,
        "total_jobs": len(all_jobs)
    }


@router.get("/leads/all", response_model=List[LeadRecordResponse])
async def list_enriched_leads(
    industry: Optional[str] = Query(None),
    status_tag: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None, ge=0, le=10),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve all enriched lead records with optional status, industry, and lead score filtering."""
    query = select(LeadRecord)
    
    if industry:
        query = query.where(LeadRecord.industry.ilike(f"%{industry.strip()}%"))
    if status_tag:
        query = query.where(LeadRecord.status_tag == status_tag)
    if min_score is not None:
        query = query.where(LeadRecord.lead_score >= min_score)
        
    query = query.order_by(desc(LeadRecord.enriched_at)).limit(limit)
    result = await db.execute(query)
    leads = result.scalars().all()
    
    return leads
