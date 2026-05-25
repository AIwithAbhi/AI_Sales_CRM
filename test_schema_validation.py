"""Test Airtable schema validation."""
from dotenv import load_dotenv
load_dotenv()

from pipeline.crm import push_to_airtable

test_record = {
    "company_name": "Test Company Schema",
    "url": "https://testschema.com",
    "summary": "Test summary for schema validation",
    "industry": "Technology",
    "size_estimate": "Small",
    "b2b_buyer": True,
    "lead_score": 50,
    "status_tag": "Warm",
    "score_reason": "Test reason",
    "Headcount W1": 10,
    "Headcount W4": 15,
    "Growth Rate %": 50.0,
    "Growth Label": "Growing",
}

print("Testing Airtable schema validation...")
print("=" * 60)

result = push_to_airtable(test_record)

if result:
    print("✓ Schema validation passed and record created successfully")
else:
    print("✗ Schema validation failed or record creation failed")

print("=" * 60)
