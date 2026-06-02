"""Check Airtable table schema and test push functionality."""

import os
import json
from dotenv import load_dotenv
from pyairtable import Api

load_dotenv()

# Get credentials
api_key = os.getenv("AIRTABLE_API_KEY")
base_id = os.getenv("AIRTABLE_BASE_ID")
table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")
field_name = os.getenv("AIRTABLE_FIELD_NAME", "Company Data Raw")

print("=" * 60)
print("Airtable Schema Check")
print("=" * 60)
print(f"Base ID: {base_id}")
print(f"Table Name: {table_name}")
print(f"Field Name: {field_name}")
print("=" * 60)

try:
    api = Api(api_key)
    table = api.table(base_id, table_name)
    
    # Get table schema
    print("\nFetching table schema...")
    schema = table.schema()
    
    print(f"\nTable '{table_name}' has {len(schema.fields)} fields:")
    for field in schema.fields:
        print(f"  - {field.name} ({field.type})")
    
    # Check if our configured field exists
    field_names = [f.name for f in schema.fields]
    if field_name in field_names:
        print(f"\n✓ Field '{field_name}' exists in table")
    else:
        print(f"\n❌ Field '{field_name}' NOT found in table!")
        print(f"   Available fields: {', '.join(field_names)}")
    
    # Fetch existing records
    print("\nFetching existing records...")
    records = table.all()
    print(f"Found {len(records)} records")
    
    if records:
        print("\nFirst record structure:")
        for key, value in records[0]["fields"].items():
            if isinstance(value, str) and len(value) > 100:
                print(f"  {key}: {value[:100]}...")
            else:
                print(f"  {key}: {value}")
    
    # Test pushing a record
    print("\n" + "=" * 60)
    print("Testing Push Functionality")
    print("=" * 60)
    
    test_record = {
        "company_name": "Test Company 123",
        "url": "https://test123.com",
        "summary": "This is a test company for push functionality",
        "industry": "Technology",
        "size_estimate": "51-200",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": "Test reason for scoring",
        "Headcount W1": 100,
        "Headcount W4": 110,
        "Growth Rate %": 10.0,
        "Growth Label": "Growing"
    }
    
    print(f"\nTest record: {test_record['company_name']}")
    
    # Try to push using the configured field
    try:
        airtable_record = {
            field_name: json.dumps(test_record, indent=2)
        }
        
        print(f"Attempting to create record with field '{field_name}'...")
        created = table.create(airtable_record)
        print(f"✓ Successfully created test record!")
        print(f"  Record ID: {created['id']}")
        
        # Verify it was created
        print("\nVerifying record was created...")
        updated_records = table.all()
        print(f"Total records after push: {len(updated_records)}")
        
    except Exception as e:
        print(f"❌ Failed to create record: {e}")
        print("\nTrying alternative approach with separate fields...")
        
        # Try with separate fields
        try:
            airtable_record = {
                "Company Name": test_record["company_name"],
                "Website": test_record["url"],
                "Summary": test_record["summary"],
                "Industry": test_record["industry"],
                "Size": test_record["size_estimate"],
                "B2B Buyer": test_record["b2b_buyer"],
                "Lead Score": test_record["lead_score"],
                "Status": test_record["status_tag"],
                "Score Reason": test_record["score_reason"],
            }
            
            created = table.create(airtable_record)
            print(f"✓ Successfully created record with separate fields!")
            print(f"  Record ID: {created['id']}")
            
        except Exception as e2:
            print(f"❌ Also failed with separate fields: {e2}")

except Exception as e:
    print(f"❌ Error: {e}")
