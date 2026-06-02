"""
AI Sales Intelligence Pipeline - Streamlit Application

A production-ready web app that searches company leads using AI and
pushes structured data to Airtable CRM.
"""

import json
import os
import urllib.parse
from typing import Any, Dict, List

import requests
import streamlit as st
from dotenv import load_dotenv

from pipeline import (
    analyze_company,
    fetch_from_airtable,
    generate_icp,
    get_homepage_url,
    push_to_airtable,
    recommend_companies,
    scrape_homepage,
    search_company_info,
)
from utils.database import create_user, verify_user, user_exists
from utils.helpers import get_status_tag, parse_csv


def get_company_logo(url: str) -> str:
    """
    Get company logo URL using Clearbit API with fallback to initials.
    
    Args:
        url: Company website URL
        
    Returns:
        HTML string for logo display (either image or initials avatar)
    """
    try:
        # Extract domain from URL
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.replace('www.', '')
        
        # Get company name from domain for initials
        company_initial = domain.split('.')[0][0].upper() if domain else '?'
        
        # Try Clearbit logo API
        clearbit_url = f"https://logo.clearbit.com/{domain}?size=80"
        
        # Return HTML with Clearbit logo and fallback to initials
        return f'''
        <div style="position: relative; width: 36px; height: 36px;">
            <img src="{clearbit_url}" 
                 alt="Logo" 
                 onerror="this.style.display='none'; this.nextElementSibling.style.display='flex'" 
                 style="width: 36px; height: 36px; border-radius: 50%; object-fit: cover;">
            <div style="display: none; width: 36px; height: 36px; border-radius: 50%; background: #06B6D4; align-items: center; justify-content: center; color: #0B1220; font-weight: 700; font-size: 14px;">
                {company_initial}
            </div>
        </div>
        '''
    except:
        # Fallback to initials if URL parsing fails
        return '<div style="width: 36px; height: 36px; border-radius: 50%; background: #06B6D4; align-items: center; justify-content: center; color: #0B1220; font-weight: 700; font-size: 14px;">?</div>'


def filter_public_email(emails: str) -> str:
    """
    Filter emails to prioritize public company emails over personal ones.
    
    Args:
        emails: String of emails (comma-separated or single)
        
    Returns:
        Best public email or "Not Available"
    """
    if not emails or emails == "Not Found":
        return "Not Available"
    
    # Parse emails
    email_list = [e.strip() for e in emails.split(',') if e.strip()]
    
    # Priority order for public company emails
    priority_prefixes = ['contact@', 'sales@', 'support@', 'info@', 'hello@']
    
    # First try to find priority emails
    for prefix in priority_prefixes:
        for email in email_list:
            if email.lower().startswith(prefix):
                return email
    
    # If no priority emails found, check if any email looks like a public one
    # (not personal names like john@, jane@, etc.)
    personal_prefixes = ['john@', 'jane@', 'mike@', 'sarah@', 'david@', 'emily@', 
                         'chris@', 'alex@', 'matt@', 'jessica@', 'michael@', 
                         'lisa@', 'robert@', 'jennifer@', 'william@', 'elizabeth@']
    
    public_emails = []
    for email in email_list:
        email_lower = email.lower()
        is_personal = any(email_lower.startswith(prefix) for prefix in personal_prefixes)
        if not is_personal:
            public_emails.append(email)
    
    if public_emails:
        return public_emails[0]
    
    # If only personal emails found, return Not Available
    return "Not Available"


def validate_and_format_phone(phone: str) -> str:
    """
    Validate and format phone numbers.
    
    Args:
        phone: Phone number string
        
    Returns:
        Formatted phone number or "Not Available"
    """
    if not phone or phone == "Not Found":
        return "Not Available"
    
    # Remove all non-digit characters
    digits = ''.join(c for c in phone if c.isdigit())
    
    # Check if we have enough digits for a valid phone number
    # Minimum 7 digits, maximum 15 digits (international)
    if len(digits) < 7 or len(digits) > 15:
        return "Not Available"
    
    # Format based on length
    if len(digits) == 10:
        # US format: (XXX) XXX-XXXX
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    elif len(digits) == 11 and digits[0] == '1':
        # US with country code: +1 (XXX) XXX-XXXX
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:11]}"
    elif len(digits) > 10:
        # International format: +XXX XXX XXX XXX
        # Split into groups
        parts = []
        for i in range(0, len(digits), 3):
            parts.append(digits[i:i+3])
        return f"+{'+'.join(parts)}"
    else:
        # Shorter numbers, just format with spaces
        if len(digits) == 7:
            return f"{digits[0:3]}-{digits[3:7]}"
        elif len(digits) == 8:
            return f"{digits[0:4]}-{digits[4:8]}"
        else:
            return "Not Available"


def extract_contact_fallback(text: str, url: str, company_name: str) -> Dict[str, str]:
    """
    Extract contact information using regex patterns as fallback.
    
    Args:
        text: Homepage text to search
        url: Company website URL
        company_name: Company name
        
    Returns:
        Dictionary with extracted contact information
    """
    import re
    from urllib.parse import urlparse, urljoin
    
    result = {
        "phone": "",
        "email": "",
        "linkedin": "",
        "contact_page": "",
    }
    
    # Extract phone numbers (various formats)
    phone_patterns = [
        r'\+?[\d\s\-\(\)]{10,}',  # International format
        r'\(\d{3}\)\s*\d{3}[-\s]?\d{4}',  # US format
        r'\d{3}[-\s]?\d{3}[-\s]?\d{4}',  # Simple format
    ]
    for pattern in phone_patterns:
        phones = re.findall(pattern, text)
        if phones:
            result["phone"] = phones[0].strip()
            break
    
    # Extract email addresses
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(email_pattern, text)
    if emails:
        # Prefer contact, info, or support emails
        preferred_emails = [e for e in emails if any(pref in e.lower() for pref in ['contact', 'info', 'support', 'sales'])]
        result["email"] = preferred_emails[0] if preferred_emails else emails[0]
    
    # Extract LinkedIn URLs
    linkedin_pattern = r'https?://(?:www\.)?linkedin\.com/company/[\w-]+'
    linkedins = re.findall(linkedin_pattern, text)
    if linkedins:
        result["linkedin"] = linkedins[0]
    else:
        # Try to construct LinkedIn URL from company name
        company_slug = company_name.lower().replace(' ', '-').replace('.', '').replace(',', '')
        result["linkedin"] = f"https://www.linkedin.com/company/{company_slug}"
    
    # Extract contact page URLs
    contact_patterns = [
        r'href=["\']([^"\']*(?:contact|contact-us|get-in-touch|reach-us)[^"\']*)["\']',
        r'href=["\']([^"\']*/contact[^"\']*)["\']',
    ]
    for pattern in contact_patterns:
        contact_urls = re.findall(pattern, text, re.IGNORECASE)
        if contact_urls:
            contact_url = contact_urls[0]
            if contact_url.startswith('/'):
                base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                result["contact_page"] = urljoin(base_url, contact_url)
            else:
                result["contact_page"] = contact_url
            break
    
    # Fallback to common contact page paths
    if not result["contact_page"]:
        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        common_paths = ['/contact', '/contact-us', '/contactus', '/get-in-touch']
        for path in common_paths:
            result["contact_page"] = urljoin(base_url, path)
            break
    
    return result


def generate_lead_explanation(result: Dict[str, Any]) -> str:
    """
    Generate dynamic explanation of how the lead was evaluated based on actual scoring logic.
    
    Args:
        result: Lead result dictionary
        
    Returns:
        Human-readable explanation string
    """
    score = result.get("lead_score", 0)
    industry = result.get("industry", "")
    size = result.get("size_estimate", "")
    b2b = result.get("b2b_buyer", False)
    growth_label = result.get("Growth Label", "")
    
    # Build explanation based on actual scoring criteria
    factors = []
    
    # Industry factor
    if industry in ["Energy", "Technology", "Manufacturing"]:
        factors.append(f"operates in the high-priority {industry} sector")
    elif industry and industry != "Other":
        factors.append(f"operates in the {industry} industry")
    
    # Size factor
    if "200+" in size:
        factors.append("has a large employee base (200+ employees)")
    elif "51-200" in size:
        factors.append("has a medium-to-large employee base (51-200 employees)")
    elif size and size != "1-10 employees":
        factors.append(f"has a {size}")
    
    # B2B factor
    if b2b:
        factors.append("demonstrates strong B2B software purchase potential")
    
    # Growth factor
    if growth_label == "Rapid growth":
        factors.append("shows rapid LinkedIn headcount growth")
    elif growth_label == "Growing":
        factors.append("shows positive headcount growth")
    
    # Generate explanation
    if factors:
        factor_text = ", ".join(factors[:-1]) + ", and " + factors[-1] if len(factors) > 1 else factors[0]
        explanation = f"This company received a score of {score}/10 because it {factor_text}."
    else:
        explanation = f"This company received a score of {score}/10 based on available company data."
    
    return explanation


def get_lead_qualification_breakdown(result: Dict[str, Any]) -> Dict[str, str]:
    """
    Generate lead qualification breakdown based on actual data.
    
    Args:
        result: Lead result dictionary
        
    Returns:
        Dictionary with qualification factors and their status
    """
    industry = result.get("industry", "")
    size = result.get("size_estimate", "")
    b2b = result.get("b2b_buyer", False)
    growth_label = result.get("Growth Label", "")
    
    breakdown = {}
    
    # Industry match
    if industry in ["Energy", "Technology", "Manufacturing"]:
        breakdown["Industry Match"] = "✓ Strong Match"
    elif industry and industry != "Other":
        breakdown["Industry Match"] = "✓ Good Match"
    else:
        breakdown["Industry Match"] = "○ Standard"
    
    # Company size
    if "200+" in size:
        breakdown["Company Size"] = "✓ Large Organization"
    elif "51-200" in size:
        breakdown["Company Size"] = "✓ Medium Organization"
    elif size:
        breakdown["Company Size"] = "○ Small Organization"
    else:
        breakdown["Company Size"] = "○ Unknown"
    
    # B2B potential
    if b2b:
        breakdown["B2B Potential"] = "✓ High"
    else:
        breakdown["B2B Potential"] = "○ Low"
    
    # Growth trend
    if growth_label == "Rapid growth":
        breakdown["Growth Trend"] = "✓ Rapid Growth"
    elif growth_label == "Growing":
        breakdown["Growth Trend"] = "✓ Growing"
    elif growth_label == "Stable":
        breakdown["Growth Trend"] = "○ Stable"
    else:
        breakdown["Growth Trend"] = "○ Unknown"
    
    return breakdown

