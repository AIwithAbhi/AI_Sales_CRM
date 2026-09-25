"""
Search Pipeline - Standalone Script

Loads companies from CSV, searches them using the existing pipeline
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
log_filename = f"search_{datetime.utcnow().strftime('%Y-%m-%d')}.log"
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
from pipeline import analyze_company, get_homepage_url, push_to_airtable, scrape_homepage, search_company_info
from utils.funnel_log import log_funnel_stage
from utils.helpers import load_headcount_data
from utils.lead_scoring import (
    compute_enterprise_readiness_tier,
    compute_weighted_lead_score,
)

# Cache for URL lookups to avoid re-searching
_url_cache: Dict[str, str] = {}
_search_cache: Dict[str, tuple] = {}


@lru_cache(maxsize=128)
def cached_get_homepage_url(company_name: str) -> str:
    """Cached version of get_homepage_url for faster repeated lookups."""
    if company_name in _url_cache:
        return _url_cache[company_name]
    
    url = get_homepage_url(company_name)
    if url:
        _url_cache[company_name] = url
    return url


def cached_search_company_info(company_name: str) -> tuple:
    """Cached version of search_company_info for faster repeated lookups."""
    if company_name in _search_cache:
        return _search_cache[company_name]
    
    res = search_company_info(company_name)
    if res[0]:
        _search_cache[company_name] = res
    return res


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


def search_company(
    company_name: str,
    use_cache: bool = True,
    headcount_data: Dict[str, Any] = None,
    run_id: str = None,
) -> Dict[str, Any]:
    """
    Search a single company through the full pipeline.
    
    Args:
        company_name: Name of the company to search.
        use_cache: Whether to use URL caching for faster lookups.
        headcount_data: Optional pre-loaded headcount data dictionary.
        run_id: Optional funnel logging run id.
        
    Returns:
        Searched record dict.
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
        "growth_label": "No data",
        "growth_rate": 0.0,
        "error": None,
    }
    
    try:
        # Load headcount data if not provided
        if headcount_data is None:
            headcount_data = load_headcount_data()
        
        # Look up headcount for this company
        company_key = company_name.strip().lower()
        headcount_info = headcount_data.get(company_key, {})
        
        growth_rate = headcount_info.get("growth_rate", 0.0)
        growth_label = headcount_info.get("growth_label", "No data")
        
        result["growth_rate"] = growth_rate
        result["growth_label"] = growth_label
        
        # Build headcount context for AI prompt
        headcount_context = f"LinkedIn headcount trend: {growth_label} ({growth_rate:.1f}% over 4 weeks)"
        
        # Step 1: Search for homepage URL and get search context (with caching)
        if use_cache:
            url, search_context = cached_search_company_info(company_name)
        else:
            url, search_context = search_company_info(company_name)
            
        if not url:
            result["error"] = "Website not found"
            if run_id:
                log_funnel_stage(company_name, run_id, "failed", failure_reason=result["error"])
            return result
        
        result["url"] = url
        
        # Step 2: Scrape homepage content, fall back to search summary if it fails
        homepage_text = scrape_homepage(url)
        print(f"  [{company_name}] Scraped {len(homepage_text) if homepage_text else 0} characters")
        if not homepage_text:
            if search_context:
                print(f"  [{company_name}] Scraping failed. Using search results fallback.")
                homepage_text = f"[Scraping failed. Using search results fallback]\n\n{search_context}"
            else:
                result["error"] = "Failed to scrape website"
                if run_id:
                    log_funnel_stage(company_name, run_id, "failed", failure_reason=result["error"])
                return result

        if run_id:
            log_funnel_stage(company_name, run_id, "scraped")
        
        # Debug: print first 200 chars of text
        print(f"  [{company_name}] Text preview: {homepage_text[:200]}...")
        
        # Step 3: Analyze with NVIDIA AI (with headcount context)
        print(f"  [{company_name}] Sending to AI with context: {headcount_context}")
        analysis = analyze_company(company_name, homepage_text, headcount_context)
        print(
            f"  [{company_name}] AI signals: industry={analysis.get('industry')}, "
            f"size={analysis.get('size_estimate')}, b2b={analysis.get('b2b_buyer')}"
        )

        result.update({
            "summary": analysis.get("summary", ""),
            "industry": analysis.get("industry", ""),
            "size_estimate": analysis.get("size_estimate", ""),
            "b2b_buyer": bool(analysis.get("b2b_buyer", False)),
            "b2b_evidence": analysis.get("b2b_evidence", ""),
            "business_model": analysis.get("business_model", ""),
            "buying_signals": analysis.get("buying_signals") or [],
            "score_reason": analysis.get("score_reason", ""),
            "lead_score_rationale": analysis.get("lead_score_rationale")
            or analysis.get("score_reason", ""),
            "lead_score_confidence": analysis.get("lead_score_confidence", "low"),
            "ai_maturity_score": analysis.get("ai_maturity_score", 1),
            "ai_maturity_reason": analysis.get("ai_maturity_reason", ""),
            "ai_maturity_confidence": analysis.get("ai_maturity_confidence", "low"),
            "transformation_readiness_score": analysis.get(
                "transformation_readiness_score", 1
            ),
            "transformation_readiness_reason": analysis.get(
                "transformation_readiness_reason", ""
            ),
            "transformation_readiness_confidence": analysis.get(
                "transformation_readiness_confidence", "low"
            ),
            "profile_scores": analysis.get("profile_scores") or {},
            "scoring_profile_ids": analysis.get("scoring_profile_ids") or [],
        })

        # Step 4: Same weighted lead score as the web path (do NOT trust AI lead_score)
        scored = compute_weighted_lead_score(result)
        result["lead_score"] = scored["lead_score"]
        result["status_tag"] = scored["status_tag"]
        result["score_reason"] = scored["score_reason"]
        result["score_breakdown"] = scored["score_breakdown"]
        result["buying_signals"] = scored["buying_signals"]
        print(
            f"  [{company_name}] Weighted score={result['lead_score']} "
            f"status={result['status_tag']}"
        )

        readiness = compute_enterprise_readiness_tier(
            result.get("ai_maturity_score"),
            result.get("transformation_readiness_score"),
        )
        result.update(readiness)
        if run_id:
            log_funnel_stage(
                company_name,
                run_id,
                "scored",
                lead_score=result.get("lead_score"),
                status_tag=result.get("status_tag"),
                industry=result.get("industry"),
            )
        
    except Exception as e:
        result["error"] = f"Exception: {str(e)}"
        if run_id:
            log_funnel_stage(company_name, run_id, "failed", failure_reason=result["error"])
    
    return result


