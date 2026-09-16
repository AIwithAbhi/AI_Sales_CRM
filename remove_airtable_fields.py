"""Remove unused columns from the Airtable Leads table."""

import os
import sys

import requests
from dotenv import load_dotenv
from pyairtable import Api

load_dotenv()

FIELDS_TO_REMOVE = {
    "Notes",
    "Assignee",
    "Attachments",
    "Summary",
    "Headcount W1",
    "Headcount W4",
    "Growth Rate",
    "Growth Rate %",
    "Growth Label",
    "EnrichAt",
    "Enrich At",
    "Enriched At",
}


def delete_field(api_key: str, base_id: str, table_id: str, field_id: str) -> None:
    url = f"https://api.airtable.com/v0/meta/bases/{base_id}/tables/{table_id}/fields/{field_id}"
    response = requests.delete(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{response.status_code} {response.text}")


def main() -> int:
    api_key = os.getenv("AIRTABLE_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

    if not api_key or not base_id:
        print("Error: AIRTABLE_API_KEY and AIRTABLE_BASE_ID must be set in .env")
        return 1

    api = Api(api_key)
    table = api.table(base_id, table_name)
    schema = table.schema()

    print(f"Table: {table_name} ({schema.id})")
    print(f"Current fields ({len(schema.fields)}):")
    for field in schema.fields:
        print(f"  - {field.name} ({field.type})")

    removed = []
    failed = []

    for field in schema.fields:
        if field.name not in FIELDS_TO_REMOVE:
            continue
        if field.id == schema.primary_field_id:
            print(f"Skip primary field: {field.name}")
            continue
        try:
            delete_field(api_key, base_id, schema.id, field.id)
            removed.append(field.name)
            print(f"Removed: {field.name}")
        except Exception as exc:
            print(f"Failed to remove '{field.name}': {exc}")
            failed.append(field.name)

    print()
    print(f"Removed {len(removed)} field(s): {', '.join(removed) if removed else 'none'}")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        print("Tip: your Airtable token needs schema.bases:write scope to delete columns.")

    updated = table.schema()
    print(f"\nRemaining fields ({len(updated.fields)}):")
    for field in updated.fields:
        print(f"  - {field.name} ({field.type})")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
