"""Script to push sample companies from CSV to Airtable."""

import os
import pandas as pd
from dotenv import load_dotenv

from pipeline import (
    get_homepage_url,
    scrape_homepage,
    analyze_company,
    push_to_airtable,
)

load_dotenv()

def process_and_push_companies(csv_path: str = "sample_companies.csv"):
    """Process companies from CSV and push to Airtable."""
    try:
        # Read CSV
        df = pd.read_csv(csv_path)
        print(f"📋 Loaded {len(df)} companies from {csv_path}")
        
        # Get the company name column
        column_name = None
        for col in df.columns:
            if col.lower().strip() == "company name":
                column_name = col
                break
        
        if column_name is None:
            column_name = df.columns[0]
        
        companies = df[column_name].tolist()
        companies = [c for c in companies if c and str(c).lower() != "nan"]
        
        print(f"📋 Processing {len(companies)} companies...")
        
        success_count = 0
        failed_count = 0
        
        for i, company_name in enumerate(companies, 1):
            print(f"\n{'='*60}")
            print(f"Processing {i}/{len(companies)}: {company_name}")
            print(f"{'='*60}")
            
            try:
                # Step 1: Get homepage URL
                print(f"🔍 Searching for homepage URL...")
                url = get_homepage_url(company_name)
                if not url or url == "Not Found":
                    print(f"⚠️ Could not find homepage for {company_name}")
                    failed_count += 1
                    continue
                print(f"✓ Found homepage: {url}")
                
                # Step 2: Scrape homepage
                print(f"🔍 Scraping homepage...")
                scraped_content = scrape_homepage(url)
                if not scraped_content:
                    print(f"⚠️ Failed to scrape homepage for {company_name}")
                    failed_count += 1
                    continue
                print(f"✓ Successfully scraped homepage ({len(scraped_content)} chars)")
                
                # Step 3: Analyze company
                print(f"🔍 Analyzing company with AI...")
                analysis = analyze_company(company_name, url, scraped_content)
                if not analysis or analysis.get("error"):
                    print(f"⚠️ Failed to analyze {company_name}")
                    failed_count += 1
                    continue
                print(f"✓ Successfully analyzed {company_name}")
                print(f"   Industry: {analysis.get('industry')}")
                print(f"   Size: {analysis.get('size_estimate')}")
                print(f"   Lead Score: {analysis.get('lead_score')}")
                
                # Add required fields for Airtable validation
                analysis["company_name"] = company_name
                analysis["url"] = url
                
                # Step 4: Push to Airtable
                print(f"📝 Pushing to Airtable...")
                success = push_to_airtable(analysis)
                if success:
                    print(f"✅ Successfully pushed {company_name} to Airtable")
                    success_count += 1
                else:
                    print(f"❌ Failed to push {company_name} to Airtable")
                    failed_count += 1
                    
            except Exception as e:
                print(f"❌ Error processing {company_name}: {e}")
                import traceback
                print(f"   Traceback: {traceback.format_exc()}")
                failed_count += 1
        
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"✅ Successfully pushed: {success_count} companies")
        print(f"❌ Failed: {failed_count} companies")
        print(f"{'='*60}")
        
        return success_count > 0
        
    except Exception as e:
        print(f"❌ Error processing CSV: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        return False

if __name__ == "__main__":
    process_and_push_companies()
