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
        field_name = os.getenv("AIRTABLE_FIELD_NAME", "Company Data")

        print(f"🔍 DEBUG: Attempting to push {record.get('company_name')}")
        print(f"   API Key: {'SET' if api_key else 'MISSING'}")
        print(f"   Base ID: {base_id}")
        print(f"   Table Name: {table_name}")
        print(f"   Field Name: {field_name}")

        if not api_key or not base_id:
            print("❌ ERROR: AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
            return False

        # Initialize Airtable client
        api = Api(api_key)
        table = api.table(base_id, table_name)

        print(f"✓ Connected to Airtable")

        # Check for duplicates by company name
        print(f"🔍 Checking for duplicates...")
        existing_records = table.all()
        company_name = record.get("company_name", "").strip().lower()

        print(f"   Company name to check: {company_name}")
        print(f"   Existing records count: {len(existing_records)}")

        for existing in existing_records:
            # Check if company name exists in the Company Data Raw field
            existing_raw = existing["fields"].get(field_name, "")
            if existing_raw:
                try:
                    import json
                    parsed_data = json.loads(existing_raw)
                    existing_company = parsed_data.get("company_name", "").strip().lower()
                    if existing_company == company_name:
                        print(f"⚠️ Skipping duplicate: {record.get('company_name')} already exists in Airtable")
                        print(f"   Found exact match in existing record")
                        return False
                except:
                    # If parsing fails, fall back to substring check
                    if company_name in str(existing_raw).lower():
                        print(f"⚠️ Skipping duplicate (fallback): {record.get('company_name')} already exists in Airtable")
                        print(f"   Found in existing record: {existing_raw[:100]}...")
                        return False

        # Map fields to Airtable format - using the configured field name
        import json
        airtable_record = {
            field_name: json.dumps(record, indent=2)
        }

        print(f"📝 Creating record with fields: {list(airtable_record.keys())}")
        print(f"   Data length: {len(airtable_record[field_name])} characters")

        # Create record in Airtable
        created = table.create(airtable_record)

        print(f"✅ Successfully created Airtable record for {record.get('company_name')}")
        print(f"   Record ID: {created.get('id')}")
        return True

    except Exception as e:
        print(f"❌ Airtable error for '{record.get('company_name')}': {e}")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Record data: {record}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        return False


def fetch_from_airtable() -> list:
    """
    Fetch all records from Airtable CRM.

    Returns:
        List of dictionaries containing all records from the configured Airtable table.
        Returns empty list on failure.

    Note:
        Uses environment variables for credentials and table configuration.
    """
    try:
        # Get credentials from environment
        api_key = os.getenv("AIRTABLE_API_KEY")
        base_id = os.getenv("AIRTABLE_BASE_ID")
        table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

        if not api_key or not base_id:
            print("Error: AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
            return []

        # Initialize Airtable client
        api = Api(api_key)
        table = api.table(base_id, table_name)

        # Fetch all records
        records = table.all()

        # Extract field data from records
        result = []
        for record in records:
            fields = record["fields"]

            # Check if data is stored in "Company Data Raw" field
            if "Company Data Raw" in fields:
                # Try to parse the raw data if it's a string
                raw_data = fields["Company Data Raw"]
                if isinstance(raw_data, str):
                    import json
                    try:
                        parsed = json.loads(raw_data)
                        # Merge parsed data with other fields
                        fields.update(parsed)
                    except:
                        pass

            result.append(fields)

        print(f"✓ Fetched {len(result)} records from Airtable")
        return result

    except Exception as e:
        print(f"❌ Error fetching from Airtable: {e}")
        return []