def search_companies(
    companies: List[str],
    use_cache: bool = True,
    max_workers: int = 2,
    sequential: bool = False,
    run_id: str = None,
) -> List[Dict[str, Any]]:
    """
    Search multiple companies in parallel or sequentially.
    
    Args:
        companies: List of company names to search.
        use_cache: Whether to use URL caching for faster lookups.
        max_workers: Number of parallel workers (default: 2).
        sequential: If True, process one at a time (slower but more reliable).
        run_id: Optional funnel logging run id.
        
    Returns:
        List of searched company records.
    """
    # Load headcount data once
    headcount_data = load_headcount_data()
    
    if sequential:
        print(f"⚡ Starting sequential search (one at a time)...")
        results = []
        
        for i, company in enumerate(companies, 1):
            print(f"  [{i}/{len(companies)}] Processing {company}...")
            try:
                result = search_company(company, use_cache, headcount_data, run_id=run_id)
                status = "✓" if not result.get("error") else "✗"
                print(f"  [{i}/{len(companies)}] {status} {company}")
                results.append(result)
            except Exception as e:
                print(f"  [{i}/{len(companies)}] ✗ {company}: {str(e)}")
                if run_id:
                    log_funnel_stage(company, run_id, "failed", failure_reason=str(e))
                results.append({
                    "company_name": company,
                    "error": str(e),
                    "lead_score": 0,
                    "status_tag": "Unknown"
                })
        
        return results
    
    # Parallel processing
    print(f"⚡ Starting parallel search with {max_workers} workers...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(search_company, company, use_cache, headcount_data, run_id): company
            for company in companies
        }
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
                if run_id:
                    log_funnel_stage(company, run_id, "failed", failure_reason=str(e))
                result = {
                    "company_name": company,
                    "error": str(e),
                    "lead_score": 0,
                    "status_tag": "Unknown"
                }
            
            results.append(result)
    
    return results


