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
    page_title="AI Sales Intelligence CRM",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Modern, Attractive CSS with animations and gradients
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    * {
        font-family: 'Inter', sans-serif;
    }
    
    /* Animated gradient header */
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 16px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
        box-shadow: 0 10px 40px rgba(102, 126, 234, 0.3);
        animation: slideDown 0.8s ease-out;
    }
    
    @keyframes slideDown {
        from { opacity: 0; transform: translateY(-30px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    @keyframes pulse {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.05); }
    }
    
    .main-header h1 {
        font-size: 2.5rem;
        font-weight: 700;
        margin: 0;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
    }
    
    .main-header p {
        font-size: 1.1rem;
        opacity: 0.9;
        margin-top: 0.5rem;
    }
    
    /* Status badges */
    .status-hot {
        background: linear-gradient(135deg, #ff6b6b, #ee5a5a);
        color: white;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        display: inline-block;
    }
    
    .status-warm {
        background: linear-gradient(135deg, #feca57, #ff9f43);
        color: white;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        display: inline-block;
    }
    
    .status-cold {
        background: linear-gradient(135deg, #48dbfb, #0abde3);
        color: white;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        display: inline-block;
    }
    
    /* Modern metric cards */
    .metric-container {
        background: white;
        padding: 1.5rem;
        border-radius: 16px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border-left: 4px solid #667eea;
        transition: transform 0.3s ease, box-shadow 0.3s ease;
        animation: fadeIn 0.6s ease-out;
    }
    
    .metric-container:hover {
        transform: translateY(-5px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.12);
    }
    
    .metric-value {
        font-size: 2.5rem;
        font-weight: 700;
        color: #667eea;
        margin: 0;
    }
    
    .metric-label {
        color: #6c757d;
        font-size: 0.9rem;
        font-weight: 500;
    }
    
    /* Lead cards */
    .lead-card {
        padding: 1rem;
        border-radius: 12px;
        text-align: center;
        font-weight: 600;
        transition: all 0.3s ease;
        animation: fadeIn 0.6s ease-out;
    }
    
    .lead-card:hover {
        transform: scale(1.05);
    }
    
    .lead-hot {
        background: linear-gradient(135deg, #ff6b6b 0%, #ee5a5a 100%);
        color: white;
        box-shadow: 0 4px 15px rgba(255, 107, 107, 0.4);
    }
    
    .lead-warm {
        background: linear-gradient(135deg, #feca57 0%, #ff9f43 100%);
        color: white;
        box-shadow: 0 4px 15px rgba(254, 202, 87, 0.4);
    }
    
    .lead-cold {
        background: linear-gradient(135deg, #48dbfb 0%, #0abde3 100%);
        color: white;
        box-shadow: 0 4px 15px rgba(72, 219, 251, 0.4);
    }
    
    /* Section headers */
    .section-header {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 1.5rem;
        font-weight: 700;
        margin: 2rem 0 1rem 0;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid #e9ecef;
    }
    
    /* Upload area styling */
    .uploadedFile {
        border: 2px dashed #667eea;
        border-radius: 12px;
        padding: 2rem;
        background: #f8f9fa;
        transition: all 0.3s ease;
    }
    
    /* Progress bar styling */
    .stProgress > div > div > div {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    }
    
    /* Button styling */
    .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 12px;
        padding: 0.75rem 2rem;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
    }
    
    /* Dataframe styling */
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    }
    
    /* Success/Error message styling */
    .stSuccess {
        border-radius: 12px;
        animation: slideDown 0.5s ease-out;
    }
    
    .stError {
        border-radius: 12px;
        animation: slideDown 0.5s ease-out;
    }
    
    .stWarning {
        border-radius: 12px;
        animation: slideDown 0.5s ease-out;
    }
    
    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #667eea 0%, #764ba2 100%);
    }
    
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 2rem;
    }
    
    /* API status indicators */
    .api-connected {
        color: #00d26a;
        font-weight: 600;
    }
    
    .api-missing {
        color: #ff6b6b;
        font-weight: 600;
    }
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

    # Beautiful animated header
    st.markdown("""
        <div class="main-header">
            <h1>🚀 AI Sales Intelligence CRM</h1>
            <p>Transform prospects into qualified leads with AI-powered enrichment</p>
        </div>
    """, unsafe_allow_html=True)

    # Modern sidebar with gradient styling
    with st.sidebar:
        st.markdown("<h2 style='color: white; text-align: center;'>⚙️ Configuration</h2>", unsafe_allow_html=True)
        st.markdown("<hr style='border-color: rgba(255,255,255,0.3);'>", unsafe_allow_html=True)
        
        # API Status indicators
        st.markdown("<h3 style='color: white;'>🔌 API Status</h3>", unsafe_allow_html=True)
        
        env_status = check_env_vars()
        
        for var_name, is_set in env_status.items():
            if is_set:
                st.markdown(f"<p style='color: #00ff88; margin: 0;'>✓ {var_name}</p>", unsafe_allow_html=True)
            else:
                st.markdown(f"<p style='color: #ff6b6b; margin: 0;'>✗ {var_name}</p>", unsafe_allow_html=True)
        
        if not all(env_status.values()):
            st.warning("⚠️ Some API keys missing")
        else:
            st.success("✓ All APIs ready")
        
        st.markdown("<hr style='border-color: rgba(255,255,255,0.3);'>", unsafe_allow_html=True)

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

        st.markdown("<hr style='border-color: rgba(255,255,255,0.3);'>", unsafe_allow_html=True)
        st.markdown("<h3 style='color: white;'>📖 How It Works</h3>", unsafe_allow_html=True)
        st.markdown("""
            <div style="color: rgba(255,255,255,0.9); font-size: 0.9rem;">
                <p>1️⃣ Upload CSV with company names</p>
                <p>2️⃣ Click "Enrich All" to start</p>
                <p>3️⃣ AI analyzes each company</p>
                <p>4️⃣ Results pushed to Airtable</p>
            </div>
        """, unsafe_allow_html=True)

    # Welcome card with features
    st.markdown("""
        <div style="
            background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%);
            border: 2px solid #667eea40;
            border-radius: 16px;
            padding: 1.5rem;
            margin-bottom: 2rem;
            display: flex;
            gap: 2rem;
            flex-wrap: wrap;
        ">
            <div style="flex: 1; min-width: 200px;">
                <h4 style="color: #667eea; margin: 0 0 0.5rem 0;">🤖 AI-Powered</h4>
                <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">NVIDIA AI analyzes company websites to extract insights</p>
            </div>
            <div style="flex: 1; min-width: 200px;">
                <h4 style="color: #667eea; margin: 0 0 0.5rem 0;">🌐 Web Scraping</h4>
                <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">Firecrawl automatically finds and scrapes company websites</p>
            </div>
            <div style="flex: 1; min-width: 200px;">
                <h4 style="color: #667eea; margin: 0 0 0.5rem 0;">📊 Lead Scoring</h4>
                <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">Automatic lead scoring from 1-10 with status tags</p>
            </div>
            <div style="flex: 1; min-width: 200px;">
                <h4 style="color: #667eea; margin: 0 0 0.5rem 0;">🚀 CRM Integration</h4>
                <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">One-click push to Airtable CRM</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

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
    st.markdown('<div class="section-header">📤 Upload Companies</div>', unsafe_allow_html=True)

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
    st.markdown('<div class="section-header">🔄 Processing</div>', unsafe_allow_html=True)

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
        st.markdown('<div class="section-header">📊 Results Summary</div>', unsafe_allow_html=True)

        results = st.session_state.results

        # Calculate metrics
        total = len(results)
        successful = sum(1 for r in results if r.get("error") is None)
        failed = total - successful

        hot_count = sum(1 for r in results if r.get("status_tag") == "Hot")
        warm_count = sum(1 for r in results if r.get("status_tag") == "Warm")
        cold_count = sum(1 for r in results if r.get("status_tag") == "Cold")

        # Display metric cards with modern styling
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(f"""
                <div class="metric-container">
                    <p class="metric-label">Total Processed</p>
                    <p class="metric-value">{total}</p>
                    <p style="color: #28a745; font-size: 0.9rem;">✓ {successful} successful</p>
                </div>
            """, unsafe_allow_html=True)

        with col2:
            st.markdown(f"""
                <div class="metric-container">
                    <p class="metric-label">Successfully Enriched</p>
                    <p class="metric-value">{successful}</p>
                    <p style="color: {'#dc3545' if failed > 0 else '#28a745'}; font-size: 0.9rem;">
                        {'✗ ' + str(failed) + ' failed' if failed > 0 else '✓ All good'}
                    </p>
                </div>
            """, unsafe_allow_html=True)

        with col3:
            st.markdown(f"""
                <div class="metric-container">
                    <p class="metric-label">Failed</p>
                    <p class="metric-value" style="color: {'#dc3545' if failed > 0 else '#28a745'};">{failed}</p>
                    <p style="color: #6c757d; font-size: 0.9rem;">attempts</p>
                </div>
            """, unsafe_allow_html=True)

        # Breakdown by status with attractive cards
        st.markdown('<div class="section-header">Lead Breakdown</div>', unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(f"""
                <div class="lead-card lead-hot">
                    <div style="font-size: 2rem;">🔥</div>
                    <div style="font-size: 1.5rem;">{hot_count}</div>
                    <div>Hot Leads</div>
                    <div style="font-size: 0.8rem; opacity: 0.8;">Score 8-10</div>
                </div>
            """, unsafe_allow_html=True)

        with col2:
            st.markdown(f"""
                <div class="lead-card lead-warm">
                    <div style="font-size: 2rem;">🌟</div>
                    <div style="font-size: 1.5rem;">{warm_count}</div>
                    <div>Warm Leads</div>
                    <div style="font-size: 0.8rem; opacity: 0.8;">Score 5-7</div>
                </div>
            """, unsafe_allow_html=True)

        with col3:
            st.markdown(f"""
                <div class="lead-card lead-cold">
                    <div style="font-size: 2rem;">❄️</div>
                    <div style="font-size: 1.5rem;">{cold_count}</div>
                    <div>Cold Leads</div>
                    <div style="font-size: 0.8rem; opacity: 0.8;">Score 1-4</div>
                </div>
            """, unsafe_allow_html=True)

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
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="section-header">📤 Push to Airtable</div>', unsafe_allow_html=True)

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
