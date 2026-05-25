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
            - Headcount W1: int (LinkedIn headcount week 1)
            - Headcount W4: int (LinkedIn headcount week 4)
            - Growth Rate %: float (headcount growth percentage)
            - Growth Label: str (growth trend label)

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

        # Validate Airtable schema before proceeding
        print(f"🔍 Validating Airtable schema for table '{table_name}'...")
        try:
            # Get table schema by fetching one record
            existing_records = table.all(max_records=1)
            
            if existing_records:
                # Extract field names from the first record
                field_names = list(existing_records[0]['fields'].keys())
                print(f"📋 Available Airtable fields: {', '.join(field_names)}")
                
                # Check if required field exists
                required_field = "Company Data"
                if required_field not in field_names:
                    print(f"❌ Missing Airtable field: {required_field}")
                    print(f"   Please add the '{required_field}' field (Long text type) to your Airtable table")
                    return False
                else:
                    print(f"✓ Required field '{required_field}' found")
            else:
                # Table is empty, we can't validate schema
                print(f"⚠️ Table is empty, cannot validate schema. Proceeding with caution...")
                print(f"   Please ensure the 'Company Data' field exists in your Airtable table")
                
        except Exception as schema_error:
            print(f"⚠️ Schema validation failed: {schema_error}")
            print(f"   Proceeding with caution. Please ensure 'Company Data' field exists")

        # Check for duplicates by company name
        existing_records = table.all()
        company_name = record.get("company_name", "").strip().lower()
        
        for existing in existing_records:
            # Check if company name exists in the Company Data field
            company_data = existing["fields"].get("Company Data", "")
            if company_name in company_data.lower():
                print(f"⚠️ Skipping duplicate: {record.get('company_name')} already exists in Airtable")
                return False

        # Map fields to Airtable format
        # Using "Company Data" field to store all data as formatted text
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
Headcount W1: {record.get("Headcount W1", 0)}
Headcount W4: {record.get("Headcount W4", 0)}
Growth Rate %: {record.get("Growth Rate %", 0.0)}
Growth Label: {record.get("Growth Label", "No data")}
Enriched At: {datetime.utcnow().isoformat()}Z
        """.strip()

        airtable_record = {
            "Company Data": summary_text
        }

        # Create record in Airtable
        created = table.create(airtable_record)

        print(f"Successfully created Airtable record for {record.get('company_name')}")
        return True

    except Exception as e:
        print(f"❌ Airtable error for '{record.get('company_name')}': {e}")
        print(f"   Record data: {record}")
        return False
