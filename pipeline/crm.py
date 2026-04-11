"""CRM module for pushing enriched company data to Airtable."""

import os
from datetime import datetime
from typing import Any, Dict

from pyairtable import Api


def push_to_airtable(record: Dict[str, Any]) -> bool:
    """
    Push an enriched company record to Airtable CRM.

    Creates a new record in the configured Airtable base with all
    fields mapped from the enrichment pipeline output.

    Args:
        record: Dictionary containing enriched company data with keys:
            - company_name: str
            - url: str (homepage URL)
            - summary: str
            - industry: str
            - size_estimate: str
            - b2b_buyer: bool
            - lead_score: int
            - status_tag: str (Hot/Warm/Cold)
            - score_reason: str

    Returns:
        True on success, False on failure.

    Note:
        Prints error to console if Airtable write fails but does not
        crash the application. Uses environment variables for
        credentials and table configuration.
    """
    try:
        # Get credentials from environment
        api_key = os.getenv("AIRTABLE_API_KEY")
        base_id = os.getenv("AIRTABLE_BASE_ID")
        table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

        if not api_key or not base_id:
            print("Error: AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
            return False

        # Initialize Airtable client
        api = Api(api_key)
        table = api.table(base_id, table_name)

        # Map fields to Airtable format
        airtable_record = {
            "Company Name": record.get("company_name", ""),
            "Website": record.get("url", ""),
            "Summary": record.get("summary", ""),
            "Industry": record.get("industry", "Other"),
            "Size": record.get("size_estimate", ""),
            "B2B Buyer": record.get("b2b_buyer", False),
            "Lead Score": record.get("lead_score", 0),
            "Status": record.get("status_tag", "Unknown"),
            "Score Reason": record.get("score_reason", ""),
            "Enriched At": datetime.utcnow().isoformat() + "Z",
        }

        # Create record in Airtable
        created = table.create(airtable_record)

        print(f"Successfully created Airtable record for {record.get('company_name')}")
        return True

    except Exception as e:
        print(f"Airtable error for '{record.get('company_name')}': {e}")
        return False