# Load environment variables from .env file
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="AI Sales Intelligence CRM",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Modern AI SaaS Design System - Premium Dark Theme
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    
    * {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    
    /* Global Styles */
    .stApp {
        background-color: #0A0F1C;
    }
    
    /* Main Header - Clean Modern Design */
    .main-header {
        background: #111827;
        border: 1px solid #2D3748;
        border-radius: 16px;
        padding: 2rem 2.5rem;
        text-align: center;
        color: white;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.3);
        margin-bottom: 2rem;
    }
    
    .main-header h1 {
        font-size: 2rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.02em;
        color: #FFFFFF;
    }
    
    .main-header p {
        font-size: 1rem;
        color: #94A3B8;
        margin-top: 0.75rem;
        margin-bottom: 0;
        font-weight: 400;
    }
    
    /* Glass Cards - Subtle Dark Theme */
    .glass-card {
        background: #111827;
        border: 1px solid #2D3748;
        border-radius: 16px;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.2);
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }
    
    .glass-card:hover {
        transform: translateY(-1px);
        border-color: #3B82F6;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
    }
    
    /* Metric Cards - Clean Design */
    .metric-container {
        background: #111827;
        border: 1px solid #2D3748;
        border-radius: 16px;
        padding: 1.5rem;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.2);
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }
    
    .metric-container:hover {
        transform: translateY(-1px);
        border-color: #3B82F6;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
    }
    
    .metric-value {
        font-size: 2.5rem;
        font-weight: 700;
        color: #00D4FF;
        margin: 0;
    }
    
    .metric-label {
        color: #94A3B8;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-top: 0.5rem;
    }
    
    /* Infographic Sections - Compact Modern Cards */
    .infographic-container {
        display: flex;
        gap: 1rem;
        margin: 1.5rem 0;
    }
    
    .infographic-section {
        flex: 1;
        padding: 1rem;
        border-radius: 16px;
        background: #111827;
        border: 1px solid #2D3748;
        transition: all 0.2s ease;
        min-height: 85px;
    }
    
    .infographic-section:hover {
        transform: translateY(-1px);
        border-color: #3B82F6;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
    }
    
    .section-title {
        font-size: 0.85rem;
        font-weight: 700;
        margin-bottom: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: #FFFFFF;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    .section-content {
        list-style: none;
        padding: 0;
        margin: 0;
    }
    
    .section-content li {
        margin-bottom: 0.5rem;
        padding-left: 1rem;
        position: relative;
        font-size: 0.8rem;
        line-height: 1.4;
        color: #94A3B8;
    }
    
    .section-content li::before {
        content: '';
        position: absolute;
        left: 0;
        top: 6px;
        width: 4px;
        height: 4px;
        border-radius: 50%;
        background: #00D4FF;
    }
    
    .highlight-text {
        font-weight: 600;
        color: #00D4FF;
    }
    
    @media (max-width: 768px) {
        .infographic-container {
            flex-direction: column;
        }
    }
    
    /* Lead Cards - Status Colors */
    .lead-card {
        padding: 1rem;
        border-radius: 8px;
        text-align: center;
        font-weight: 600;
        transition: all 0.2s ease;
        background: #111827;
        border: 1px solid #2D3748;
    }
    
    .lead-card:hover {
        transform: translateY(-1px);
        border-color: #3B82F6;
    }
    
    .lead-hot {
        background: #EF4444;
        color: white;
        border: none;
    }
    
    .lead-warm {
        background: #F59E0B;
        color: white;
        border: none;
    }
    
    .lead-cold {
        background: #3B82F6;
        color: white;
        border: none;
    }
    
    /* Buttons - Primary Accent, Dark Outline Secondary */
    .stButton > button {
        background: #00D4FF;
        color: #0A0F1C;
        border: none;
        border-radius: 8px;
        padding: 0.75rem 1.5rem;
        font-weight: 600;
        font-size: 0.9rem;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 2px 8px rgba(0, 212, 255, 0.2);
    }
    
    .stButton > button:hover {
        background: #00B8E6;
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0, 212, 255, 0.3);
    }
    
    .stButton > button[kind="secondary"] {
        background: transparent;
        color: #FFFFFF;
        border: 1px solid #2D3748;
        box-shadow: none;
    }
    
    .stButton > button[kind="secondary"]:hover {
        background: #1F2937;
        border-color: #3B82F6;
    }
    
    /* Upload Area */
    .uploadedFile {
        border: 2px dashed #2D3748;
        border-radius: 16px;
        padding: 2rem;
        background: #111827;
        transition: all 0.2s ease;
    }
    
    .uploadedFile:hover {
        border-color: #00D4FF;
        background: #1F2937;
    }
    
    /* Progress Bar */
    .stProgress > div > div > div {
        background: #00D4FF;
        border-radius: 6px;
    }
    
    /* Section Headers */
    .section-header {
        font-size: 1.5rem;
        font-weight: 700;
        margin-bottom: 1.5rem;
        color: #FFFFFF;
        letter-spacing: -0.01em;
    }
    
    /* Dataframe */
    .stDataFrame {
        background: #111827;
        border-radius: 16px;
        border: 1px solid #2D3748;
        overflow: hidden;
    }
    
    /* Status Pills */
    .status-pill {
        display: inline-block;
        padding: 0.3rem 0.75rem;
        border-radius: 16px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.25px;
    }
    
    .status-hot {
        background: #EF4444;
        color: white;
    }
    
    .status-warm {
        background: #F59E0B;
        color: white;
    }
    
    .status-cold {
        background: #3B82F6;
        color: white;
    }
    
    /* Industry Badges */
    .industry-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 6px;
        font-size: 0.7rem;
        font-weight: 600;
        background: #1F2937;
        color: #94A3B8;
        border: 1px solid #2D3748;
    }
    
    /* Score Badge */
    .score-badge {
        display: inline-block;
        padding: 0.3rem 0.75rem;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: 700;
        background: #00D4FF;
        color: #0A0F1C;
    }
    
    /* Not Available Badge */
    .not-available-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        background: #1F2937;
        color: #64748B;
        border: 1px solid #2D3748;
    }
    
    /* Empty States */
    .empty-state {
        text-align: center;
        padding: 3rem 2rem;
        background: #111827;
        border-radius: 16px;
        border: 1px solid #2D3748;
    }
    
    .empty-state-icon {
        font-size: 3rem;
        margin-bottom: 1rem;
        opacity: 0.5;
    }
    
    .empty-state-text {
        color: #FFFFFF;
        font-size: 1rem;
        margin-bottom: 0.5rem;
        font-weight: 600;
    }
    
    .empty-state-subtext {
        color: #94A3B8;
        font-size: 0.85rem;
    }
    
    /* Contact Intelligence Panel */
    .contact-intelligence-card {
        background: #111827;
        border-radius: 8px;
        padding: 0.75rem;
        margin-bottom: 0.5rem;
        border: 1px solid #334155;
        transition: all 0.2s ease;
    }
    
    .contact-intelligence-card:hover {
        border-color: #475569;
        transform: translateX(2px);
    }
    
    .contact-icon {
        font-size: 1rem;
        margin-right: 0.5rem;
    }
    
    .contact-label {
        font-size: 0.7rem;
        color: #94A3B8;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.25px;
    }
    
    .contact-value {
        color: #FFFFFF;
        font-weight: 600;
        font-size: 0.85rem;
    }
    
    .contact-value a {
        color: #06B6D4;
        text-decoration: none;
        transition: color 0.2s ease;
    }
    
    .contact-value a:hover {
        color: #0891B2;
    }
    
    /* Feature Cards */
    .feature-card {
        background: #111827;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 2rem;
        margin-bottom: 1.5rem;
        transition: all 0.3s ease;
    }
    
    .feature-card:hover {
        transform: translateY(-4px);
        border-color: #475569;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
    }
    
    /* Sidebar - Minimal Dark Theme */
    [data-testid="stSidebar"] {
        background: #0B1220;
        border-right: 1px solid #1F2937;
    }
    
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 1.5rem;
    }
    
    /* Success/Error message styling */
    .stSuccess {
        border-radius: 12px;
    }
    
    .stError {
        border-radius: 12px;
    }
    
    .stWarning {
        border-radius: 12px;
    }
    
    /* API status indicators */
    .api-connected {
        color: #22C55E;
        font-weight: 600;
    }
    
    .api-missing {
        color: #EF4444;
        font-weight: 600;
    }
    
    /* AI Brain Animation */
    @keyframes pulse-glow {
        0%, 100% {
            box-shadow: 0 0 15px rgba(6, 182, 212, 0.3);
        }
        50% {
            box-shadow: 0 0 25px rgba(6, 182, 212, 0.5);
        }
    }
    
    .ai-brain-icon {
        font-size: 2rem;
        animation: pulse-glow 2s ease-in-out infinite;
    }
    </style>
""", unsafe_allow_html=True)

# Add Apple-style parallax JavaScript
st.markdown("""
    <script>
    // Apple-style smooth parallax scrolling
    document.addEventListener('DOMContentLoaded', function() {
        const cards = document.querySelectorAll('.glass-card, .metric-container, .lead-card, .feature-card');
        
        // Intersection Observer for 3D entrance animations
        const observerOptions = {
            threshold: 0.1,
            rootMargin: '0px 0px -50px 0px'
        };
        
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.style.opacity = '1';
                    entry.target.style.transform = 'translateY(0) translateZ(0)';
                }
            });
        }, observerOptions);
        
        cards.forEach(card => {
            card.style.opacity = '0';
            card.style.transform = 'translateY(30px) translateZ(-50px)';
            card.style.transition = 'opacity 0.6s cubic-bezier(0.23, 1, 0.32, 1), transform 0.6s cubic-bezier(0.23, 1, 0.32, 1)';
            observer.observe(card);
        });
        
        // Mouse parallax effect for header
        const header = document.querySelector('.main-header');
        if (header) {
            document.addEventListener('mousemove', (e) => {
                const x = (window.innerWidth / 2 - e.pageX) / 50;
                const y = (window.innerHeight / 2 - e.pageY) / 50;
                header.style.transform = `translateZ(20px) rotateX(${y}deg) rotateY(${x}deg)`;
            });
        }
    });
    </script>
