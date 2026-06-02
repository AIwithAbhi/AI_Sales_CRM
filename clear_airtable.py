"""Script to clear all records from Airtable."""

import os
from pyairtable import Api
from dotenv import load_dotenv

load_dotenv()

def clear_airtable():
    """Delete all records from the Airtable table."""
    try:
        # Get credentials from environment
        api_key = os.getenv("AIRTABLE_API_KEY")
        base_id = os.getenv("AIRTABLE_BASE_ID")
        table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

        if not api_key or not base_id:
            print("❌ ERROR: AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
            return False

        # Initialize Airtable client
        api = Api(api_key)
        table = api.table(base_id, table_name)

        print(f"🔍 Fetching all records from {table_name}...")
        records = table.all()
        print(f"   Found {len(records)} records")

        if not records:
            print("✓ No records to delete")
            return True

        # Delete all records
        print(f"🗑️  Deleting {len(records)} records...")
        deleted_count = 0
        for record in records:
            try:
                table.delete(record["id"])
                deleted_count += 1
                if deleted_count % 10 == 0:
                    print(f"   Deleted {deleted_count}/{len(records)} records...")
            except Exception as e:
                print(f"   ❌ Error deleting record {record['id']}: {e}")

        print(f"✅ Successfully deleted {deleted_count} records from Airtable")
        return True

    except Exception as e:
        print(f"❌ Error clearing Airtable: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        return False

if __name__ == "__main__":
    clear_airtable()
