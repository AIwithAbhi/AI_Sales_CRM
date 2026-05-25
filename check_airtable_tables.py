"""Check available tables in Airtable base."""
from pyairtable import Api
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("AIRTABLE_API_KEY")
base_id = os.getenv("AIRTABLE_BASE_ID")

print(f"API Key: {api_key[:20]}...")
print(f"Base ID: {base_id}")
print()

api = Api(api_key)
base = api.base(base_id)

# Get all tables in the base
try:
    tables = base.tables()
    print(f"Available tables in base '{base_id}':")
    print("=" * 50)
    for table in tables:
        print(f"- Table Name: {table.name}")
        print(f"  Table ID: {table.id}")
        print()
except Exception as e:
    print(f"Error listing tables: {e}")
