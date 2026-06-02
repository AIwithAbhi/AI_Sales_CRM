"""Get actual field names from Airtable by fetching records."""
from pyairtable import Api
from dotenv import load_dotenv
import os

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
    # Get first 10 records to see field names
    records = table.all(max_records=10)
    
    if records:
        print(f"Found {len(records)} records")
        print("\nField names from existing records:")
        print("=" * 50)
        # Get all unique field names from all records
        all_fields = set()
        for record in records:
            all_fields.update(record['fields'].keys())
        
        for field in sorted(all_fields):
            print(f"- {field}")
    else:
        print("No records found in table")
        print("Table is empty - need to know what fields to create")
        
except Exception as e:
    print(f"Error: {e}")
