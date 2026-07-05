from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from backend.app.database import Base


class EnrichmentJob(Base):
    __tablename__ = "enrichment_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String, unique=True, index=True, nullable=False)
    user_email = Column(String, index=True, nullable=False)
    status = Column(String, default="pending")  # "pending", "running", "completed", "failed", "cancelled"
    total_companies = Column(Integer, default=0)
    processed_companies = Column(Integer, default=0)
    current_company = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "user_email": self.user_email,
            "status": self.status,
            "total_companies": self.total_companies,
            "processed_companies": self.processed_companies,
            "current_company": self.current_company,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LeadRecord(Base):
    __tablename__ = "lead_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String, index=True, nullable=False)
    company_name = Column(String, nullable=False)
    url = Column(String, default="")
    summary = Column(Text, default="")
    industry = Column(String, default="")
    size_estimate = Column(String, default="")
    b2b_buyer = Column(Boolean, default=False)
    lead_score = Column(Integer, default=0)
    status_tag = Column(String, default="Unknown")
    score_reason = Column(Text, default="")
    
    # Headcount tracking fields
    headcount_w1 = Column(Integer, default=0)
    headcount_w4 = Column(Integer, default=0)
    growth_rate = Column(Float, default=0.0)
    growth_label = Column(String, default="No data")
    
    # Operations flags
    pushed_to_airtable = Column(Boolean, default=False)
    enriched_at = Column(DateTime, default=datetime.utcnow)
    error = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "company_name": self.company_name,
            "url": self.url,
            "summary": self.summary,
            "industry": self.industry,
            "size_estimate": self.size_estimate,
            "b2b_buyer": self.b2b_buyer,
            "lead_score": self.lead_score,
            "status_tag": self.status_tag,
            "score_reason": self.score_reason,
            "headcount_w1": self.headcount_w1,
            "headcount_w4": self.headcount_w4,
            "growth_rate": self.growth_rate,
            "growth_label": self.growth_label,
            "pushed_to_airtable": self.pushed_to_airtable,
            "enriched_at": self.enriched_at.isoformat() if self.enriched_at else None,
            "error": self.error,
        }
