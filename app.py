"""
AI Sales Intelligence Pipeline - Streamlit Application

A production-ready web app that enriches company leads using AI and
pushes structured data to Airtable CRM.
"""

import os
from typing import Any, Dict, List

import streamlit as st
from dotenv import load_dotenv

from pipeline import (
    analyze_company,
    get_homepage_url,
    push_to_airtable,
    scrape_homepage,
)
from utils.helpers import get_status_tag, parse_csv

# Load environment variables from .env file
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="Sales Intelligence Pipeline",
    page_icon="🎯",
    layout="wide",
)

# Custom CSS for better styling
st.markdown("""
    <style>
    .stDataFrame { border-radius: 8px; }
    .metric-card { background: #f8f9fa; padding: 16px; border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)


def check_env_vars() -> Dict[str, bool]:
    """Check if all required environment variables are set."""
    return {
        "FIRECRAWL_API_KEY": bool(os.getenv("FIRECRAWL_API_KEY")),
        "NVIDIA_API_KEY": bool(os.getenv("NVIDIA_API_KEY")),
        "AIRTABLE_API_KEY": bool(os.getenv("AIRTABLE_API_KEY")),
        "AIRTABLE_BASE_ID": bool(os.getenv("AIRTABLE_BASE_ID")),
    }


def process_company(company_name: str) -> Dict[str, Any]:
    """
    Process a single company through the full enrichment pipeline.

    Args:
        company_name: Name of the company to enrich.

    Returns:
        Dictionary containing all enriched data plus original company name.
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

    # Step 1: Search for homepage URL
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

    # Step 3: Analyze with AI
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

    return result


def color_code_status(status: str) -> str:
    """Return color for status tag."""
    colors = {
        "Hot": "color: green; font-weight: bold;",
        "Warm": "color: orange; font-weight: bold;",
        "Cold": "color: red; font-weight: bold;",
        "Unknown": "color: gray;",
    }
    return colors.get(status, "")


def main():
    """Main Streamlit application."""

    # =========================================================================
    # HEADER SECTION
    # =========================================================================
    st.title("🎯 Sales Intelligence Pipeline")
    st.subheader("Upload company names → AI enriches → pushes to Airtable CRM")
    st.info("Powered by NVIDIA AI + Firecrawl + Airtable", icon="ℹ️")

    # =========================================================================
    # SIDEBAR - Configuration Check
    # =========================================================================
    with st.sidebar:
        st.header("Configuration Check")

        env_status = check_env_vars()

        for var_name, is_set in env_status.items():
            if is_set:
                st.success(f"✓ {var_name}")
            else:
                st.error(f"✗ {var_name}")

        st.divider()

        # Download sample CSV button
        sample_csv = """company_name
Siemens Energy
Vestas Wind Systems
Schneider Electric
ABB Group
Orsted
SolarEdge Technologies
Enphase Energy
NextEra Energy
Brookfield Renewable
EDF Renewables
"""
        st.download_button(
            label="📥 Download Sample CSV",
            data=sample_csv,
            file_name="sample_companies.csv",
            mime="text/csv",
        )

        st.divider()
        st.markdown("""
        ### How It Works
        1. Upload CSV with company names
        2. Click "Enrich All" to start
        3. AI analyzes each company
        4. Results pushed to Airtable
        """)

    # =========================================================================
    # CHECK FOR MISSING CONFIGURATION
    # =========================================================================
    if not all(env_status.values()):
        st.error(
            "⚠️ Missing required environment variables. "
            "Please check the sidebar and ensure all credentials are configured."
        )
        st.stop()

    # =========================================================================
    # UPLOAD SECTION
    # =========================================================================
    st.header("📤 Upload Companies")

    uploaded_file = st.file_uploader(
        "Choose a CSV file",
        type=["csv"],
        help="CSV file must have a 'company_name' column or use the first column",
    )

    if uploaded_file is None:
        st.info("Upload a CSV file to begin enrichment")
        st.stop()

    # Parse CSV
    companies = parse_csv(uploaded_file)

    if not companies:
        st.warning("No valid companies found in the uploaded file")
        st.stop()

    st.success(f"✓ {len(companies)} companies loaded")

    # Show preview
    with st.expander("📋 Preview First 5 Companies"):
        st.write(companies[:5])
        if len(companies) > 5:
            st.write(f"... and {len(companies) - 5} more")

    # =========================================================================
    # PROCESSING SECTION
    # =========================================================================
    st.header("🔄 Processing")

    # Initialize session state for results
    if "results" not in st.session_state:
        st.session_state.results = []
    if "processing_complete" not in st.session_state:
        st.session_state.processing_complete = False

    col1, col2 = st.columns([1, 3])
    with col1:
        start_button = st.button("🚀 Enrich All", type="primary", width='stretch')

    if start_button:
        # Reset state
        st.session_state.results = []
        st.session_state.processing_complete = False

        # Create progress bar and status placeholder
        progress_bar = st.progress(0)
        status_text = st.empty()
        results_container = st.container()

        # Process each company
        for i, company in enumerate(companies, 1):
            status_text.text(f"Processing: {company} ({i} of {len(companies)})")

            # Process company
            result = process_company(company)
            st.session_state.results.append(result)

            # Update progress
            progress_bar.progress(i / len(companies))

            # Show real-time results
            with results_container:
                if st.session_state.results:
                    df = st.dataframe(
                        st.session_state.results,
                        width='stretch',
                        hide_index=True,
                        column_config={
                            "company_name": "Company",
                            "url": "Website",
                            "summary": "Summary",
                            "industry": "Industry",
                            "size_estimate": "Size",
                            "b2b_buyer": "B2B Buyer",
                            "lead_score": "Score",
                            "status_tag": "Status",
                            "score_reason": "Reason",
                            "error": "Error",
                        },
                    )

        # Processing complete
        st.session_state.processing_complete = True
        status_text.text("✓ Processing complete!")
        progress_bar.empty()

        # Show errors if any
        errors = [r for r in st.session_state.results if r.get("error")]
        if errors:
            with st.expander(f"⚠️ View Errors ({len(errors)} failed)", expanded=True):
                for e in errors:
                    st.error(f"**{e['company_name']}**: {e['error']}")

    # =========================================================================
    # RESULTS SECTION (shown after processing)
    # =========================================================================
    if st.session_state.results and st.session_state.processing_complete:
        st.header("📊 Results Summary")

        results = st.session_state.results

        # Calculate metrics
        total = len(results)
        successful = sum(1 for r in results if r.get("error") is None)
        failed = total - successful

        hot_count = sum(1 for r in results if r.get("status_tag") == "Hot")
        warm_count = sum(1 for r in results if r.get("status_tag") == "Warm")
        cold_count = sum(1 for r in results if r.get("status_tag") == "Cold")

        # Display metric cards
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                label="Total Processed",
                value=total,
                delta=f"{successful} successful",
            )

        with col2:
            st.metric(
                label="Pushed to Airtable",
                value=successful,
                delta=f"{failed} failed" if failed > 0 else None,
            )

        with col3:
            st.metric(
                label="Failed",
                value=failed,
                delta=None,
            )

        # Breakdown by status
        st.subheader("Lead Breakdown")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.success(f"🔥 Hot Leads: {hot_count}")

        with col2:
            st.warning(f"🌟 Warm Leads: {warm_count}")

        with col3:
            st.error(f"❄️ Cold Leads: {cold_count}")

        # =========================================================================
        # ACTION BUTTONS
        # =========================================================================
        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            # Download results as CSV
            import pandas as pd

            df_results = pd.DataFrame(results)
            csv_data = df_results.to_csv(index=False)

            st.download_button(
                label="📥 Download Results as CSV",
                data=csv_data,
                file_name="enriched_companies.csv",
                mime="text/csv",
                width='stretch',
            )

        with col2:
            # View in Airtable button
            base_id = os.getenv("AIRTABLE_BASE_ID", "")
            airtable_url = f"https://airtable.com/{base_id}"

            st.link_button(
                label="🔗 View in Airtable",
                url=airtable_url,
                width='stretch',
            )

        # =========================================================================
        # PUSH TO AIRTABLE
        # =========================================================================
        st.divider()
        st.subheader("📤 Push to Airtable")

        # Check which records haven't been pushed yet
        unpushed = [r for r in results if r.get("error") is None]

        if unpushed:
            if st.button(f"Push {len(unpushed)} Records to Airtable", type="primary"):
                push_count = 0
                progress = st.progress(0)

                for i, record in enumerate(unpushed):
                    # Prepare record for Airtable
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
                        push_count += 1

                    progress.progress((i + 1) / len(unpushed))

                st.success(f"✓ Pushed {push_count} of {len(unpushed)} records to Airtable")


if __name__ == "__main__":
    main()
