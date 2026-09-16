"""Test script to check Airtable records and test push functionality."""

import os

from dotenv import load_dotenv
from pyairtable import Api

from pipeline.crm import push_to_airtable

load_dotenv()

api_key = os.getenv("AIRTABLE_API_KEY")
base_id = os.getenv("AIRTABLE_BASE_ID")
table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

print(f"API Key: {api_key[:20]}..." if api_key else "API Key: MISSING")
print(f"Base ID: {base_id}" if base_id else "Base ID: MISSING")
print(f"Table Name: {table_name}")
print()

api = Api(api_key)
table = api.table(base_id, table_name)

records = table.all()
print(f"Total records in Airtable: {len(records)}")
print()

if records:
    print("Current records:")
    for i, r in enumerate(records, 1):
        fields = r.get("fields", {})
        name_value = fields.get("Name", "Unknown")
        print(f"{i}. {name_value}")
else:
    print("No records found in Airtable")

print("\n" + "=" * 50)
print("Testing push to Airtable...")
print("=" * 50)

test_record = {
    "company_name": f"Test Company {len(records) + 1}",
    "url": f"https://test{len(records) + 1}.com",
    "summary": "Test summary",
    "industry": "Technology",
    "size_estimate": "Small",
    "b2b_buyer": True,
    "lead_score": 7,
    "status_tag": "Warm",
    "score_reason": "Test reason",
}

if push_to_airtable(test_record):
    print("Successfully created test record")
else:
    print("Push failed — check console output above")
