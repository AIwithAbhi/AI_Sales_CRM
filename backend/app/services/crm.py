from datetime import datetime
from typing import Any, Dict
from pyairtable import Api

from backend.app.config import settings
from backend.app.utils.logging import logger


def push_to_airtable(record: Dict[str, Any]) -> bool:
    """
    Push an enriched company record to Airtable CRM.

    Creates a new record in the configured Airtable base with all
    fields mapped from the enrichment pipeline output.

    Args:
        record: Dictionary containing enriched company data keys:
            - company_name: str
            - url: str
            - summary: str
            - industry: str
            - size_estimate: str
            - b2b_buyer: bool
            - lead_score: int
            - status_tag: str
            - score_reason: str
            - headcount_w1: int
            - headcount_w4: int
            - growth_rate: float
            - growth_label: str

    Returns:
        True on success, False on failure or skip.
    """
    try:
        # Get credentials from settings
        api_key = settings.AIRTABLE_API_KEY
        base_id = settings.AIRTABLE_BASE_ID
        table_name = settings.AIRTABLE_TABLE_NAME
        field_name = settings.AIRTABLE_FIELD_NAME

        if not api_key or not base_id:
            logger.error("AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set in configuration")
            return False

        # Initialize Airtable client
        api = Api(api_key)
        table = api.table(base_id, table_name)

        logger.info(f"Connecting to Airtable CRM. Table: '{table_name}', Field: '{field_name}'")

        # Check for duplicates by company name
        existing_records = table.all()
        company_name = record.get("company_name", "").strip().lower()
        
        for existing in existing_records:
            # Check if company name exists in the target field
            company_data = existing["fields"].get(field_name, "")
            # Handle both string and dict types for the field
            if isinstance(company_data, str):
                if company_name in company_data.lower():
                    logger.warning(f"Skipping duplicate: {record.get('company_name')} already exists in Airtable")
                    return False
            elif isinstance(company_data, dict):
                # Skip duplicate check for dict fields (attachment type)
                pass

        # Map fields to Airtable format
        # Using the configured field name to store all data as formatted text
        summary_text = f"""
Company: {record.get("company_name", "")}
Website: {record.get("url", "")}
Summary: {record.get("summary", "")}
Industry: {record.get("industry", "Other")}
Size: {record.get("size_estimate", "")}
B2B Buyer: {record.get("b2b_buyer", False)}
Lead Score: {record.get("lead_score", 0)}
Status: {record.get("status_tag", "Unknown")}
Score Reason: {record.get("score_reason", "")}
Headcount W1: {record.get("headcount_w1", 0)}
Headcount W4: {record.get("headcount_w4", 0)}
Growth Rate %: {record.get("growth_rate", 0.0)}
Growth Label: {record.get("growth_label", "No data")}
Enriched At: {datetime.utcnow().isoformat()}Z
        """.strip()

        airtable_record = {
            field_name: summary_text
        }

        # Create record in Airtable
        table.create(airtable_record)

        logger.info(f"Successfully pushed and created Airtable record for {record.get('company_name')}")
        return True

    except Exception as e:
        logger.error(f"Airtable push failure for '{record.get('company_name')}': {e}")
        return False
