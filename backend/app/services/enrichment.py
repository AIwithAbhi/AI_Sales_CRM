import asyncio
from datetime import datetime
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal
from backend.app.models.enrichment import EnrichmentJob, LeadRecord
from backend.app.services.analyzer import analyze_company
from backend.app.services.scraper import scrape_homepage
from backend.app.services.search import search_company_info
from backend.app.utils.helpers import get_status_tag, load_headcount_data
from backend.app.utils.logging import logger


async def run_enrichment_pipeline(
    job_id: str,
    company_names: List[str],
    quick_mode: bool = False
) -> None:
    """
    Background worker function that runs the complete lead enrichment pipeline
    for a list of companies under a given job_id.

    Args:
        job_id: Unique string tracking the enrichment job.
        company_names: List of company names to enrich.
        quick_mode: Whether to run fast scraper parsing context.
    """
    logger.info(f"Starting async background pipeline for Job ID '{job_id}' with {len(company_names)} companies")
    
    # Load headcount CSV data once
    headcount_db = load_headcount_data()

    # Obtain database session dynamically in background thread
    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch current job
            query = select(EnrichmentJob).where(EnrichmentJob.job_id == job_id)
            result = await db.execute(query)
            job = result.scalar_one_or_none()

            if not job:
                logger.error(f"Background EnrichmentJob not found in database: {job_id}")
                return

            # Update job status to running
            job.status = "running"
            job.total_companies = len(company_names)
            job.processed_companies = 0
            await db.commit()

            # 2. Iterate and enrich each company
            for idx, company_name in enumerate(company_names):
                # Always check for cancellation before starting the next item
                await db.refresh(job)
                if job.status == "cancelled":
                    logger.warning(f"Enrichment Job '{job_id}' was cancelled by user. Aborting pipeline execution.")
                    break

                logger.info(f"Processing lead [{idx + 1}/{len(company_names)}]: {company_name}")
                job.current_company = company_name
                await db.commit()

                # Start building lead record
                lead = LeadRecord(
                    job_id=job_id,
                    company_name=company_name,
                    enriched_at=datetime.utcnow()
                )

                try:
                    # Step A: Headcount CSV check
                    company_key = company_name.strip().lower()
                    headcount_info = headcount_db.get(company_key, {})
                    lead.headcount_w1 = headcount_info.get("headcount_week1", 0)
                    lead.headcount_w4 = headcount_info.get("headcount_week4", 0)
                    lead.growth_rate = headcount_info.get("growth_rate", 0.0)
                    lead.growth_label = headcount_info.get("growth_label", "No data")

                    headcount_context = f"LinkedIn headcount trend: {lead.growth_label} ({lead.growth_rate:.1f}% over 4 weeks)"

                    # Step B: Web search discovery
                    url, search_context = search_company_info(company_name)
                    if not url:
                        raise ValueError("Company official homepage URL could not be found")

                    lead.url = url

                    # Step C: Scraping
                    homepage_text = scrape_homepage(url)
                    if not homepage_text:
                        if search_context:
                            logger.warning(f"Homepage scraping failed for {company_name}. Using search results fallback.")
                            homepage_text = f"[Scraping failed. Using search results fallback]\n\n{search_context}"
                        else:
                            raise ValueError("Homepage scraping failed and no fallback context available")

                    # Truncate text according to mode
                    char_limit = 1500 if quick_mode else 3000
                    text_for_ai = homepage_text[:char_limit]

                    # Step D: AI Analysis
                    analysis = analyze_company(company_name, text_for_ai, headcount_context)
                    
                    # Update fields
                    lead.summary = analysis.get("summary", "")
                    lead.industry = analysis.get("industry", "Other")
                    lead.size_estimate = analysis.get("size_estimate", "1-10 employees")
                    lead.b2b_buyer = analysis.get("b2b_buyer", False)
                    lead.lead_score = analysis.get("lead_score", 0)
                    lead.score_reason = analysis.get("score_reason", "")
                    lead.status_tag = get_status_tag(lead.lead_score)

                except Exception as proc_err:
                    err_msg = str(proc_err)
                    logger.error(f"Error enriching lead '{company_name}': {err_msg}")
                    lead.error = err_msg
                    lead.status_tag = "Unknown"

                # Save lead record to DB
                db.add(lead)
                
                # Update job progress
                job.processed_companies += 1
                await db.commit()
                
                # Rate limit safety delay
                await asyncio.sleep(1.0)

            # 3. Finalize Job Status
            await db.refresh(job)
            if job.status == "running":
                job.status = "completed"
                logger.info(f"Pipeline completed successfully for Job ID '{job_id}'")
            
            job.current_company = None
            await db.commit()

        except Exception as job_err:
            logger.error(f"Enrichment Pipeline Job ID '{job_id}' encountered a critical error: {job_err}")
            try:
                job.status = "failed"
                job.current_company = None
                await db.commit()
            except Exception:
                pass