""", unsafe_allow_html=True)


def check_env_vars() -> Dict[str, bool]:
    """Check if all required environment variables are set."""
    return {
        "FIRECRAWL_API_KEY": bool(os.getenv("FIRECRAWL_API_KEY")),
        "NVIDIA_API_KEY": bool(os.getenv("NVIDIA_API_KEY")),
        "AIRTABLE_API_KEY": bool(os.getenv("AIRTABLE_API_KEY")),
        "AIRTABLE_BASE_ID": bool(os.getenv("AIRTABLE_BASE_ID")),
    }


def process_company(company_name: str, quick_mode: bool = False) -> Dict[str, Any]:
    """
    Process a single company through the full search pipeline.

    Args:
        company_name: Name of the company to search.
        quick_mode: Use faster AI analysis with reduced context for quicker results.

    Returns:
        Dictionary containing all searched data plus original company name.
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
        "Headcount W1": 0,
        "Headcount W4": 0,
        "Growth Rate %": 0,
        "Growth Label": "No data",
        "headquarters": "",
        "country": "",
        "phone": "",
        "email": "",
        "linkedin": "",
        "contact_page": "",
        "contact_reason": "",
    }

    # Step 1: Search for homepage URL and get search context
    url, search_context = search_company_info(company_name)
    if not url:
        result["error"] = "Website not found"
        return result

    result["url"] = url

    # Step 2: Scrape homepage content, fall back to search summary if it fails
    homepage_text = scrape_homepage(url)
    if not homepage_text:
        if search_context:
            homepage_text = f"[Scraping failed. Using search results fallback]\n\n{search_context}"
        else:
            result["error"] = "Failed to scrape website"
            return result

    # Step 3: Analyze with AI
    # QUICK MODE: Send less text to AI for faster response
    if quick_mode:
        # Only send first 1500 chars for quick analysis (~3x faster)
        text_for_ai = homepage_text[:1500]
    else:
        # Full mode: send up to 3000 chars for complete analysis
        text_for_ai = homepage_text[:3000]

    analysis = analyze_company(company_name, text_for_ai)

    result.update({
        "summary": analysis.get("summary", ""),
        "industry": analysis.get("industry", ""),
        "size_estimate": analysis.get("size_estimate", ""),
        "b2b_buyer": analysis.get("b2b_buyer", False),
        "lead_score": analysis.get("lead_score", 0),
        "score_reason": analysis.get("score_reason", ""),
        "headquarters": analysis.get("headquarters", ""),
        "country": analysis.get("country", ""),
        "phone": analysis.get("phone", ""),
        "email": analysis.get("email", ""),
        "linkedin": analysis.get("linkedin", ""),
        "contact_page": analysis.get("contact_page", ""),
        "contact_reason": analysis.get("contact_reason", ""),
    })

    # Step 4: Use fallback contact extraction if AI didn't find contact info
    if not result.get("phone") or not result.get("email") or not result.get("linkedin") or not result.get("contact_page"):
        fallback_contacts = extract_contact_fallback(homepage_text, url, company_name)
        if not result.get("phone") and fallback_contacts.get("phone"):
            result["phone"] = fallback_contacts["phone"]
        if not result.get("email") and fallback_contacts.get("email"):
            result["email"] = fallback_contacts["email"]
        if not result.get("linkedin") and fallback_contacts.get("linkedin"):
            result["linkedin"] = fallback_contacts["linkedin"]
        if not result.get("contact_page") and fallback_contacts.get("contact_page"):
            result["contact_page"] = fallback_contacts["contact_page"]

    # Step 5: Determine status tag
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


def init_app_session():
    """Initialize app session state."""
    if "page" not in st.session_state:
        st.session_state.page = "landing"  # landing, main, or view_airtable


