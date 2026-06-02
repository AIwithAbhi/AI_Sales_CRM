"""Inspect actual records in Airtable to see their structure."""
from pyairtable import Api
from dotenv import load_dotenv
import os
import json

load_dotenv()

api_key = os.getenv("AIRTABLE_API_KEY")
base_id = os.getenv("AIRTABLE_BASE_ID")
table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

print(f"Base ID: {base_id}")
print(f"Table Name: {table_name}")
print()

api = Api(api_key)
table = api.table(base_id, table_name)

try:
    # Get first few records
    records = table.all(max_records=3)
    
    if records:
        print(f"Found {len(records)} records")
        print("\nRecord structure:")
        print("=" * 50)
        
        for i, record in enumerate(records, 1):
            print(f"\nRecord {i}:")
            print(f"  ID: {record['id']}")
            print(f"  Created: {record['createdTime']}")
            print(f"  Fields: {json.dumps(record['fields'], indent=4)}")
    else:
        print("No records found in table")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
