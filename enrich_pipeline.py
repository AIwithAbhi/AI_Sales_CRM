"""
Enrichment Pipeline - Standalone Script

Loads companies from CSV, enriches them using the existing pipeline
(search → scrape → analyze), pushes to Airtable in batches,
and sends email summary.
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import existing pipeline modules (no code duplication)
from pipeline import analyze_company, get_homepage_url, push_to_airtable, scrape_homepage
from utils.helpers import get_status_tag


def load_companies_from_csv(csv_path: str = "sample_companies.csv") -> List[str]:
    """
    Load company names from CSV file.
    
    Args:
        csv_path: Path to the CSV file.
        
    Returns:
        List of company names.
    """
    try:
        df = pd.read_csv(csv_path)
        
        # Try to find 'Company Name' column (case-insensitive)
        column_name = None
        for col in df.columns:
            if col.lower().strip() in ["company_name", "company name"]:
                column_name = col
                break
        
        # If not found, use first column
        if column_name is None:
            column_name = df.columns[0]
            print(f"Using column '{column_name}' as company name source")
        
        # Extract and clean company names
        companies = df[column_name].astype(str).str.strip().tolist()
        companies = [c for c in companies if c and c.lower() not in ("nan", "none")]
        
        print(f"✓ Loaded {len(companies)} companies from {csv_path}")
        return companies
        
    except FileNotFoundError:
        print(f"✗ Error: File not found: {csv_path}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error loading CSV: {e}")
        sys.exit(1)


def enrich_company(company_name: str) -> Dict[str, Any]:
    """
    Enrich a single company through the full pipeline.
    
    Args:
        company_name: Name of the company to enrich.
        
    Returns:
        Enriched record dict.
    """
    print(f"\n  🔍 Processing: {company_name}")
    
    result = {
        "company_name": company_name,
        "url": "",
        "summary": "",
        "industry": "",
        "size_estimate": "",
        "b2b_buyer": False,
        "lead_score": 0,
        "status_tag": "Unknown",
        "score_reason": "",
        "error": None,
    }
    
    # Step 1: Search for homepage URL
    print(f"     → Searching website...")
    url = get_homepage_url(company_name)
    if not url:
        result["error"] = "Website not found"
        print(f"     ✗ Website not found")
        return result
    
    result["url"] = url
    print(f"     ✓ Found: {url}")
    
    # Step 2: Scrape homepage content
    print(f"     → Scraping homepage...")
    homepage_text = scrape_homepage(url)
    if not homepage_text:
        result["error"] = "Failed to scrape website"
        print(f"     ✗ Scraping failed")
        return result
    
    print(f"     ✓ Scraped {len(homepage_text)} characters")
    
    # Step 3: Analyze with NVIDIA AI
    print(f"     → Analyzing with AI...")
    analysis = analyze_company(company_name, homepage_text)
    
    result.update({
        "summary": analysis.get("summary", ""),
        "industry": analysis.get("industry", ""),
        "size_estimate": analysis.get("size_estimate", ""),
        "b2b_buyer": analysis.get("b2b_buyer", False),
        "lead_score": analysis.get("lead_score", 0),
        "score_reason": analysis.get("score_reason", ""),
    })
    
    # Step 4: Determine status tag
    result["status_tag"] = get_status_tag(result["lead_score"])
    
    print(f"     ✓ Score: {result['lead_score']}/10 ({result['status_tag']}) | Industry: {result['industry']}")
    
    return result


def push_batch_to_airtable(records: List[Dict[str, Any]]) -> int:
    """
    Push a batch of records to Airtable.
    
    Args:
        records: List of enriched company records.
        
    Returns:
        Number of successfully pushed records.
    """
    pushed_count = 0
    
    for record in records:
        if record.get("error"):
            continue
            
        airtable_record = {
            "company_name": record.get("company_name", ""),
            "url": record.get("url", ""),
            "summary": record.get("summary", ""),
            "industry": record.get("industry", ""),
            "size_estimate": record.get("size_estimate", ""),
            "b2b_buyer": record.get("b2b_buyer", False),
            "lead_score": record.get("lead_score", 0),
            "status_tag": record.get("status_tag", ""),
            "score_reason": record.get("score_reason", ""),
        }
        
        if push_to_airtable(airtable_record):
            pushed_count += 1
    
    return pushed_count


def notify_email(enriched_records: List[Dict[str, Any]]) -> None:
    """
    Send HTML email with enrichment summary.
    
    Args:
        enriched_records: List of all enriched company records.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    # Read environment variables
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    recipient = os.getenv("EMAIL_RECIPIENT")
    smtp_host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    
    # Silently skip if any required variable is missing
    if not sender or not password or not recipient:
        return
    
    # Calculate stats
    total = len(enriched_records)
    successful = [r for r in enriched_records if r.get("error") is None]
    hot_count = sum(1 for r in successful if r.get("status_tag") == "Hot")
    warm_count = sum(1 for r in successful if r.get("status_tag") == "Warm")
    cold_count = sum(1 for r in successful if r.get("status_tag") == "Cold")
    
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Build HTML table rows
    rows = []
    for record in successful:
        status = record.get("status_tag", "Unknown")
        score = record.get("lead_score", 0)
        
        # Set row color based on status
        if status == "Hot":
            bg_color = "#d4edda"  # Light green
        elif status == "Cold":
            bg_color = "#f8d7da"  # Light red
        elif status == "Warm":
            bg_color = "#fff3cd"  # Light yellow
        else:
            bg_color = "#ffffff"  # White
        
        row = f"""
        <tr style="background-color: {bg_color};">
            <td style="padding: 8px; border: 1px solid #ddd;">{record.get('company_name', '')}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{score}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{status}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{record.get('industry', '')}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{record.get('score_reason', '')}</td>
        </tr>
        """
        rows.append(row)
    
    # Build HTML email
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
            .summary {{ margin-bottom: 20px; }}
            .summary p {{ margin: 5px 0; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th {{ background-color: #343a40; color: white; padding: 10px; text-align: left; }}
            td {{ padding: 8px; border: 1px solid #ddd; }}
            .footer {{ margin-top: 20px; font-size: 12px; color: #6c757d; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>🎯 Daily Lead Enrichment Report</h2>
            <p>Date: {today}</p>
        </div>
        
        <div class="summary">
            <h3>Summary</h3>
            <p><strong>Total Companies:</strong> {total}</p>
            <p><strong>Successfully Enriched:</strong> {len(successful)}</p>
            <p>🔥 <strong>Hot Leads:</strong> {hot_count}</p>
            <p>🌟 <strong>Warm Leads:</strong> {warm_count}</p>
            <p>❄️ <strong>Cold Leads:</strong> {cold_count}</p>
        </div>
        
        <h3>Enriched Companies</h3>
        <table>
            <thead>
                <tr>
                    <th>Company Name</th>
                    <th>Score</th>
                    <th>Status</th>
                    <th>Industry</th>
                    <th>Score Reason</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        
        <div class="footer">
            <p>Generated by AI Sales Enrichment Pipeline</p>
            <p>Completed: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
        </div>
    </body>
    </html>
    """
    
    # Create email message
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = f"Daily Lead Enrichment Report — {today}"
    
    # Attach HTML body
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        # Connect to SMTP server
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)
        
        print(f"\n✓ Email sent to {recipient}")
        
    except Exception:
        # Silently ignore failures
        pass


def run_pipeline(csv_path: str = "sample_companies.csv", batch_size: int = 10) -> None:
    """
    Run the full enrichment pipeline.
    
    Args:
        csv_path: Path to the CSV file with company names.
        batch_size: Number of records to push to Airtable at once.
    """
    print("=" * 60)
    print("🚀 AI Sales Enrichment Pipeline")
    print("=" * 60)
    print(f"Started at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()
    
    # Check required environment variables
    required_vars = ["FIRECRAWL_API_KEY", "NVIDIA_API_KEY", "AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print("✗ Missing required environment variables:")
        for var in missing:
            print(f"   - {var}")
        print("\nPlease set these in your .env file and try again.")
        sys.exit(1)
    
    print("✓ Environment variables loaded")
    print(f"✓ Airtable Table: {os.getenv('AIRTABLE_TABLE_NAME', 'Leads')}")
    print()
    
    # Load companies
    companies = load_companies_from_csv(csv_path)
    
    if not companies:
        print("✗ No companies to process")
        sys.exit(1)
    
    # Process all companies
    print(f"\n📊 Processing {len(companies)} companies...")
    print("-" * 60)
    
    enriched_records: List[Dict[str, Any]] = []
    
    for i, company in enumerate(companies, 1):
        print(f"\n[{i}/{len(companies)}]", end="")
        
        record = enrich_company(company)
        enriched_records.append(record)
        
        # Progress indicator
        if i % 5 == 0:
            print(f"\n   Progress: {i}/{len(companies)} companies processed")
    
    # Summary stats
    successful = [r for r in enriched_records if r.get("error") is None]
    failed = [r for r in enriched_records if r.get("error")]
    hot_leads = [r for r in successful if r.get("status_tag") == "Hot"]
    warm_leads = [r for r in successful if r.get("status_tag") == "Warm"]
    cold_leads = [r for r in successful if r.get("status_tag") == "Cold"]
    
    print("\n" + "=" * 60)
    print("📈 Processing Summary")
    print("=" * 60)
    print(f"Total:        {len(enriched_records)} companies")
    print(f"Successful:   {len(successful)} ✓")
    print(f"Failed:       {len(failed)} ✗")
    print(f"\nLead Breakdown:")
    print(f"  🔥 Hot:    {len(hot_leads)} (score 8-10)")
    print(f"  🌟 Warm:   {len(warm_leads)} (score 5-7)")
    print(f"  ❄️  Cold:   {len(cold_leads)} (score 1-4)")
    
    if failed:
        print(f"\n⚠️  Failed Companies:")
        for f in failed:
            print(f"   - {f['company_name']}: {f['error']}")
    
    # Push to Airtable in batches
    print("\n" + "=" * 60)
    print("📤 Pushing to Airtable (batches of 10)")
    print("=" * 60)
    
    total_pushed = 0
    batches = [successful[i:i + batch_size] for i in range(0, len(successful), batch_size)]
    
    for batch_num, batch in enumerate(batches, 1):
        print(f"\nBatch {batch_num}/{len(batches)}: Pushing {len(batch)} records...")
        pushed = push_batch_to_airtable(batch)
        total_pushed += pushed
        print(f"  ✓ Pushed {pushed}/{len(batch)} records")
    
    print(f"\n✓ Total pushed to Airtable: {total_pushed}/{len(successful)}")
    
    # Send email notification (CHANGED FROM SLACK TO EMAIL)
    print("\n" + "=" * 60)
    print("📧 Sending Email Notification")
    print("=" * 60)
    notify_email(enriched_records)
    
    # Final summary
    print("\n" + "=" * 60)
    print("✅ Pipeline Complete!")
    print("=" * 60)
    print(f"Finished at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    run_pipeline()