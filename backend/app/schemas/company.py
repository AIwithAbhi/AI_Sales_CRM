from typing import List, Optional
from pydantic import BaseModel, Field


class CompanyEnrichRequest(BaseModel):
    companies: List[str] = Field(..., min_items=1, description="List of company names to enrich")
    quick_mode: bool = Field(False, description="Faster AI analysis mode utilizing less context")


class CompanyBulkPushRequest(BaseModel):
    lead_ids: List[int] = Field(..., min_items=1, description="List of LeadRecord IDs to push to Airtable")


class CompanyResponse(BaseModel):
    company_name: str
    url: str
    summary: str
    industry: str
    size_estimate: str
    b2b_buyer: bool
    lead_score: int
    status_tag: str
    score_reason: str
    headcount_w1: int
    headcount_w4: int
    growth_rate: float
    growth_label: str
    error: Optional[str] = None

    class Config:
        from_attributes = True
