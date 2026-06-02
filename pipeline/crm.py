"""CRM module for pushing enriched company data to Airtable."""

import json
import os
from datetime import datetime
from typing import Any, Dict

from pyairtable import Api


def validate_record(record: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate that a record does not contain error JSON objects.
    
    Args:
        record: Dictionary containing company data
        
    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if record is valid, False if it contains error JSON
        - error_message: Description of validation error if invalid
    """
    error_indicators = [
        "state", "errorType", "error", "exception", "traceback", "failed"
    ]
    
    for key, value in record.items():
        # Check if value is a string that looks like error JSON
        if isinstance(value, str):
            # Check if it's a JSON string with error indicators
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    for indicator in error_indicators:
                        if indicator in parsed:
                            return False, f"Field '{key}' contains error JSON with '{indicator}'"
            except json.JSONDecodeError:
                pass  # Not JSON, continue checking
        
        # Check if value is a dict with error indicators
        if isinstance(value, dict):
            for indicator in error_indicators:
                if indicator in value:
                    return False, f"Field '{key}' is a dict with error indicator '{indicator}'"
    
    # Validate required fields are not error-like
    required_fields = ["company_name", "url", "summary", "industry", "size_estimate"]
    for field in required_fields:
        if field not in record:
            return False, f"Missing required field: {field}"
        
        value = record[field]
        if not value or value == "Not Found" or value == "":
            return False, f"Field '{field}' is empty or 'Not Found'"
    
    return True, ""



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
        Validates record before writing to prevent error JSON from being stored.
        Prints error to console if Airtable write fails but does not
        crash the application. Uses environment variables for
        credentials and table configuration.
    """
    try:
        # Validate record before processing
        is_valid, validation_error = validate_record(record)
        if not is_valid:
            print(f"❌ VALIDATION FAILED for '{record.get('company_name')}': {validation_error}")
            print(f"   Record rejected - will NOT be written to Airtable")
            print(f"   Record data: {json.dumps(record, indent=2)[:500]}...")
            return False

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
        print(f"   ✓ Record validation passed")

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
        airtable_record = {
            field_name: json.dumps(record, indent=2)
        }

        print(f"📝 Creating record with fields: {list(airtable_record.keys())}")
        print(f"   Data length: {len(airtable_record[field_name])} characters")
        print(f"   Record preview: {json.dumps(record, indent=2)[:300]}...")

        # Create record in Airtable
        created = table.create(airtable_record)

        print(f"✅ Successfully created Airtable record for {record.get('company_name')}")
        print(f"   Record ID: {created.get('id')}")
        return True

    except Exception as e:
        print(f"❌ Airtable error for '{record.get('company_name')}': {e}")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Record data: {json.dumps(record, indent=2)[:500]}...")
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
