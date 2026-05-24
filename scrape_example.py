"""Example: Scrape company website using Firecrawl."""

from dotenv import load_dotenv
from pipeline.scraper import scrape_homepage

load_dotenv()

# Example companies to scrape
companies = [
    ("Tesla", "https://www.tesla.com"),
    ("Siemens", "https://www.siemens.com"),
]

for name, url in companies:
    print(f"\n{'='*60}")
    print(f"Scraping {name}: {url}")
    print('='*60)
    
    content = scrape_homepage(url)
    
    if content:
        print(f"✅ Success - {len(content)} characters scraped")
        print(f"\nPreview:")
        print(content[:400] + "..." if len(content) > 400 else content)
    else:
        print("❌ Failed to scrape")