def show_airtable_data_page():
    """Display all records from Airtable in a table."""
    st.markdown("""
        <div class="perspective-container">
            <div class="main-header">
                <h1>📊 Airtable Data</h1>
                <p>View all records stored in your Airtable CRM</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Fetch data from Airtable
    with st.spinner("Fetching data from Airtable..."):
        records = fetch_from_airtable()

    if not records:
        st.warning("No records found in Airtable. Upload and process companies first.")
        return

    # Display summary stats
    st.markdown('<div class="section-header">📈 Summary</div>', unsafe_allow_html=True)

    total = len(records)
    hot_count = sum(1 for r in records if r.get("Status") == "Hot")
    warm_count = sum(1 for r in records if r.get("Status") == "Warm")
    cold_count = sum(1 for r in records if r.get("Status") == "Cold")

    st.markdown(f"""
        <div style="display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap;">
            <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                <div style="font-size: 2rem; font-weight: 700; color: #06B6D4;">{total}</div>
                <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Total</div>
            </div>
            <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                <div style="font-size: 2rem; font-weight: 700; color: #EF4444;">🔥 {hot_count}</div>
                <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Hot</div>
            </div>
            <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                <div style="font-size: 2rem; font-weight: 700; color: #F59E0B;">🌟 {warm_count}</div>
                <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Warm</div>
            </div>
            <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                <div style="font-size: 2rem; font-weight: 700; color: #3B82F6;">❄️ {cold_count}</div>
                <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Cold</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Display data table
    st.markdown('<div class="section-header">📋 All Records</div>', unsafe_allow_html=True)

    import pandas as pd

    # Prepare data for display
    table_data = []
    for r in records:
        status = r.get("Status", "Unknown")
        status_icon = "🔥" if status == "Hot" else "🌟" if status == "Warm" else "❄️" if status == "Cold" else "⚪"
        b2b = r.get("B2B Buyer", False)
        b2b_display = "✅ Yes" if b2b else "❌ No"

        table_data.append({
            "Company": r.get("Company Name", ""),
            "Website": r.get("Website", ""),
            "Industry": r.get("Industry", ""),
            "Size": r.get("Size", ""),
            "B2B": b2b_display,
            "Score": r.get("Lead Score", 0),
            "Status": f"{status_icon} {status}",
            "Reason": r.get("Score Reason", "")[:80] + "..." if len(r.get("Score Reason", "")) > 80 else r.get("Score Reason", ""),
            "Headcount W1": r.get("Headcount W1", 0),
            "Headcount W4": r.get("Headcount W4", 0),
            "Growth %": r.get("Growth Rate %", 0),
            "Growth": r.get("Growth Label", ""),
            "Enriched At": r.get("Enriched At", ""),
        })

    df_display = pd.DataFrame(table_data)

    st.dataframe(
        df_display,
        width='stretch',
        hide_index=True,
        column_config={
            "Company": st.column_config.TextColumn("Company", width="medium"),
            "Website": st.column_config.LinkColumn("Website", width="medium"),
            "Industry": st.column_config.TextColumn("Industry", width="small"),
            "Size": st.column_config.TextColumn("Size", width="small"),
            "B2B": st.column_config.TextColumn("B2B", width="small"),
            "Score": st.column_config.NumberColumn("Score", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Reason": st.column_config.TextColumn("Reason", width="large"),
            "Headcount W1": st.column_config.NumberColumn("HC W1", width="small"),
            "Headcount W4": st.column_config.NumberColumn("HC W4", width="small"),
            "Growth %": st.column_config.NumberColumn("Growth %", width="small"),
            "Growth": st.column_config.TextColumn("Growth", width="small"),
            "Enriched At": st.column_config.TextColumn("Enriched At", width="medium"),
        }
    )

    # Download button
    df_results = pd.DataFrame(records)
    csv_data = df_results.to_csv(index=False)

    st.download_button(
        label="📥 Download Airtable Data as CSV",
        data=csv_data,
        file_name="airtable_data.csv",
        mime="text/csv",
    )


def show_landing_page():
    """Display landing page with problem, solution, and future plans."""
    st.markdown("""
        <div class="perspective-container">
            <div class="main-header">
                <h1>🧠 AI Sales Intelligence Platform</h1>
                <p>AI-powered lead qualification and prospect discovery for modern sales teams</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Infographic Sections - Landing Page
    st.markdown("""
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 1.5rem 0;">
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.25rem; transition: all 0.2s ease;">
                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem;">
                    <span style="font-size: 1.25rem;">⚠️</span>
                    <h3 style="color: #FFFFFF; margin: 0; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px;">Problem</h3>
                </div>
                <ul style="list-style: none; padding: 0; margin: 0;">
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        <span style="color: #06B6D4; font-weight: 600;">80%</span> of time on research vs selling
                    </li>
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Spreadsheets, browser tabs, manual work
                    </li>
                    <li style="padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        <span style="color: #06B6D4; font-weight: 600;">2+ hours</span> daily per sales rep
                    </li>
                </ul>
            </div>
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.25rem; transition: all 0.2s ease;">
                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem;">
                    <span style="font-size: 1.25rem;">✨</span>
                    <h3 style="color: #FFFFFF; margin: 0; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px;">Solution</h3>
                </div>
                <ul style="list-style: none; padding: 0; margin: 0;">
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Upload CSV → AI in <span style="color: #06B6D4; font-weight: 600;">90s</span> → Scores
                    </li>
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Transparent reasoning for decisions
                    </li>
                    <li style="padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        One-click export to Airtable CRM
                    </li>
                </ul>
            </div>
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.25rem; transition: all 0.2s ease;">
                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem;">
                    <span style="font-size: 1.25rem;">🚀</span>
                    <h3 style="color: #FFFFFF; margin: 0; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px;">Future Vision</h3>
                </div>
                <ul style="list-style: none; padding: 0; margin: 0;">
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Claude agents for real-time insights
                    </li>
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Continuous learning from feedback
                    </li>
                    <li style="padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Autonomous research & mapping
                    </li>
                </ul>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Launch button centered
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚀 Launch App", type="primary", use_container_width=True):
            st.session_state.page = "main"
            st.rerun()


def main():
    """Main Streamlit application."""

    # Initialize app session
    init_app_session()

    # Show landing page first
    if st.session_state.page == "landing":
        show_landing_page()
        return

    # Show airtable data page
    if st.session_state.page == "view_airtable":
        show_airtable_data_page()

        # Add navigation buttons
        st.divider()
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("🏠 Back to Main", type="secondary", use_container_width=True):
                st.session_state.page = "main"
                st.rerun()
        return
    
    # Show main app
    # Compact dashboard header with KPI cards
    st.markdown("""
        <div class="perspective-container">
            <div class="main-header">
                <h1>AI Sales Intelligence Platform</h1>
                <p>AI-powered lead qualification and prospect discovery</p>
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    # KPI Cards (shown when results exist)
    if st.session_state.get("results"):
        results = st.session_state.results
        total = len(results)
        successful = sum(1 for r in results if r.get("error") is None)
        hot_count = sum(1 for r in results if r.get("status_tag") == "Hot")
        avg_score = sum(r.get("lead_score", 0) for r in results) / total if total > 0 else 0
        
        st.markdown(f"""
            <div style="display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap;">
                <div class="metric-container" style="flex: 1; min-width: 120px;">
                    <div class="metric-value">{total}</div>
                    <div class="metric-label">Companies</div>
                </div>
                <div class="metric-container" style="flex: 1; min-width: 120px;">
                    <div class="metric-value">{successful}</div>
                    <div class="metric-label">Processed</div>
                </div>
                <div class="metric-container" style="flex: 1; min-width: 120px;">
                    <div class="metric-value">{hot_count}</div>
                    <div class="metric-label">Hot Leads</div>
                </div>
                <div class="metric-container" style="flex: 1; min-width: 120px;">
                    <div class="metric-value">{avg_score:.1f}</div>
                    <div class="metric-label">Avg Score</div>
                </div>
            </div>
        """, unsafe_allow_html=True)

    # Problem/Solution/Future - Compact Modern Cards
    st.markdown("""
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 1.5rem 0;">
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.25rem; transition: all 0.2s ease;">
                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem;">
                    <span style="font-size: 1.25rem;">⚠️</span>
                    <h3 style="color: #FFFFFF; margin: 0; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px;">Problem</h3>
                </div>
                <ul style="list-style: none; padding: 0; margin: 0;">
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        <span style="color: #06B6D4; font-weight: 600;">80%</span> of time on research vs selling
                    </li>
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Spreadsheets, browser tabs, manual work
                    </li>
                    <li style="padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        <span style="color: #06B6D4; font-weight: 600;">2+ hours</span> daily per sales rep
                    </li>
                </ul>
            </div>
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.25rem; transition: all 0.2s ease;">
                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem;">
                    <span style="font-size: 1.25rem;">✨</span>
                    <h3 style="color: #FFFFFF; margin: 0; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px;">Solution</h3>
                </div>
                <ul style="list-style: none; padding: 0; margin: 0;">
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Upload CSV → AI in <span style="color: #06B6D4; font-weight: 600;">90s</span> → Scores
                    </li>
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Transparent reasoning for decisions
                    </li>
                    <li style="padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        One-click export to Airtable CRM
                    </li>
                </ul>
            </div>
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.25rem; transition: all 0.2s ease;">
                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem;">
                    <span style="font-size: 1.25rem;">🚀</span>
                    <h3 style="color: #FFFFFF; margin: 0; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px;">Future Vision</h3>
                </div>
                <ul style="list-style: none; padding: 0; margin: 0;">
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Claude agents for real-time insights
                    </li>
                    <li style="margin-bottom: 0.75rem; padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Continuous learning from feedback
                    </li>
                    <li style="padding-left: 1rem; position: relative; font-size: 0.8rem; line-height: 1.5; color: #94A3B8;">
                        <span style="position: absolute; left: 0; top: 6px; width: 4px; height: 4px; border-radius: 50%; background: #06B6D4;"></span>
                        Autonomous research & mapping
                    </li>
                </ul>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Minimal Sidebar - Clean Dark Theme
    with st.sidebar:
        # Compact User Profile
        st.markdown("""
            <div style="padding: 0.5rem 0 1.5rem 0; border-bottom: 1px solid #1F2937; margin-bottom: 1.5rem;">
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                    <div style="width: 32px; height: 32px; border-radius: 50%; background: #06B6D4; display: flex; align-items: center; justify-content: center; font-size: 0.9rem; color: #0B1220; font-weight: 700;">
                        S
                    </div>
                    <div>
                        <div style="color: #FFFFFF; font-weight: 600; font-size: 0.85rem;">Sales User</div>
                        <div style="color: #94A3B8; font-size: 0.7rem;">VP Operations</div>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
        
        # API Health Toggle
        if st.button("API Health", use_container_width=True):
            st.session_state.show_api_health = not st.session_state.get('show_api_health', False)
        
        # API Health Panel
        if st.session_state.get('show_api_health', False):
            st.markdown("""
                <div style="background: #111827; border-radius: 8px; padding: 0.75rem; margin-bottom: 1rem; border: 1px solid #334155;">
                    <div style="color: #FFFFFF; font-weight: 600; font-size: 0.8rem; margin-bottom: 0.75rem;">Service Status</div>
            """, unsafe_allow_html=True)
            
            env_status = check_env_vars()
            
            api_services = [
                ("Firecrawl", env_status["FIRECRAWL_API_KEY"]),
                ("NVIDIA", env_status["NVIDIA_API_KEY"]),
                ("Airtable", env_status["AIRTABLE_API_KEY"]),
                ("Base ID", env_status["AIRTABLE_BASE_ID"]),
            ]
            
            for service_name, is_connected in api_services:
                if is_connected:
                    st.markdown(f"""
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem;">
                            <div style="display: flex; align-items: center; gap: 0.5rem;">
                                <div style="width: 4px; height: 4px; border-radius: 50%; background: #22C55E;"></div>
                                <div style="color: #94A3B8; font-size: 0.75rem; font-weight: 500;">{service_name}</div>
                            </div>
                            <div style="color: #22C55E; font-size: 0.65rem; font-weight: 600;">Connected</div>
                        </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem;">
                            <div style="display: flex; align-items: center; gap: 0.5rem;">
                                <div style="width: 4px; height: 4px; border-radius: 50%; background: #EF4444;"></div>
                                <div style="color: #94A3B8; font-size: 0.75rem; font-weight: 500;">{service_name}</div>
                            </div>
                            <div style="color: #EF4444; font-size: 0.65rem; font-weight: 600;">Missing</div>
                        </div>
                    """, unsafe_allow_html=True)
            
            st.markdown("</div>", unsafe_allow_html=True)
        
        st.markdown("<div style='height: 1px; background: #1F2937; margin: 1rem 0;'></div>", unsafe_allow_html=True)
        
        # Configuration
        st.markdown("""
            <div style="color: #FFFFFF; font-weight: 600; font-size: 0.8rem; margin-bottom: 0.75rem;">Configuration</div>
        """, unsafe_allow_html=True)

        # View Airtable Data button
        if st.button("View Airtable Data", use_container_width=True):
            st.session_state.page = "view_airtable"
            st.rerun()

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
            label="Download Sample CSV",
            data=sample_csv,
            file_name="sample_companies.csv",
            mime="text/csv",
        )

        st.markdown("<div style='height: 1px; background: #1F2937; margin: 1rem 0;'></div>", unsafe_allow_html=True)
        
        # Quick Start Guide
        st.markdown("""
            <div style="color: #94A3B8; font-size: 0.75rem; line-height: 1.8;">
                <div style="margin-bottom: 0.5rem;"><span style="color: #06B6D4; font-weight: 600;">1.</span> Upload CSV with companies</div>
                <div style="margin-bottom: 0.5rem;"><span style="color: #06B6D4; font-weight: 600;">2.</span> Click "Search All" to start</div>
                <div style="margin-bottom: 0.5rem;"><span style="color: #06B6D4; font-weight: 600;">3.</span> AI analyzes each company</div>
                <div><span style="color: #06B6D4; font-weight: 600;">4.</span> Results pushed to Airtable</div>
            </div>
        """, unsafe_allow_html=True)

    # Workflow Section - 4 Modern Cards
    st.markdown("""
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin: 2rem 0;">
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; transition: all 0.2s ease;">
                <div style="font-size: 2rem; margin-bottom: 0.75rem;">🤖</div>
                <h4 style="color: #FFFFFF; margin: 0 0 0.5rem 0; font-weight: 700; font-size: 0.95rem;">AI Analysis</h4>
                <p style="color: #94A3B8; margin: 0; font-size: 0.8rem; line-height: 1.5;">NVIDIA AI extracts insights from company data</p>
            </div>
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; transition: all 0.2s ease;">
                <div style="font-size: 2rem; margin-bottom: 0.75rem;">🌐</div>
                <h4 style="color: #FFFFFF; margin: 0 0 0.5rem 0; font-weight: 700; font-size: 0.95rem;">Web Research</h4>
                <p style="color: #94A3B8; margin: 0; font-size: 0.8rem; line-height: 1.5;">Firecrawl scrapes company websites automatically</p>
            </div>
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; transition: all 0.2s ease;">
                <div style="font-size: 2rem; margin-bottom: 0.75rem;">📊</div>
                <h4 style="color: #FFFFFF; margin: 0 0 0.5rem 0; font-weight: 700; font-size: 0.95rem;">Lead Qualification</h4>
                <p style="color: #94A3B8; margin: 0; font-size: 0.8rem; line-height: 1.5;">Automatic scoring with Hot/Warm/Cold tags</p>
            </div>
            <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; transition: all 0.2s ease;">
                <div style="font-size: 2rem; margin-bottom: 0.75rem;">🚀</div>
                <h4 style="color: #FFFFFF; margin: 0 0 0.5rem 0; font-weight: 700; font-size: 0.95rem;">CRM Export</h4>
                <p style="color: #94A3B8; margin: 0; font-size: 0.8rem; line-height: 1.5;">One-click push to Airtable CRM</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # =========================================================================
    # CHECK FOR MISSING CONFIGURATION
    # =========================================================================
    env_status = check_env_vars()
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

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        uploaded_file = st.file_uploader(
            "Choose a CSV file",
            type=["csv"],
            help="CSV file must have a 'company_name' column or use the first column",
        )

    if uploaded_file is None:
        st.markdown("""
            <div class="empty-state">
                <div class="empty-state-icon">📁</div>
                <div class="empty-state-text">Upload a CSV file to begin</div>
                <div class="empty-state-subtext">Drag and drop or click to browse</div>
            </div>
        """, unsafe_allow_html=True)
        st.stop()

    # Parse CSV
    companies = parse_csv(uploaded_file)

    if not companies:
        st.warning("No valid companies found in the uploaded file")
        st.stop()

    # Modern upload status card
    st.markdown(f"""
        <div style="background: #111827; border-radius: 12px; padding: 1.25rem; border: 1px solid #1F2937; margin-bottom: 1rem;">
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <div>
                    <div style="color: #FFFFFF; font-weight: 600; font-size: 0.9rem;">{uploaded_file.name}</div>
                    <div style="color: #94A3B8; font-size: 0.75rem;">{len(companies)} companies loaded</div>
                </div>
                <div style="color: #22C55E; font-size: 0.75rem; font-weight: 600; background: rgba(34, 197, 94, 0.1); padding: 0.25rem 0.75rem; border-radius: 6px;">✓ Ready</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Show preview
    with st.expander("📋 Preview First 5 Companies"):
        st.dataframe(
            [{"Company": company} for company in companies[:5]],
            hide_index=True,
            width='stretch'
        )
        if len(companies) > 5:
            st.caption(f"... and {len(companies) - 5} more companies")

    # =========================================================================
    # PROCESSING SECTION
    # =========================================================================
    st.markdown('<div class="section-header">🧠 AI Processing</div>', unsafe_allow_html=True)

    # Initialize session state for results
    if "results" not in st.session_state:
        st.session_state.results = []
    if "processing_complete" not in st.session_state:
        st.session_state.processing_complete = False

    col1, col2 = st.columns([1, 3])
    with col1:
        start_button = st.button("🚀 Search All", type="primary", width='stretch')

    if start_button:
        # Reset state
        st.session_state.results = []
        st.session_state.processing_complete = False

        # Create progress bar and status placeholder with AI brain icon
        st.markdown("""
            <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem;">
                <div class="ai-brain-icon">🧠</div>
                <div>
                    <div style="color: white; font-weight: 700; font-size: 1.1rem;">AI Analysis in Progress</div>
                    <div style="color: #94A3B8; font-size: 0.85rem;">Analyzing company websites and extracting insights</div>
                </div>
            </div>
        """, unsafe_allow_html=True)
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        results_container = st.container()

        # Process companies in parallel for faster results
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = min(3, len(companies))
        status_text.text(f"Process {len(companies)} companies in parallel ({max_workers} at a time)...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_company = {executor.submit(process_company, company, False): company for company in companies}
            completed = 0

            for future in as_completed(future_to_company):
                company = future_to_company[future]
                completed += 1

                try:
                    result = future.result()
                    st.session_state.results.append(result)
                    status_text.text(f"Completed: {company} ({completed}/{len(companies)})")
                except Exception as e:
                    # Handle any unexpected errors
                    error_result = {
                        "company_name": company,
                        "url": "",
                        "summary": "",
                        "industry": "Other",
                        "size_estimate": "",
                        "b2b_buyer": False,
                        "lead_score": 0,
                        "status_tag": "Unknown",
                        "score_reason": "",
                        "error": str(e),
                        "Headcount W1": 0,
                        "Headcount W4": 0,
                        "Growth Rate %": 0,
                        "Growth Label": "No data",
                    }
                    st.session_state.results.append(error_result)
                    status_text.text(f"Failed: {company} ({completed}/{len(companies)})")

                # Update progress
                progress_bar.progress(completed / len(companies))

            # Show real-time results in unified table format
            with results_container:
                if st.session_state.results:
                    import pandas as pd

                    # Prepare data for unified table (same format as final results)
                    table_data = []
                    for r in st.session_state.results:
                        status = r.get("status_tag", "Unknown")
                        status_icon = "🔥" if status == "Hot" else "🌟" if status == "Warm" else "❄️" if status == "Cold" else "⚪"
                        b2b = r.get("b2b_buyer", False)
                        b2b_display = "✅ Yes" if b2b else "❌ No"
                        error_msg = r.get("error", "")
                        status_display = f"{status_icon} {status}" if not error_msg else f"❌ Error"
                        
                        # Industry
                        industry = r.get("industry", "")
                        
                        # Size
                        size = r.get("size_estimate", "")
                        
                        # Score
                        score = r.get("lead_score", 0)
                        
                        # Status
                        status = r.get("status_tag", "Unknown")

                        # Create a unique key for each row's insights button
                        company_name = r.get("company_name", "")
                        insights_key = f"insights_{company_name.replace(' ', '_')}"

                        # Filter and format email
                        raw_email = r.get("email", "") or ""
                        filtered_email = filter_public_email(raw_email)
                        
                        # Validate and format phone
                        raw_phone = r.get("phone", "") or ""
                        formatted_phone = validate_and_format_phone(raw_phone)

                        table_data.append({
                            "Logo": r.get("company_name", "")[:1].upper(),  # Just show first letter
                            "Company": r.get("company_name", ""),
                            "Website": r.get("url", ""),
                            "Industry": industry,
                            "Size": size,
                            "Score": score,
                            "Status": status,
                            "Insights": company_name,  # Store company name for lookup
                            "Email": filtered_email,
                            "Phone": formatted_phone,
                        })

                    df_display = pd.DataFrame(table_data)
                    
                    # Add selection for Lead Insights
                    selected_company = st.selectbox(
                        "👁️ Select a company to view Lead Insights:",
                        options=[""] + [r.get("company_name", "") for r in st.session_state.results if r.get("error") is None],
                        key="realtime_insights_selector"
                    )
                    
                    # Display table using Streamlit's native dataframe
                    st.dataframe(
                        df_display,
                        width='stretch',
                        hide_index=True,
                        column_config={
                            "Logo": st.column_config.TextColumn("Logo", width="small"),
                            "Company": st.column_config.TextColumn("Company", width="medium"),
                            "Website": st.column_config.LinkColumn("Website", width="medium"),
                            "Industry": st.column_config.TextColumn("Industry", width="small"),
                            "Size": st.column_config.TextColumn("Size", width="small"),
                            "Score": st.column_config.NumberColumn("Score", width="small"),
                            "Status": st.column_config.TextColumn("Status", width="small"),
                            "Email": st.column_config.TextColumn("Email", width="medium"),
                            "Phone": st.column_config.TextColumn("Phone", width="medium"),
                        }
                    )
                    
                    # Show Lead Insights panel if a company is selected
                    if selected_company:
                        selected_result = next((r for r in st.session_state.results if r.get("company_name") == selected_company and r.get("error") is None), None)
                        if selected_result:
                            st.markdown("---")
                            st.markdown('<div class="section-header">👁️ Lead Insights</div>', unsafe_allow_html=True)
                            
                            # Generate dynamic explanations
                            explanation = generate_lead_explanation(selected_result)
                            breakdown = get_lead_qualification_breakdown(selected_result)
                            
                            # Get company logo for panel
                            url = selected_result.get("url", "")
                            company_name = selected_result.get("company_name", "")
                            company_initial = company_name[:1].upper() if company_name else "?"
                            
                            # Display lead intelligence panel with premium SaaS design
                            st.markdown(f"""
                                <div style="background: #111827; 
                                            backdrop-filter: blur(20px); 
                                            -webkit-backdrop-filter: blur(20px);
                                            border: 1px solid #1F2937;
                                            border-radius: 12px; 
                                            padding: 1.5rem; 
                                            margin-bottom: 1.5rem;
                                            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                            """, unsafe_allow_html=True)
                            
                            # Company Overview Section
                            col1, col2 = st.columns([1, 3])
                            with col1:
                                st.markdown(f"""
                                    <div style="text-align: center; padding: 1rem;">
                                        <div style="width: 80px; height: 80px; border-radius: 50%; background: #06B6D4; align-items: center; justify-content: center; color: #0B1220; font-weight: 700; font-size: 32px; margin: 0 auto;">{company_initial}</div>
                                        <h3 style="margin-top: 1rem; margin-bottom: 0.25rem; color: #FFFFFF; font-weight: 700; font-size: 1rem;">{company_name}</h3>
                                        <a href="{url}" target="_blank" style="color: #06B6D4; text-decoration: none; font-size: 0.85rem;">{url}</a>
                                    </div>
                                """, unsafe_allow_html=True)
                            
                            with col2:
                                st.markdown("""
                                    <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">📋 Company Overview</h4>
                                """, unsafe_allow_html=True)
                                
                                overview_col1, overview_col2, overview_col3 = st.columns(3)
                                with overview_col1:
                                    st.metric("Industry", selected_result.get("industry", ""))
                                with overview_col2:
                                    st.metric("Size", selected_result.get("size_estimate", ""))
                                with overview_col3:
                                    b2b_status = "✅ Yes" if selected_result.get("b2b_buyer") else "❌ No"
                                    st.metric("B2B Buyer", b2b_status)
                            
                            st.markdown("</div>", unsafe_allow_html=True)
                            
                            # Score Explanation
                            st.markdown("""
                                <div style="background: #111827; 
                                            border-radius: 12px; 
                                            padding: 1.25rem; 
                                            margin-bottom: 1rem;
                                            border: 1px solid #1F2937;
                                            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                                <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">📊 Score Explanation</h4>
                            """, unsafe_allow_html=True)
                            
                            score = selected_result.get("lead_score", 0)
                            status = selected_result.get("status_tag", "Unknown")
                            
                            st.markdown(f"""
                                <div style="display: flex; gap: 1rem; margin-bottom: 1rem;">
                                    <div style="flex: 1; padding: 1rem; border-radius: 8px; background: {'#EF4444' if status == 'Hot' else '#F59E0B' if status == 'Warm' else '#3B82F6' if status == 'Cold' else '#64748B'}; color: white; text-align: center;">
                                        <div style="font-size: 1.5rem; font-weight: 700;">{score}/10</div>
                                        <div style="font-size: 0.85rem; opacity: 0.9;">{status}</div>
                                    </div>
                                    <div style="flex: 3; padding: 1rem; border-radius: 8px; background: #1E293B;">
                                        <div style="font-size: 0.85rem; color: #FFFFFF; line-height: 1.6;">{explanation}</div>
                                    </div>
                                </div>
                            """, unsafe_allow_html=True)
                            
                            st.markdown("</div>", unsafe_allow_html=True)

        # Processing complete
        st.session_state.processing_complete = True
        status_text.text("✓ Process complete!")
        progress_bar.empty()

        # Generate ICP from successful results
        successful_results = [r for r in st.session_state.results if r.get("error") is None]
        if successful_results:
            with st.spinner("🧠 Generating Ideal Customer Profile (ICP)..."):
                st.session_state.icp = generate_icp(successful_results)
            
            # Calculate ICP match scores for analyzed companies
            icp = st.session_state.icp
            target_industries = icp.get('target_industries', [])
            target_size = icp.get('target_size', '')
            
            for result in st.session_state.results:
                if result.get("error") is None:
                    icp_match_score = 0
                    company_industry = result.get("industry", "")
                    company_size = result.get("size_estimate", "")
                    
                    # Industry match (up to 3 points)
                    if company_industry in target_industries:
                        icp_match_score += 3
                    
                    # Size match (up to 2 points)
                    if target_size and company_size:
                        if target_size.lower() in company_size.lower() or company_size.lower() in target_size.lower():
                            icp_match_score += 2
                    
                    # B2B buyer match (up to 2 points)
                    if result.get("b2b_buyer"):
                        icp_match_score += 2
                    
                    # High lead score bonus (up to 3 points)
                    if result.get("lead_score", 0) >= 7:
                        icp_match_score += 3
                    
                    result["icp_match_score"] = icp_match_score
            
            # Generate company recommendations based on ICP
            with st.spinner("🎯 Finding similar companies..."):
                st.session_state.recommendations = recommend_companies(st.session_state.icp, num_recommendations=5)

        # Show statistics cards after processing
        results = st.session_state.results
        total = len(results)
        successful = sum(1 for r in results if r.get("error") is None)
        failed = total - successful
        hot_count = sum(1 for r in results if r.get("status_tag") == "Hot")
        warm_count = sum(1 for r in results if r.get("status_tag") == "Warm")
        cold_count = sum(1 for r in results if r.get("status_tag") == "Cold")
        avg_score = sum(r.get("lead_score", 0) for r in results) / total if total > 0 else 0

        st.markdown(f"""
            <div style="display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap;">
                <div class="metric-container" style="flex: 1; min-width: 140px;">
                    <div class="metric-value">{total}</div>
                    <div class="metric-label">Companies</div>
                </div>
                <div class="metric-container" style="flex: 1; min-width: 140px;">
                    <div class="metric-value">{successful}</div>
                    <div class="metric-label">Processed</div>
                </div>
                <div class="metric-container" style="flex: 1; min-width: 140px;">
                    <div class="metric-value">{hot_count}</div>
                    <div class="metric-label">Hot Leads</div>
                </div>
                <div class="metric-container" style="flex: 1; min-width: 140px;">
                    <div class="metric-value">{avg_score:.1f}</div>
                    <div class="metric-label">Avg Score</div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        # Show errors if any
        errors = [r for r in st.session_state.results if r.get("error")]
        if errors:
            with st.expander(f"⚠️ View Errors ({len(errors)} failed)", expanded=True):
                for e in errors:
                    st.error(f"**{e['company_name']}**: {e['error']}")

    # =========================================================================
    # RESULTS SECTION (shown during and after processing) - UNIFIED SINGLE TABLE
    # =========================================================================
    if st.session_state.results:
        st.markdown('<div class="section-header">📊 Search Results</div>', unsafe_allow_html=True)

        results = st.session_state.results

        # Calculate summary metrics
        total = len(results)
        successful = sum(1 for r in results if r.get("error") is None)
        failed = total - successful
        hot_count = sum(1 for r in results if r.get("status_tag") == "Hot")
        warm_count = sum(1 for r in results if r.get("status_tag") == "Warm")
        cold_count = sum(1 for r in results if r.get("status_tag") == "Cold")

        # Show summary stats in one line
        st.markdown(f"""
            <div style="display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap;">
                <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <div style="font-size: 2rem; font-weight: 700; color: #06B6D4;">{total}</div>
                    <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Total</div>
                </div>
                <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <div style="font-size: 2rem; font-weight: 700; color: #22C55E;">{successful}</div>
                    <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Success</div>
                </div>
                <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <div style="font-size: 2rem; font-weight: 700; color: #EF4444;">{failed}</div>
                    <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Failed</div>
                </div>
                <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <div style="font-size: 2rem; font-weight: 700; color: #EF4444;">🔥 {hot_count}</div>
                    <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Hot</div>
                </div>
                <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <div style="font-size: 2rem; font-weight: 700; color: #F59E0B;">🌟 {warm_count}</div>
                    <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Warm</div>
                </div>
                <div style="background: #111827; border: 1px solid #334155; padding: 1.5rem; border-radius: 12px; color: white; text-align: center; flex: 1; min-width: 120px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <div style="font-size: 2rem; font-weight: 700; color: #3B82F6;">❄️ {cold_count}</div>
                    <div style="font-size: 0.75rem; color: #94A3B8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.5rem;">Cold</div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        # Show AI Recommended Companies section if available
        if st.session_state.get("recommendations") and st.session_state.get("icp"):
            st.markdown('<div class="section-header">🎯 AI Recommended Companies</div>', unsafe_allow_html=True)
            
            # Show ICP summary card
            icp = st.session_state.icp
            st.markdown(f"""
                <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">🧠 Generated Ideal Customer Profile (ICP)</h4>
                    <p style="color: #94A3B8; margin-bottom: 1rem; font-size: 0.85rem; line-height: 1.6;">{icp.get('icp_summary', '')}</p>
                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; margin-top: 1rem;">
                        <div>
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Target Industries</div>
                            <div style="color: #94A3B8; font-size: 0.8rem;">{', '.join(icp.get('target_industries', []))}</div>
                        </div>
                        <div>
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Target Size</div>
                            <div style="color: #94A3B8; font-size: 0.8rem;">{icp.get('target_size', '')}</div>
                        </div>
                        <div>
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Business Model</div>
                            <div style="color: #94A3B8; font-size: 0.8rem;">{icp.get('business_model', '')}</div>
                        </div>
                        <div>
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Customer Segment</div>
                            <div style="color: #94A3B8; font-size: 0.8rem;">{icp.get('customer_segment', '')}</div>
                        </div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
            
            # Show recommended companies
            recommendations = st.session_state.recommendations
            for rec in recommendations:
                company_initial = rec.get("company_name", "")[:1].upper() if rec.get("company_name") else "?"
                similarity_score = rec.get("similarity_score", 0)
                
                st.markdown(f"""
                    <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                        <div style="display: flex; gap: 1.5rem; align-items: flex-start;">
                            <div style="flex-shrink: 0;">
                                <div style="width: 60px; height: 60px; border-radius: 50%; background: #06B6D4; align-items: center; justify-content: center; color: #0B1220; font-weight: 700; font-size: 24px; margin: 0 auto;">{company_initial}</div>
                            </div>
                            <div style="flex: 1;">
                                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem;">
                                    <h4 style="color: #FFFFFF; margin: 0; font-weight: 700; font-size: 1rem;">{rec.get('company_name', '')}</h4>
                                    <div style="background: #06B6D4; color: #0B1220; padding: 0.25rem 0.75rem; border-radius: 6px; font-weight: 700; font-size: 0.85rem;">{similarity_score}/10</div>
                                </div>
                                <div style="color: #94A3B8; font-size: 0.85rem; margin-bottom: 0.5rem;">
                                    <a href="{rec.get('website', '')}" target="_blank" style="color: #06B6D4; text-decoration: none;">{rec.get('website', '')}</a>
                                </div>
                                <div style="color: #94A3B8; font-size: 0.85rem; margin-bottom: 0.5rem;">
                                    <strong style="color: #06B6D4;">Industry:</strong> {rec.get('industry', '')}
                                </div>
                                <p style="color: #94A3B8; font-size: 0.85rem; line-height: 1.5; margin: 0.5rem 0;">{rec.get('description', '')}</p>
                                <div style="background: #1E293B; padding: 0.75rem; border-radius: 8px; margin-top: 0.75rem;">
                                    <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Why it matches:</div>
                                    <div style="color: #94A3B8; font-size: 0.8rem; line-height: 1.4;">{rec.get('match_reason', '')}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)
            
            # Add explainability card
            st.markdown(f"""
                <div style="background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; margin-top: 2rem; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <h4 style="color: #FFFFFF; margin-bottom: 1.5rem; font-weight: 600; font-size: 0.9rem;">🔍 How AI Generated Recommendations</h4>
                    <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;">
                        <div style="background: #1E293B; padding: 0.75rem 1rem; border-radius: 8px; color: #94A3B8; font-size: 0.8rem;">
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Step 1</div>
                            <div>Uploaded Companies</div>
                        </div>
                        <div style="color: #06B6D4; font-size: 1.5rem;">→</div>
                        <div style="background: #1E293B; padding: 0.75rem 1rem; border-radius: 8px; color: #94A3B8; font-size: 0.8rem;">
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Step 2</div>
                            <div>AI Pattern Detection</div>
                        </div>
                        <div style="color: #06B6D4; font-size: 1.5rem;">→</div>
                        <div style="background: #1E293B; padding: 0.75rem 1rem; border-radius: 8px; color: #94A3B8; font-size: 0.8rem;">
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Step 3</div>
                            <div>ICP Generation</div>
                        </div>
                        <div style="color: #06B6D4; font-size: 1.5rem;">→</div>
                        <div style="background: #1E293B; padding: 0.75rem 1rem; border-radius: 8px; color: #94A3B8; font-size: 0.8rem;">
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Step 4</div>
                            <div>Similarity Search</div>
                        </div>
                        <div style="color: #06B6D4; font-size: 1.5rem;">→</div>
                        <div style="background: #1E293B; padding: 0.75rem 1rem; border-radius: 8px; color: #94A3B8; font-size: 0.8rem;">
                            <div style="color: #06B6D4; font-weight: 600; font-size: 0.75rem; margin-bottom: 0.25rem;">Step 5</div>
                            <div>Recommended Companies</div>
                        </div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        # Create unified DataFrame with all fields
        import pandas as pd

        # Prepare data for unified table
        table_data = []
        for r in results:
            # Status icon
            status = r.get("status_tag", "Unknown")
            status_icon = "🔥" if status == "Hot" else "🌟" if status == "Warm" else "❄️" if status == "Cold" else "⚪"

            # Error status
            error_msg = r.get("error", "")
            status_display = f"{status_icon} {status}" if not error_msg else f"❌ Error"
            
            # Industry
            industry = r.get("industry", "")
            
            # Size
            size = r.get("size_estimate", "")
            
            # Score
            score = r.get("lead_score", 0)
            
            # ICP Match Score
            icp_match_score = r.get("icp_match_score", 0)
            
            # Status
            status = r.get("status_tag", "Unknown")

            # Filter and format email
            raw_email = r.get("email", "") or ""
            filtered_email = filter_public_email(raw_email)
            
            # Validate and format phone
            raw_phone = r.get("phone", "") or ""
            formatted_phone = validate_and_format_phone(raw_phone)

            table_data.append({
                "Logo": r.get("company_name", "")[:1].upper(),  # Just show first letter
                "Company": r.get("company_name", ""),
                "Website": r.get("url", ""),
                "Industry": industry,
                "Size": size,
                "Score": score,
                "ICP Match": icp_match_score,
                "Status": status,
                "Insights": r.get("company_name", ""),
                "Email": filtered_email,
                "Phone": formatted_phone,
            })

        df_display = pd.DataFrame(table_data)

        # Add selection for Lead Insights
        selected_company = st.selectbox(
            "👁️ Select a company to view Lead Insights:",
            options=[""] + [r.get("company_name", "") for r in results if r.get("error") is None],
            key="final_insights_selector"
        )

        # Display table using Streamlit's native dataframe
        st.dataframe(
            df_display,
            width='stretch',
            hide_index=True,
            column_config={
                "Logo": st.column_config.TextColumn("Logo", width="small"),
                "Company": st.column_config.TextColumn("Company", width="medium"),
                "Website": st.column_config.LinkColumn("Website", width="medium"),
                "Industry": st.column_config.TextColumn("Industry", width="small"),
                "Size": st.column_config.TextColumn("Size", width="small"),
                "Score": st.column_config.NumberColumn("Score", width="small"),
                "ICP Match": st.column_config.NumberColumn("ICP Match", width="small"),
                "Status": st.column_config.TextColumn("Status", width="small"),
                "Email": st.column_config.TextColumn("Email", width="medium"),
                "Phone": st.column_config.TextColumn("Phone", width="medium"),
            }
        )
        
        # Show Lead Insights panel if a company is selected
        if selected_company:
            selected_result = next((r for r in results if r.get("company_name") == selected_company and r.get("error") is None), None)
            if selected_result:
                st.markdown("---")
                st.markdown('<div class="section-header">👁️ Lead Insights</div>', unsafe_allow_html=True)
                
                # Generate dynamic explanations
                explanation = generate_lead_explanation(selected_result)
                breakdown = get_lead_qualification_breakdown(selected_result)
                
                # Get company logo for panel
                url = selected_result.get("url", "")
                company_name = selected_result.get("company_name", "")
                company_initial = company_name[:1].upper() if company_name else "?"
                
                # Display lead intelligence panel with premium SaaS design
                st.markdown(f"""
                    <div style="background: #111827; 
                                backdrop-filter: blur(20px); 
                                -webkit-backdrop-filter: blur(20px);
                                border: 1px solid #1F2937;
                                border-radius: 12px; 
                                padding: 1.5rem; 
                                margin-bottom: 1.5rem;
                                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                """, unsafe_allow_html=True)
                
                # Company Overview Section
                col1, col2 = st.columns([1, 3])
                with col1:
                    st.markdown(f"""
                        <div style="text-align: center; padding: 1rem;">
                            <div style="width: 80px; height: 80px; border-radius: 50%; background: #06B6D4; align-items: center; justify-content: center; color: #0B1220; font-weight: 700; font-size: 32px; margin: 0 auto;">{company_initial}</div>
                            <h3 style="margin-top: 1rem; margin-bottom: 0.25rem; color: #FFFFFF; font-weight: 700; font-size: 1rem;">{company_name}</h3>
                            <a href="{url}" target="_blank" style="color: #06B6D4; text-decoration: none; font-size: 0.85rem;">{url}</a>
                        </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown("""
                        <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">📋 Company Overview</h4>
                    """, unsafe_allow_html=True)
                    
                    overview_col1, overview_col2, overview_col3 = st.columns(3)
                    with overview_col1:
                        st.metric("Industry", selected_result.get("industry", ""))
                    with overview_col2:
                        st.metric("Size", selected_result.get("size_estimate", ""))
                    with overview_col3:
                        b2b_status = "✅ Yes" if selected_result.get("b2b_buyer") else "❌ No"
                        st.metric("B2B Buyer", b2b_status)
                
                st.markdown("</div>", unsafe_allow_html=True)
                
                # Score Explanation
                st.markdown("""
                    <div style="background: #111827; 
                                border-radius: 12px; 
                                padding: 1.25rem; 
                                margin-bottom: 1rem;
                                border: 1px solid #1F2937;
                                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">📊 Score Explanation</h4>
                """, unsafe_allow_html=True)
                
                score = selected_result.get("lead_score", 0)
                status = selected_result.get("status_tag", "Unknown")
                
                st.markdown(f"""
                    <div style="display: flex; gap: 1rem; margin-bottom: 1rem;">
                        <div style="flex: 1; padding: 1rem; border-radius: 8px; background: {'#EF4444' if status == 'Hot' else '#F59E0B' if status == 'Warm' else '#3B82F6' if status == 'Cold' else '#64748B'}; color: white; text-align: center;">
                            <div style="font-size: 1.5rem; font-weight: 700;">{score}/10</div>
                            <div style="font-size: 0.85rem; opacity: 0.9;">{status}</div>
                        </div>
                        <div style="flex: 3; padding: 1rem; border-radius: 8px; background: #1E293B;">
                            <div style="font-size: 0.85rem; color: #FFFFFF; line-height: 1.6;">{explanation}</div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)
                
                st.markdown("</div>", unsafe_allow_html=True)

        # =========================================================================
        # LEAD INTELLIGENCE PANEL
        # =========================================================================
        st.markdown('<br>', unsafe_allow_html=True)
        st.markdown('<div class="section-header">👁️ Lead Intelligence</div>', unsafe_allow_html=True)
        
        # Company selector for detailed view
        successful_results = [r for r in results if r.get("error") is None]
        if successful_results:
            company_names = [r.get("company_name", "") for r in successful_results]
            selected_company = st.selectbox(
                "Select a company to view detailed lead intelligence:",
                options=company_names,
                index=0,
                key="lead_intelligence_selector"
            )
            
            # Find the selected company's data
            selected_result = next((r for r in successful_results if r.get("company_name") == selected_company), None)
            
            if selected_result:
                # Generate dynamic explanations
                explanation = generate_lead_explanation(selected_result)
                breakdown = get_lead_qualification_breakdown(selected_result)
                
                # Get company logo for panel
                url = selected_result.get("url", "")
                company_name = selected_result.get("company_name", "")
                company_initial = company_name[:1].upper() if company_name else "?"
                
                # Display lead intelligence panel with premium SaaS design
                st.markdown(f"""
                    <div style="background: #111827; 
                                backdrop-filter: blur(20px); 
                                -webkit-backdrop-filter: blur(20px);
                                border: 1px solid #1F2937;
                                border-radius: 12px; 
                                padding: 1.5rem; 
                                margin-bottom: 1.5rem;
                                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                """, unsafe_allow_html=True)
                
                # Company Overview Section
                col1, col2 = st.columns([1, 3])
                with col1:
                    st.markdown(f"""
                        <div style="text-align: center; padding: 1rem;">
                            <div style="width: 80px; height: 80px; border-radius: 50%; background: #06B6D4; align-items: center; justify-content: center; color: #0B1220; font-weight: 700; font-size: 32px; margin: 0 auto;">{company_initial}</div>
                            <h3 style="margin-top: 1rem; margin-bottom: 0.25rem; color: #FFFFFF; font-weight: 700; font-size: 1rem;">{company_name}</h3>
                            <a href="{url}" target="_blank" style="color: #06B6D4; text-decoration: none; font-size: 0.85rem;">{url}</a>
                        </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown("""
                        <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">📋 Company Overview</h4>
                    """, unsafe_allow_html=True)
                    
                    overview_col1, overview_col2, overview_col3 = st.columns(3)
                    with overview_col1:
                        st.metric("Industry", selected_result.get("industry", ""))
                    with overview_col2:
                        st.metric("Size", selected_result.get("size_estimate", ""))
                    with overview_col3:
                        b2b_status = "✅ Yes" if selected_result.get("b2b_buyer") else "❌ No"
                        st.metric("B2B Buyer", b2b_status)
                
                st.markdown("</div>", unsafe_allow_html=True)
                
                # Lead Qualification Breakdown
                st.markdown("""
                    <div style="background: #111827; 
                                border-radius: 12px; 
                                padding: 1.25rem; 
                                margin-bottom: 1rem;
                                border: 1px solid #1F2937;
                                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">🎯 Lead Qualification Breakdown</h4>
                """, unsafe_allow_html=True)
                
                qual_col1, qual_col2 = st.columns(2)
                with qual_col1:
                    for key, value in list(breakdown.items())[:2]:
                        st.markdown(f"""
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; margin-bottom: 0.5rem; background: #1F2937; border-radius: 8px;">
                                <span style="font-weight: 500; color: #FFFFFF; font-size: 0.85rem;">{key}</span>
                                <span style="color: #06B6D4; font-weight: 600; font-size: 0.85rem;">{value}</span>
                            </div>
                        """, unsafe_allow_html=True)
                
                with qual_col2:
                    for key, value in list(breakdown.items())[2:]:
                        st.markdown(f"""
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; margin-bottom: 0.5rem; background: #1F2937; border-radius: 8px;">
                                <span style="font-weight: 500; color: #FFFFFF; font-size: 0.85rem;">{key}</span>
                                <span style="color: #06B6D4; font-weight: 600; font-size: 0.85rem;">{value}</span>
                            </div>
                        """, unsafe_allow_html=True)
                
                st.markdown("</div>", unsafe_allow_html=True)
                
                # Score Explanation
                st.markdown("""
                    <div style="background: #111827; 
                                border-radius: 12px; 
                                padding: 1.25rem; 
                                margin-bottom: 1rem;
                                border: 1px solid #1F2937;
                                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">📊 Score Explanation</h4>
                """, unsafe_allow_html=True)
                
                score = selected_result.get("lead_score", 0)
                status = selected_result.get("status_tag", "Unknown")
                
                st.markdown(f"""
                    <div style="display: flex; gap: 1rem; margin-bottom: 1rem;">
                        <div style="flex: 1; padding: 1rem; border-radius: 8px; background: {'#EF4444' if status == 'Hot' else '#F59E0B' if status == 'Warm' else '#3B82F6' if status == 'Cold' else '#64748B'}; color: white; text-align: center;">
                            <div style="font-size: 1.5rem; font-weight: 700;">{score}/10</div>
                            <div style="font-size: 0.85rem; opacity: 0.9;">{status}</div>
                        </div>
                        <div style="flex: 3; padding: 1rem; border-radius: 8px; background: #1E293B;">
                            <div style="font-size: 0.85rem; color: #FFFFFF; line-height: 1.6;">{explanation}</div>
                        </div>
                    </div>
                    
                    <div style="padding: 0.75rem; border-radius: 8px; background: #1E293B; border-left: 3px solid #06B6D4;">
                        <strong style="color: #FFFFFF; font-size: 0.85rem;">Score Ranges:</strong><br>
                        <span style="color: #94A3B8; font-size: 0.8rem;">🔥 8–10 = Hot (High-priority leads ready for immediate outreach)</span><br>
                        <span style="color: #94A3B8; font-size: 0.8rem;">🌟 5–7 = Warm (Potential leads worth nurturing)</span><br>
                        <span style="color: #94A3B8; font-size: 0.8rem;">❄️ 1–4 = Cold (Low-priority or poor-fit prospects)</span>
                    </div>
                """, unsafe_allow_html=True)
                
                st.markdown("</div>", unsafe_allow_html=True)
                
                # AI Reasoning
                st.markdown("""
                    <div style="background: #111827; 
                                border-radius: 12px; 
                                padding: 1.25rem; 
                                margin-bottom: 1rem;
                                border: 1px solid #1F2937;
                                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">🤖 AI Reasoning</h4>
                """, unsafe_allow_html=True)
                
                st.info(f"**Why AI believes this is a good prospect:**\n\n{selected_result.get('score_reason', 'No AI reasoning available.')}")
                
                st.markdown("</div>", unsafe_allow_html=True)
                
                # Contact Intelligence
                st.markdown("""
                    <div style="background: #111827; 
                                border-radius: 12px; 
                                padding: 1.25rem; 
                                margin-bottom: 1rem;
                                border: 1px solid #1F2937;
                                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);">
                    <h4 style="color: #FFFFFF; margin-bottom: 1rem; font-weight: 600; font-size: 0.9rem;">📞 Contact Intelligence</h4>
                """, unsafe_allow_html=True)
                
                # Display contact information with icons
                contact_col1, contact_col2 = st.columns(2)
                
                with contact_col1:
                    # Email
                    email = selected_result.get("email", "")
                    if email:
                        st.markdown(f"""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">📧</span>
                                <div>
                                    <div class="contact-label">Email</div>
                                    <div class="contact-value"><a href="mailto:{email}">{email}</a></div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">📧</span>
                                <div>
                                    <div class="contact-label">Email</div>
                                    <div class="contact-value" style="color: #64748B; font-style: italic;">Not Found</div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    
                    # Phone
                    phone = selected_result.get("phone", "")
                    if phone:
                        st.markdown(f"""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">📞</span>
                                <div>
                                    <div class="contact-label">Phone</div>
                                    <div class="contact-value"><a href="tel:{phone}">{phone}</a></div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">📞</span>
                                <div>
                                    <div class="contact-label">Phone</div>
                                    <div class="contact-value" style="color: #64748B; font-style: italic;">Not Found</div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    
                    # Website
                    website = selected_result.get("url", "")
                    if website:
                        st.markdown(f"""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">🌍</span>
                                <div>
                                    <div class="contact-label">Website</div>
                                    <div class="contact-value"><a href="{website}" target="_blank">{website}</a></div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                
                with contact_col2:
                    # Headquarters
                    headquarters = selected_result.get("headquarters", "")
                    country = selected_result.get("country", "")
                    if headquarters or country:
                        location = f"{headquarters}, {country}" if headquarters and country else headquarters or country
                        st.markdown(f"""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">🏢</span>
                                <div>
                                    <div class="contact-label">Headquarters</div>
                                    <div class="contact-value">{location}</div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">🏢</span>
                                <div>
                                    <div class="contact-label">Headquarters</div>
                                    <div class="contact-value" style="color: #64748B; font-style: italic;">Not Found</div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    
                    # LinkedIn
                    linkedin = selected_result.get("linkedin", "")
                    if linkedin:
                        st.markdown(f"""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">💼</span>
                                <div>
                                    <div class="contact-label">LinkedIn</div>
                                    <div class="contact-value"><a href="{linkedin}" target="_blank">View Company Page</a></div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">💼</span>
                                <div>
                                    <div class="contact-label">LinkedIn</div>
                                    <div class="contact-value" style="color: #64748B; font-style: italic;">Not Found</div>
                                </div>
                            </div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    
                    # Contact Page
                    contact_page = selected_result.get("contact_page", "")
                    if contact_page:
                        st.markdown(f"""
                            <div class="contact-intelligence-card">
                                <span class="contact-icon">📄</span>
                                <div>
                                    <div class="contact-label">Contact Page</div>
                                    <div class="contact-value"><a href="{contact_page}" target="_blank">View Contact Page</a></div>
                                </div>
                            </div>
                                <div>
                                    <div style="font-size: 12px; color: #666; font-weight: 500;">Contact Page</div>
                                    <a href="{contact_page}" target="_blank" style="color: #5B5FDE; text-decoration: none; font-weight: 600;">Visit Contact Page</a>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                            <div style="display: flex; align-items: center; padding: 12px; margin-bottom: 8px; background: #f8f9fa; border-radius: 8px;">
                                <span style="font-size: 20px; margin-right: 12px;">📄</span>
                                <div>
                                    <div style="font-size: 12px; color: #666; font-weight: 500;">Contact Page</div>
                                    <div style="color: #999; font-style: italic;">Not Found</div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                
                # Confidence indicator
                contact_fields = ["email", "phone", "linkedin", "contact_page"]
                found_fields = sum(1 for field in contact_fields if selected_result.get(field, ""))
                
                if found_fields >= 3:
                    confidence = "High Confidence"
                    confidence_color = "#28a745"
                elif found_fields >= 1:
                    confidence = "Medium Confidence"
                    confidence_color = "#feca57"
                else:
                    confidence = "Low Confidence"
                    confidence_color = "#dc3545"
                
                st.markdown(f"""
                    <div style="padding: 15px; border-radius: 12px; background: #f0f4ff; border-left: 4px solid {confidence_color}; margin-top: 15px;">
                        <strong style="color: {confidence_color};">Data Quality:</strong> {confidence} ({found_fields}/4 contact fields found)
                    </div>
                """, unsafe_allow_html=True)
                
                # Contact Reason
                contact_reason = selected_result.get("contact_reason", "")
                if contact_reason:
                    st.markdown(f"""
                        <div style="padding: 15px; border-radius: 12px; background: #f8f9fa; margin-top: 15px;">
                            <strong style="color: #5B5FDE;">Why this company is worth contacting:</strong><br>
                            <span style="color: #333;">{contact_reason}</span>
                        </div>
                    """, unsafe_allow_html=True)
                
                st.markdown("</div>", unsafe_allow_html=True)
                
                # Workflow Visualization
                st.markdown("""
                    <div style="background: white; 
                                border-radius: 16px; 
                                padding: 25px; 
                                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.05);">
                    <h4 style="color: #5B5FDE; margin-bottom: 20px; font-weight: 600;">🔄 Workflow Explanation</h4>
                """, unsafe_allow_html=True)
                
                st.markdown("""
                    <div style="display: flex; align-items: center; justify-content: space-between; padding: 1.5rem; background: #111827; border: 1px solid #334155; border-radius: 12px;">
                        <div style="text-align: center; flex: 1;">
                            <div style="background: #06B6D4; color: #0B1220; padding: 0.5rem 1rem; border-radius: 6px; font-weight: 600; margin-bottom: 0.25rem; font-size: 0.85rem;">Company Data</div>
                            <div style="font-size: 0.75rem; color: #94A3B8;">Website & Info</div>
                        </div>
                        <div style="font-size: 1.5rem; color: #06B6D4;">→</div>
                        <div style="text-align: center; flex: 1;">
                            <div style="background: #06B6D4; color: #0B1220; padding: 0.5rem 1rem; border-radius: 6px; font-weight: 600; margin-bottom: 0.25rem; font-size: 0.85rem;">AI Analysis</div>
                            <div style="font-size: 0.75rem; color: #94A3B8;">NVIDIA AI</div>
                        </div>
                        <div style="font-size: 1.5rem; color: #06B6D4;">→</div>
                        <div style="text-align: center; flex: 1;">
                            <div style="background: #06B6D4; color: #0B1220; padding: 0.5rem 1rem; border-radius: 6px; font-weight: 600; margin-bottom: 0.25rem; font-size: 0.85rem;">Qualification</div>
                            <div style="font-size: 0.75rem; color: #94A3B8;">Industry & Size</div>
                        </div>
                        <div style="font-size: 1.5rem; color: #06B6D4;">→</div>
                        <div style="text-align: center; flex: 1;">
                            <div style="background: #06B6D4; color: #0B1220; padding: 0.5rem 1rem; border-radius: 6px; font-weight: 600; margin-bottom: 0.25rem; font-size: 0.85rem;">Lead Score</div>
                            <div style="font-size: 0.75rem; color: #94A3B8;">1-10 Rating</div>
                        </div>
                        <div style="font-size: 1.5rem; color: #06B6D4;">→</div>
                        <div style="text-align: center; flex: 1;">
                            <div style="background: #EF4444; color: white; padding: 0.5rem 1rem; border-radius: 6px; font-weight: 600; margin-bottom: 0.25rem; font-size: 0.85rem;">Status</div>
                            <div style="font-size: 0.75rem; color: #94A3B8;">Hot/Warm/Cold</div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)
                
                st.markdown("</div>", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

        # =========================================================================
        # ACTION BUTTONS
        # =========================================================================
        st.divider()

        col1, col2, col3 = st.columns(3)

        with col1:
            # Download results as CSV
            import pandas as pd

            df_results = pd.DataFrame(results)
            csv_data = df_results.to_csv(index=False)

            st.download_button(
                label="📥 Download Results as CSV",
                data=csv_data,
                file_name="searched_companies.csv",
                mime="text/csv",
                width='stretch',
            )

        with col2:
            # Push to Airtable button
            if st.button("🚀 Push to Airtable", use_container_width=True):
                with st.spinner("Pushing data to Airtable..."):
                    successful_results = [r for r in results if r.get("error") is None]
                    if successful_results:
                        try:
                            pushed_count = 0
                            failed_count = 0
                            for record in successful_results:
                                if push_to_airtable(record):
                                    pushed_count += 1
                                else:
                                    failed_count += 1
                            
                            if pushed_count > 0:
                                st.success(f"✅ Successfully pushed {pushed_count} records to Airtable!")
                            if failed_count > 0:
                                st.warning(f"⚠️ Failed to push {failed_count} records (check console for details)")
                        except Exception as e:
                            st.error(f"❌ Failed to push to Airtable: {str(e)}")
                    else:
                        st.warning("⚠️ No successful results to push.")

        with col3:
            # View in Airtable button
            base_id = os.getenv("AIRTABLE_BASE_ID", "")
            airtable_url = f"https://airtable.com/{base_id}"

            st.link_button(
                label="🔗 View in Airtable",
                url=airtable_url,
                width='stretch',
            )



if __name__ == "__main__":
    main()
