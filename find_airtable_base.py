"""
Helper script to find your Airtable Base ID
Run this to list all your Airtable bases
"""
import os
from pyairtable import Api
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("AIRTABLE_API_KEY")

if not api_key:
    print("❌ AIRTABLE_API_KEY not found in .env")
    exit(1)

print("🔍 Finding your Airtable bases...\n")

try:
    api = Api(api_key)
    
    # List all bases
    for base in api.bases():
        print(f"Base ID: {base['id']}")
        print(f"Name: {base.get('name', 'N/A')}")
        print(f"Permission: {base.get('permissionLevel', 'N/A')}")
        print("-" * 50)
        
except Exception as e:
    print(f"❌ Error: {e}")
    print("\n💡 Make sure your AIRTABLE_API_KEY is correct!")
