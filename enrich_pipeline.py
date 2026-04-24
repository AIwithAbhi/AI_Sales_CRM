"""
Enrichment Pipeline - Standalone Script

Loads companies from CSV, enriches them using the existing pipeline
(search → scrape → analyze), pushes to Airtable in batches,
and sends email summary.
"""

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List

import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_filename = f"enrichment_{datetime.utcnow().strftime('%Y-%m-%d')}.log"
log_path = os.path.join(log_dir, log_filename)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_path, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Import existing pipeline modules (no code duplication)
from pipeline import analyze_company, get_homepage_url, push_to_airtable, scrape_homepage
from utils.helpers import get_status_tag

# Cache for URL lookups to avoid re-searching
_url_cache: Dict[str, str] = {}


@lru_cache(maxsize=128)
def cached_get_homepage_url(company_name: str) -> str:
    """Cached version of get_homepage_url for faster repeated lookups."""
    if company_name in _url_cache:
        return _url_cache[company_name]
    
    url = get_homepage_url(company_name)
    if url:
        _url_cache[company_name] = url
    return url


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


def enrich_company(company_name: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    Enrich a single company through the full pipeline.
    
    Args:
        company_name: Name of the company to enrich.
        use_cache: Whether to use URL caching for faster lookups.
        
    Returns:
        Enriched record dict.
    """
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
    
    try:
        # Step 1: Search for homepage URL (with caching)
        if use_cache:
            url = cached_get_homepage_url(company_name)
        else:
            url = get_homepage_url(company_name)
            
        if not url:
            result["error"] = "Website not found"
            return result
        
        result["url"] = url
        
        # Step 2: Scrape homepage content
        homepage_text = scrape_homepage(url)
        if not homepage_text:
            result["error"] = "Failed to scrape website"
            return result
        
        # Step 3: Analyze with NVIDIA AI
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
        
    except Exception as e:
        result["error"] = f"Exception: {str(e)}"
    
    return result


def enrich_companies(companies: List[str], use_cache: bool = True, max_workers: int = 10) -> List[Dict[str, Any]]:
    """
    Enrich multiple companies in parallel using ThreadPoolExecutor.
    
    Args:
        companies: List of company names to enrich.
        use_cache: Whether to use URL caching for faster lookups.
        max_workers: Number of parallel workers (default: 10).
        
    Returns:
        List of enriched company records.
    """
    print(f"⚡ Starting parallel enrichment with {max_workers} workers...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(enrich_company, company, use_cache): company for company in companies}
        results = []
        completed = 0
        
        for future in as_completed(futures):
            company = futures[future]
            completed += 1
            
            try:
                result = future.result()
                status = "✓" if not result.get("error") else "✗"
                print(f"  [{completed}/{len(companies)}] {status} {company}")
            except Exception as e:
                print(f"  [{completed}/{len(companies)}] ✗ {company}: {str(e)}")
                result = {
                    "company_name": company,
                    "error": str(e),
                    "lead_score": 0,
                    "status_tag": "Unknown"
                }
            
            results.append(result)
    
    return results


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


def run_pipeline(csv_path: str = "sample_companies.csv", batch_size: int = 10, dry_run: bool = False) -> None:
    """
    Run the full enrichment pipeline.
    
    Args:
        csv_path: Path to the CSV file with company names.
        batch_size: Number of records to push to Airtable at once.
        dry_run: Test mode: process only 2 companies, skip Airtable and email
    """
    # Show dry run message if enabled
    if dry_run:
        print("=" * 60)
        print("🧪 DRY RUN MODE — no data will be written")
        print("=" * 60)
        print("Only processing first 2 companies for testing")
        print()
    else:
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
    
    # Process all companies in parallel
    if dry_run:
        companies = companies[:2]
        print(f"\n📊 DRY RUN: Processing {len(companies)} companies in parallel...")
    else:
        print(f"\n📊 Processing {len(companies)} companies in parallel (10 workers)...")
    print("-" * 60)
    
    # Use parallel processing with ThreadPoolExecutor
    start_process_time = datetime.utcnow()
    enriched_records = enrich_companies(companies, max_workers=10)
    process_duration = datetime.utcnow() - start_process_time
    
    print(f"\n✓ Parallel processing complete in {process_duration.total_seconds():.1f}s")
    print(f"✓ Processed {len(enriched_records)} companies")
    
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
    
    # In dry run mode, print results as formatted JSON
    if dry_run:
        print("\n" + "=" * 60)
        print("📋 DRY RUN RESULTS (JSON)")
        print("=" * 60)
        for record in enriched_records:
            output = {
                "company_name": record.get("company_name"),
                "url": record.get("url"),
                "lead_score": record.get("lead_score"),
                "status_tag": record.get("status_tag"),
                "industry": record.get("industry"),
                "score_reason": record.get("score_reason"),
                "error": record.get("error")
            }
            print(f"\n{record.get('company_name')}:")
            print(json.dumps(output, indent=2))
        
        print("\n" + "=" * 60)
        print("📤 SKIPPED: Airtable push (dry run mode)")
        print("=" * 60)
        
        print("\n" + "=" * 60)
        print("📧 SKIPPED: Email notification (dry run mode)")
        print("=" * 60)
    else:
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
    if dry_run:
        print(f"✅ DRY RUN complete — {len(companies)} companies processed, nothing pushed")
    else:
        print("✅ Pipeline Complete!")
    print("=" * 60)
    print(f"Finished at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Sales Enrichment Pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode: process only 2 companies, skip Airtable and email"
    )
    args = parser.parse_args()
    
    run_pipeline(dry_run=args.dry_run)