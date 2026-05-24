"""Test script to check Airtable records and test push functionality."""
from pyairtable import Api
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("AIRTABLE_API_KEY")
base_id = os.getenv("AIRTABLE_BASE_ID")
table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

print(f"API Key: {api_key[:20]}...")
print(f"Base ID: {base_id}")
print(f"Table Name: {table_name}")
print()

api = Api(api_key)
table = api.table(base_id, table_name)

# Get all records
records = table.all()
print(f"Total records in Airtable: {len(records)}")
print()

if records:
    print("Current records:")
    for i, r in enumerate(records, 1):
        print(f"{i}. {r['fields'].get('Company Name', 'N/A')} - {r['fields'].get('Website', 'N/A')}")
else:
    print("No records found in Airtable")

# Test adding a new record
print("\n" + "="*50)
print("Testing push to Airtable...")
print("="*50)

test_record = {
    "Company Name": "Test Company " + str(len(records) + 1),
    "Website": "https://test" + str(len(records) + 1) + ".com",
    "Summary": "Test summary",
    "Industry": "Technology",
    "Size": "Small",
    "B2B Buyer": True,
    "Lead Score": 50,
    "Status": "Warm",
    "Score Reason": "Test reason",
    "Headcount W1": 10,
    "Headcount W4": 15,
    "Growth Rate %": 50.0,
    "Growth Label": "Growing",
}

try:
    created = table.create(test_record)
    print(f"✓ Successfully created test record: {created['fields'].get('Company Name')}")
    print(f"  Record ID: {created['id']}")
except Exception as e:
    print(f"❌ Error creating test record: {e}")
