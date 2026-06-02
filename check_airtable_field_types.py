"""Check field types in Airtable table."""
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
base = api.base(base_id)

# Get table schema
try:
    tables = base.tables()
    for table in tables:
        if table.name == table_name:
            print(f"Table: {table.name}")
            print(f"Table ID: {table.id}")
            print()
            print("Note: pyairtable doesn't expose field type information directly.")
            print("Please check field types in Airtable UI:")
            print(f"https://airtable.com/{base_id}/{table.id}")
            break
except Exception as e:
    print(f"Error: {e}")
