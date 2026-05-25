"""Check fields in Airtable table."""
from pyairtable import Api
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("AIRTABLE_API_KEY")
base_id = os.getenv("AIRTABLE_BASE_ID")
table_name = os.getenv("AIRTABLE_TABLE_NAME", "Table 1")

print(f"API Key: {api_key[:20]}...")
print(f"Base ID: {base_id}")
print(f"Table Name: {table_name}")
print()

api = Api(api_key)
table = api.table(base_id, table_name)

# Get table schema/fields
try:
    # Get one record to see the fields
    records = table.all(max_records=1)
    if records:
        print("Fields in table:")
        print("=" * 50)
        for field_name in records[0]['fields'].keys():
            print(f"- {field_name}")
    else:
        print("No records found, table might be empty")
        print("Please check your Airtable table structure")
except Exception as e:
    print(f"Error: {e}")
