"""Check Airtable table schema to see available fields."""
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
base = api.base(base_id)

# Get table schema
try:
    tables = base.tables()
    print("Available tables:")
    for table in tables:
        print(f"  - {table.name}")
    print()
    
    # Find the target table
    target_table = None
    for table in tables:
        if table.name.lower() == table_name.lower():
            target_table = table
            break
    
    if target_table:
        print(f"Fields in '{target_table.name}':")
        print("=" * 50)
        for field in target_table.fields:
            print(f"- {field.name} ({field.type})")
    else:
        print(f"Table '{table_name}' not found")
        
except Exception as e:
    print(f"Error: {e}")
