from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from backend.app.schemas.company import CompanyResponse


class EnrichmentJobBase(BaseModel):
    job_id: str
    status: str
    total_companies: int
    processed_companies: int
    current_company: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class EnrichmentJobResponse(EnrichmentJobBase):
    progress: float
    results: List[CompanyResponse] = []

    class Config:
        from_attributes = True


class JobStartResponse(BaseModel):
    job_id: str
    status: str
    total_companies: int
    message: str


class LeadRecordResponse(CompanyResponse):
    id: int
    job_id: str
    pushed_to_airtable: bool
    enriched_at: datetime

    class Config:
        from_attributes = True


class HistoryResponse(BaseModel):
    jobs: List[EnrichmentJobBase]
    total_jobs: int
