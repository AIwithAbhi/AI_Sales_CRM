"""Test script to verify Firecrawl API key and demonstrate scraping."""

import os
from dotenv import load_dotenv
from pipeline.scraper import scrape_homepage

# Load environment variables
load_dotenv()

# Test URL
test_url = "https://example.com"

print(f"Testing Firecrawl with URL: {test_url}")
print("-" * 50)

# Check if API key is set
api_key = os.getenv("FIRECRAWL_API_KEY")
if not api_key:
    print("❌ ERROR: FIRECRAWL_API_KEY not found in .env file")
else:
    print(f"✅ API Key found: {api_key[:10]}...{api_key[-4:]}")
    print()
    
    # Scrape the URL
    content = scrape_homepage(test_url)
    
    if content:
        print(f"✅ Successfully scraped {len(content)} characters")
        print("-" * 50)
        print("Content preview:")
        print(content[:500] + "..." if len(content) > 500 else content)
    else:
        print("❌ Failed to scrape content")
