from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models.enrichment import LeadRecord
from backend.app.schemas.company import CompanyBulkPushRequest
from backend.app.services.crm import push_to_airtable
from backend.app.utils.logging import logger

router = APIRouter(prefix="/airtable", tags=["Airtable CRM"])


@router.post("/push")
async def bulk_push_leads(
    req: CompanyBulkPushRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Push selected lead records to Airtable in bulk.
    Updates pushed_to_airtable flag locally on success.
    """
    logger.info(f"Received request to push {len(req.lead_ids)} leads to Airtable")
    
    success_count = 0
    failed_leads = []

    # Process each lead record
    for lead_id in req.lead_ids:
        # Fetch lead from database
        query = select(LeadRecord).where(LeadRecord.id == lead_id)
        result = await db.execute(query)
        lead = result.scalar_one_or_none()

        if not lead:
            logger.warning(f"Lead record ID '{lead_id}' not found in database, skipping")
            failed_leads.append({"id": lead_id, "reason": "Record not found"})
            continue

        if lead.error:
            logger.warning(f"Cannot push failed lead '{lead.company_name}' to Airtable")
            failed_leads.append({"id": lead_id, "company_name": lead.company_name, "reason": f"Lead record has error: {lead.error}"})
            continue

        # Prepare payload
        payload = {
            "company_name": lead.company_name,
            "url": lead.url,
            "summary": lead.summary,
            "industry": lead.industry,
            "size_estimate": lead.size_estimate,
            "b2b_buyer": lead.b2b_buyer,
            "lead_score": lead.lead_score,
            "status_tag": lead.status_tag,
            "score_reason": lead.score_reason,
            "headcount_w1": lead.headcount_w1,
            "headcount_w4": lead.headcount_w4,
            "growth_rate": lead.growth_rate,
            "growth_label": lead.growth_label
        }

        # Push to Airtable
        pushed = push_to_airtable(payload)
        
        if pushed:
            success_count += 1
            lead.pushed_to_airtable = True
            await db.commit()
        else:
            logger.error(f"Failed to push lead '{lead.company_name}' to Airtable")
            failed_leads.append({"id": lead_id, "company_name": lead.company_name, "reason": "Airtable push failure"})

    return {
        "success_count": success_count,
        "failed_count": len(failed_leads),
        "failures": failed_leads,
        "message": f"Successfully pushed {success_count} leads to Airtable CRM."
    }