def push_batch_to_airtable(records: List[Dict[str, Any]], run_id: str = None) -> int:
    """
    Push a batch of records to Airtable.
    
    Args:
        records: List of searched company records.
        run_id: Optional funnel logging run id.
        
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
            "lead_score_confidence": record.get("lead_score_confidence", ""),
            "ai_maturity_score": record.get("ai_maturity_score"),
            "ai_maturity_reason": record.get("ai_maturity_reason", ""),
            "ai_maturity_confidence": record.get("ai_maturity_confidence", ""),
            "transformation_readiness_score": record.get(
                "transformation_readiness_score"
            ),
            "transformation_readiness_reason": record.get(
                "transformation_readiness_reason", ""
            ),
            "transformation_readiness_confidence": record.get(
                "transformation_readiness_confidence", ""
            ),
            "enterprise_readiness_tier": record.get("enterprise_readiness_tier", ""),
        }
        if run_id:
            airtable_record["_run_id"] = run_id
        
        if push_to_airtable(airtable_record):
            pushed_count += 1
    
    return pushed_count


def notify_email(searched_records: List[Dict[str, Any]]) -> None:
    """
    Send HTML email with search summary.
    
    Args:
        searched_records: List of all searched company records.
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
    total = len(searched_records)
    successful = [r for r in searched_records if r.get("error") is None]
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
            <h2>🎯 Daily Lead Search Report</h2>
            <p>Date: {today}</p>
        </div>
        
        <div class="summary">
            <h3>Summary</h3>
            <p><strong>Total Companies:</strong> {total}</p>
            <p><strong>Successfully Searched:</strong> {len(successful)}</p>
            <p>🔥 <strong>Hot Leads:</strong> {hot_count}</p>
            <p>🌟 <strong>Warm Leads:</strong> {warm_count}</p>
            <p>❄️ <strong>Cold Leads:</strong> {cold_count}</p>
        </div>
        
        <h3>Searched Companies</h3>
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
            <p>Generated by AI Sales Search Pipeline</p>
            <p>Completed: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
        </div>
    </body>
    </html>
    """
    
    # Create email message
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = f"Daily Lead Search Report — {today}"
    
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
    Run the full search pipeline.
    
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
        print("🚀 AI Sales Search Pipeline")
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

    import uuid
    run_id = f"cli-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    
    # Process all companies
    if dry_run:
        companies = companies[:2]
        print(f"\n📊 DRY RUN: Processing {len(companies)} companies sequentially...")
    else:
        print(f"\n📊 Processing {len(companies)} companies in parallel (2 workers)...")
    print("-" * 60)

    for company in companies:
        log_funnel_stage(company, run_id, "uploaded")
    
    # Use parallel processing with ThreadPoolExecutor
    # For dry-run, use sequential mode to avoid API timeouts
    start_process_time = datetime.utcnow()
    searched_records = search_companies(
        companies, max_workers=2, sequential=dry_run, run_id=run_id
    )
    process_duration = datetime.utcnow() - start_process_time
    
    print(f"\n✓ Parallel processing complete in {process_duration.total_seconds():.1f}s")
    print(f"✓ Processed {len(searched_records)} companies")
    
    # Summary stats
    successful = [r for r in searched_records if r.get("error") is None]
    failed = [r for r in searched_records if r.get("error")]
    hot_leads = [r for r in successful if r.get("status_tag") == "Hot"]
    warm_leads = [r for r in successful if r.get("status_tag") == "Warm"]
    cold_leads = [r for r in successful if r.get("status_tag") == "Cold"]
    
    print("\n" + "=" * 60)
    print("📈 Processing Summary")
    print("=" * 60)
    print(f"Total:        {len(searched_records)} companies")
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
        for record in searched_records:
            output = {
                "company_name": record.get("company_name"),
                "url": record.get("url"),
                "lead_score": record.get("lead_score"),
                "status_tag": record.get("status_tag"),
                "industry": record.get("industry"),
                "score_reason": record.get("score_reason"),
                "growth_label": record.get("growth_label"),
                "growth_rate": record.get("growth_rate"),
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
            pushed = push_batch_to_airtable(batch, run_id=run_id)
            total_pushed += pushed
            print(f"  ✓ Pushed {pushed}/{len(batch)} records")
        
        print(f"\n✓ Total pushed to Airtable: {total_pushed}/{len(successful)}")
        
        # Send email notification only if Airtable push was successful
        if total_pushed > 0:
            print("\n" + "=" * 60)
            print("📧 Sending Email Notification")
            print("=" * 60)
            notify_email(searched_records)
        else:
            print("\n" + "=" * 60)
            print("⚠️ SKIPPED: Email notification (no records pushed to Airtable)")
            print("=" * 60)
    
    # Final summary
    print("\n" + "=" * 60)
    if dry_run:
        print(f"✅ DRY RUN complete — {len(companies)} companies processed, nothing pushed")
    else:
        print("✅ Pipeline Complete!")
    print("=" * 60)
    print(f"Finished at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Sales Search Pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode: process only 2 companies, skip Airtable and email"
    )
    args = parser.parse_args()
    
    run_pipeline(dry_run=args.dry_run)