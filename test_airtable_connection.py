"""Test Airtable connection and check existing records."""

import os
from dotenv import load_dotenv
from pyairtable import Api

load_dotenv()

# Get credentials
api_key = os.getenv("AIRTABLE_API_KEY")
base_id = os.getenv("AIRTABLE_BASE_ID")
table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

print("=" * 50)
print("Airtable Connection Test")
print("=" * 50)
print(f"API Key: {api_key[:20]}... (truncated)" if api_key else "API Key: MISSING")
print(f"Base ID: {base_id}" if base_id else "Base ID: MISSING")
print(f"Table Name: {table_name}")
print("=" * 50)

if not api_key or not base_id:
    print("❌ ERROR: Missing AIRTABLE_API_KEY or AIRTABLE_BASE_ID")
    exit(1)

try:
    # Initialize Airtable client
    api = Api(api_key)
    table = api.table(base_id, table_name)
    
    print("✓ Connected to Airtable successfully")
    
    # Fetch all records
    print("\nFetching records...")
    records = table.all()
    
    print(f"✓ Found {len(records)} records in table '{table_name}'")
    
    if records:
        print("\nFirst record fields:")
        for key, value in records[0]["fields"].items():
            print(f"  {key}: {value}")
    else:
        print("\n⚠️ Table is empty - no records found")
        
        # Try to create a test record
        print("\nAttempting to create a test record...")
        test_record = {
            "Company Name": "Test Company",
            "Website": "https://example.com",
            "Summary": "Test summary",
            "Industry": "Technology",
            "Size": "51-200",
            "B2B Buyer": True,
            "Lead Score": 8,
            "Status": "Hot",
            "Score Reason": "Test reason",
        }
        
        try:
            created = table.create(test_record)
            print("✓ Test record created successfully")
            print(f"  Record ID: {created['id']}")
        except Exception as e:
            print(f"❌ Failed to create test record: {e}")
            print("\nThis might indicate a field name mismatch or permission issue.")
            
except Exception as e:
    print(f"❌ ERROR: {e}")
    print("\nPossible issues:")
    print("1. Invalid API Key or Base ID")
    print("2. Table name doesn't match")
    print("3. API token doesn't have write permissions")
