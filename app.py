"""
AI Sales Intelligence Pipeline - Streamlit Application

A production-ready web app that enriches company leads using AI and
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

# Apple-style 3D Animated CSS with glassmorphism and parallax effects
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@300;400;500;600;700&display=swap');
    
    * {
        font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif;
    }
    
    /* 3D Perspective Container */
    .perspective-container {
        perspective: 1000px;
        transform-style: preserve-3d;
    }
    
    /* Apple-style Glassmorphism Header with 3D */
    .main-header {
        background: linear-gradient(135deg, 
            rgba(102, 126, 234, 0.9) 0%, 
            rgba(118, 75, 162, 0.9) 50%,
            rgba(102, 126, 234, 0.9) 100%);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        padding: 3rem 2rem;
        border-radius: 24px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
        box-shadow: 
            0 20px 60px rgba(102, 126, 234, 0.4),
            0 0 0 1px rgba(255,255,255,0.1) inset;
        transform: translateZ(0);
        transition: transform 0.6s cubic-bezier(0.23, 1, 0.32, 1);
        animation: slideDown3D 1s cubic-bezier(0.23, 1, 0.32, 1);
        border: 1px solid rgba(255,255,255,0.2);
    }
    
    .main-header:hover {
        transform: translateZ(20px) rotateX(2deg);
        box-shadow: 
            0 30px 80px rgba(102, 126, 234, 0.5),
            0 0 0 1px rgba(255,255,255,0.2) inset;
    }
    
    @keyframes slideDown3D {
        from { 
            opacity: 0; 
            transform: translateY(-50px) translateZ(-100px) rotateX(-10deg); 
        }
        to { 
            opacity: 1; 
            transform: translateY(0) translateZ(0) rotateX(0); 
        }
    }
    
    @keyframes fadeIn3D {
        from { 
            opacity: 0; 
            transform: translateY(30px) translateZ(-50px); 
        }
        to { 
            opacity: 1; 
            transform: translateY(0) translateZ(0); 
        }
    }
    
    @keyframes float3D {
        0%, 100% { 
            transform: translateZ(0) translateY(0); 
        }
        50% { 
            transform: translateZ(10px) translateY(-10px); 
        }
    }
    
    @keyframes pulse3D {
        0%, 100% { transform: scale(1) translateZ(0); }
        50% { transform: scale(1.05) translateZ(20px); }
    }
    
    @keyframes rotate3D {
        from { transform: rotateY(-180deg); }
        to { transform: rotateY(0); }
    }
    
    @keyframes shimmer {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
    }
    
    .main-header h1 {
        font-size: 3rem;
        font-weight: 700;
        margin: 0;
        text-shadow: 0 2px 20px rgba(0,0,0,0.3);
        background: linear-gradient(90deg, #fff, #e0e0ff, #fff);
        background-size: 200% auto;
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        animation: shimmer 3s linear infinite;
    }
    
    .main-header p {
        font-size: 1.2rem;
        opacity: 0.95;
        margin-top: 0.5rem;
        font-weight: 400;
        text-shadow: 0 1px 10px rgba(0,0,0,0.2);
    }
    
    /* 3D Glassmorphism Cards */
    .glass-card {
        background: rgba(255, 255, 255, 0.7);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.3);
        box-shadow: 
            0 8px 32px rgba(0,0,0,0.1),
            0 0 0 1px rgba(255,255,255,0.5) inset;
        transform-style: preserve-3d;
        transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1);
        animation: fadeIn3D 0.8s cubic-bezier(0.23, 1, 0.32, 1);
    }
    
    .glass-card:hover {
        transform: translateY(-10px) translateZ(30px) rotateX(5deg);
        box-shadow: 
            0 20px 60px rgba(0,0,0,0.15),
            0 0 0 1px rgba(255,255,255,0.6) inset;
    }
    
    /* 3D Metric Cards */
    .metric-container {
        background: linear-gradient(145deg, rgba(255,255,255,0.9) 0%, rgba(250,250,255,0.9) 100%);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        padding: 2rem;
        border-radius: 24px;
        box-shadow: 
            0 10px 40px rgba(102, 126, 234, 0.15),
            0 0 0 1px rgba(255,255,255,0.5) inset;
        border: 1px solid rgba(255,255,255,0.4);
        transform-style: preserve-3d;
        transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1);
        animation: fadeIn3D 0.6s cubic-bezier(0.23, 1, 0.32, 1);
    }
    
    .metric-container:hover {
        transform: translateY(-15px) translateZ(40px) scale(1.02);
        box-shadow: 
            0 25px 50px rgba(102, 126, 234, 0.25),
            0 0 0 1px rgba(255,255,255,0.6) inset;
    }
    
    .metric-container::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.4), transparent);
        transition: left 0.5s;
    }
    
    .metric-container:hover::before {
        left: 100%;
    }
    
    .metric-value {
        font-size: 3rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
        transform: translateZ(10px);
    }
    
    .metric-label {
        color: #6c757d;
        font-size: 0.95rem;
        font-weight: 500;
        transform: translateZ(5px);
    }
    
    /* 3D Lead Cards with Glassmorphism */
    .lead-card {
        padding: 1.5rem;
        border-radius: 20px;
        text-align: center;
        font-weight: 600;
        transform-style: preserve-3d;
        transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1);
        animation: fadeIn3D 0.6s cubic-bezier(0.23, 1, 0.32, 1);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(255,255,255,0.3);
    }
    
    .lead-card:hover {
        transform: scale(1.08) translateZ(50px) rotateY(5deg);
    }
    
    .lead-hot {
        background: linear-gradient(135deg, rgba(255, 107, 107, 0.9) 0%, rgba(238, 90, 90, 0.9) 100%);
        color: white;
        box-shadow: 
            0 10px 40px rgba(255, 107, 107, 0.4),
            0 0 0 1px rgba(255,255,255,0.3) inset;
    }
    
    .lead-warm {
        background: linear-gradient(135deg, rgba(254, 202, 87, 0.9) 0%, rgba(255, 159, 67, 0.9) 100%);
        color: white;
        box-shadow: 
            0 10px 40px rgba(254, 202, 87, 0.4),
            0 0 0 1px rgba(255,255,255,0.3) inset;
    }
    
    .lead-cold {
        background: linear-gradient(135deg, rgba(72, 219, 251, 0.9) 0%, rgba(10, 189, 227, 0.9) 100%);
        color: white;
        box-shadow: 
            0 10px 40px rgba(72, 219, 251, 0.4),
            0 0 0 1px rgba(255,255,255,0.3) inset;
    }
    
    /* 3D Section Headers */
    .section-header {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 1.8rem;
        font-weight: 700;
        margin: 2.5rem 0 1.5rem 0;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid transparent;
        border-image: linear-gradient(90deg, #667eea, #764ba2) 1;
        transform: translateZ(0);
        transition: transform 0.3s;
    }
    
    .section-header:hover {
        transform: translateZ(10px);
    }
    
    /* 3D Feature Cards */
    .feature-card {
        background: linear-gradient(145deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 2px solid rgba(102, 126, 234, 0.2);
        border-radius: 24px;
        padding: 2rem;
        margin-bottom: 2rem;
        transform-style: preserve-3d;
        transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1);
        animation: fadeIn3D 0.8s cubic-bezier(0.23, 1, 0.32, 1);
    }
    
    .feature-card:hover {
        transform: translateY(-10px) translateZ(20px);
        border-color: rgba(102, 126, 234, 0.4);
        box-shadow: 0 20px 60px rgba(102, 126, 234, 0.2);
    }
    
    /* 3D Animated Gradient Sidebar */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, 
            rgba(102, 126, 234, 0.95) 0%, 
            rgba(118, 75, 162, 0.95) 50%,
            rgba(102, 126, 234, 0.95) 100%);
        background-size: 200% 200%;
        animation: gradientShift 8s ease infinite;
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-right: 1px solid rgba(255,255,255,0.2);
    }
    
    @keyframes gradientShift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    
    /* 3D Buttons */
    .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 16px;
        padding: 1rem 2.5rem;
        font-weight: 600;
        font-size: 1rem;
        transform-style: preserve-3d;
        transition: all 0.4s cubic-bezier(0.23, 1, 0.32, 1);
        box-shadow: 
            0 8px 25px rgba(102, 126, 234, 0.4),
            0 0 0 1px rgba(255,255,255,0.2) inset;
        position: relative;
        overflow: hidden;
    }
    
    .stButton > button::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
        transition: left 0.5s;
    }
    
    .stButton > button:hover::before {
        left: 100%;
    }
    
    .stButton > button:hover {
        transform: translateY(-5px) translateZ(20px) scale(1.02);
        box-shadow: 
            0 15px 35px rgba(102, 126, 234, 0.5),
            0 0 0 1px rgba(255,255,255,0.3) inset;
    }
    
    /* 3D Upload Area */
    .uploadedFile {
        border: 2px dashed rgba(102, 126, 234, 0.5);
        border-radius: 20px;
        padding: 2.5rem;
        background: linear-gradient(145deg, rgba(248,249,250,0.8) 0%, rgba(240,242,255,0.8) 100%);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        transition: all 0.4s cubic-bezier(0.23, 1, 0.32, 1);
        transform-style: preserve-3d;
    }
    
    .uploadedFile:hover {
        border-color: rgba(102, 126, 234, 0.8);
        transform: translateY(-5px) translateZ(10px);
        box-shadow: 0 15px 40px rgba(102, 126, 234, 0.2);
    }
    
    /* 3D Progress Bar */
    .stProgress > div > div > div {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        box-shadow: 0 0 20px rgba(102, 126, 234, 0.5);
    }
    
    /* 3D Dataframe */
    .stDataFrame {
        border-radius: 20px;
        box-shadow: 0 10px 40px rgba(0,0,0,0.1);
        transform-style: preserve-3d;
        overflow: hidden;
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
    
    /* API status indicators with 3D */
    .api-connected {
        color: #00ff88;
        font-weight: 600;
        text-shadow: 0 0 10px rgba(0, 255, 136, 0.5);
        animation: pulse3D 2s infinite;
    }
    
    .api-missing {
        color: #ff6b6b;
        text-shadow: 0 0 10px rgba(255, 107, 107, 0.5);
        font-weight: 600;
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
    Process a single company through the full enrichment pipeline.

    Args:
        company_name: Name of the company to enrich.
        quick_mode: Use faster AI analysis with reduced context for quicker results.

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
        "Headcount W1": 0,
        "Headcount W4": 0,
        "Growth Rate %": 0,
        "Growth Label": "No data",
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


def init_auth_session():
    """Initialize authentication session state."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "user_email" not in st.session_state:
        st.session_state.user_email = None
    if "user_name" not in st.session_state:
        st.session_state.user_name = None
    if "auth_method" not in st.session_state:
        st.session_state.auth_method = None


def check_credentials(email: str, password: str) -> bool:
    """Check email/password credentials."""
    # Demo credentials - in production, use secure password hashing
    demo_users = {
        "admin@example.com": "admin123",
        "user@example.com": "user123",
    }
    return demo_users.get(email) == password


def login_user(email: str, name: str, method: str):
    """Set user as authenticated."""
    st.session_state.authenticated = True
    st.session_state.user_email = email
    st.session_state.user_name = name
    st.session_state.auth_method = method


# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = "http://localhost:8501/oauth2callback"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def get_google_auth_url() -> str:
    """Generate Google OAuth authorization URL."""
    if not GOOGLE_CLIENT_ID or GOOGLE_CLIENT_ID.startswith("your-"):
        return None
    
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_google_code(code: str) -> Dict[str, Any]:
    """Exchange authorization code for access token."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return None
    
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    
    try:
        response = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Token exchange failed: {e}")
        return None


def get_google_user_info(access_token: str) -> Dict[str, Any]:
    """Get user info from Google using access token."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Failed to get user info: {e}")
        return None


def handle_google_oauth_callback():
    """Handle OAuth callback from Google."""
    # Get code from query parameters
    query_params = st.query_params
    
    if "code" in query_params:
        code = query_params["code"]
        
        # Exchange code for token
        token_data = exchange_google_code(code)
        
        if token_data and "access_token" in token_data:
            # Get user info
            user_info = get_google_user_info(token_data["access_token"])
            
            if user_info:
                email = user_info.get("email", "")
                name = user_info.get("name", email.split("@")[0])
                
                # Login user
                login_user(email, name, "Google")
                
                # Clear query params and refresh
                st.query_params.clear()
                st.rerun()
                return True
    
    return False


def logout_user():
    """Logout user and clear session."""
    st.session_state.authenticated = False
    st.session_state.user_email = None
    st.session_state.user_name = None
    st.session_state.auth_method = None


def show_login_page():
    """Display Apple-style login page."""
    # Apple-style login CSS
    st.markdown("""
        <style>
        .login-container {
            max-width: 400px;
            margin: 0 auto;
            padding: 40px;
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(20px);
            border-radius: 24px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.1);
            border: 1px solid rgba(0, 0, 0, 0.05);
        }
        
        .login-logo {
            text-align: center;
            font-size: 3rem;
            margin-bottom: 10px;
        }
        
        .login-title {
            text-align: center;
            font-size: 1.8rem;
            font-weight: 600;
            margin-bottom: 30px;
            color: #1d1d1f;
        }
        
        .apple-button {
            background: #000000;
            color: white;
            border: none;
            padding: 14px 24px;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            width: 100%;
            margin: 10px 0;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }
        
        .apple-button:hover {
            background: #333333;
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2);
        }
        
        .google-button {
            background: #ffffff;
            color: #3c4043;
            border: 1px solid #dadce0;
            padding: 14px 24px;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            width: 100%;
            margin: 10px 0;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }
        
        .google-button:hover {
            background: #f8f9fa;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }
        
        .divider {
            display: flex;
            align-items: center;
            margin: 20px 0;
            color: #86868b;
            font-size: 0.9rem;
        }
        
        .divider::before,
        .divider::after {
            content: '';
            flex: 1;
            height: 1px;
            background: #d2d2d7;
            margin: 0 15px;
        }
        
        .login-input {
            width: 100%;
            padding: 14px 16px;
            border: 1px solid #d2d2d7;
            border-radius: 12px;
            font-size: 1rem;
            margin: 8px 0;
            transition: all 0.3s ease;
            background: #f5f5f7;
        }
        
        .login-input:focus {
            outline: none;
            border-color: #007aff;
            background: white;
            box-shadow: 0 0 0 4px rgba(0, 122, 255, 0.1);
        }
        
        .login-submit {
            background: #007aff;
            color: white;
            border: none;
            padding: 14px 24px;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            width: 100%;
            margin-top: 20px;
            transition: all 0.3s ease;
        }
        
        .login-submit:hover {
            background: #0051d5;
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0, 122, 255, 0.3);
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Center the login container
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("""
            <div class="login-container">
                <div class="login-logo">🚀</div>
                <div class="login-title">AI Sales CRM</div>
            </div>
        """, unsafe_allow_html=True)
        
        # Check if Google OAuth is configured
        google_auth_url = get_google_auth_url()
        
        # Apple Sign In button (simulated for demo)
        if st.button("🍎 Sign in with Apple", key="apple_login", use_container_width=True):
            # In production, this would redirect to Apple OAuth
            # For demo, we'll simulate successful Apple login
            login_user("apple_user@icloud.com", "Apple User", "Apple")
            st.rerun()
        
        # Google Sign In button (REAL OAuth if configured, otherwise demo)
        if google_auth_url:
            # Real Google OAuth - opens Google login page
            st.markdown(f"""
                <a href="{google_auth_url}" target="_self" style="text-decoration: none;">
                    <button style="
                        background: #ffffff;
                        color: #3c4043;
                        border: 1px solid #dadce0;
                        padding: 14px 24px;
                        border-radius: 12px;
                        font-size: 1rem;
                        font-weight: 500;
                        cursor: pointer;
                        width: 100%;
                        margin: 10px 0;
                        transition: all 0.3s ease;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 10px;
                    ">
                        🔵 Sign in with Google
                    </button>
                </a>
            """, unsafe_allow_html=True)
            st.info("ℹ️ Click above to sign in with your real Google account")
        else:
            # Demo mode - Google OAuth not configured
            if st.button("🔵 Sign in with Google (Demo)", key="google_login", use_container_width=True):
                login_user("google_user@gmail.com", "Google User", "Google")
                st.rerun()
        
        st.markdown('<div class="divider">or</div>', unsafe_allow_html=True)
        
        # Email/Password login
        with st.form("login_form"):
            email = st.text_input("📧 Email", placeholder="user@example.com")
            password = st.text_input("🔒 Password", type="password", placeholder="••••••")
            
            submitted = st.form_submit_button("Sign In", use_container_width=True)
            
            if submitted:
                if check_credentials(email, password):
                    login_user(email, email.split("@")[0].title(), "Email")
                    st.success("✓ Login successful!")
                    st.rerun()
                else:
                    st.error("❌ Invalid email or password")
        
        # Demo credentials hint or OAuth setup instructions
        if not google_auth_url:
            st.markdown("""
                <div style="margin-top: 20px; padding: 15px; background: #fff3cd; border-radius: 12px; font-size: 0.8rem; color: #856404;">
                    <strong>🔧 To Enable Real Google Login:</strong><br>
                    1. Go to <a href="https://console.cloud.google.com" target="_blank">Google Cloud Console</a><br>
                    2. Create OAuth 2.0 credentials<br>
                    3. Add redirect: <code>http://localhost:8501/oauth2callback</code><br>
                    4. Update <code>GOOGLE_CLIENT_ID</code> in .env file
                </div>
            """, unsafe_allow_html=True)
        
        st.markdown("""
            <div style="margin-top: 20px; padding: 15px; background: #f5f5f7; border-radius: 12px; font-size: 0.85rem; color: #86868b;">
                <strong>Demo Credentials:</strong><br>
                admin@example.com / admin123<br>
                user@example.com / user123
            </div>
        """, unsafe_allow_html=True)


def main():
    """Main Streamlit application."""
    
    # Initialize authentication
    init_auth_session()
    
    # Handle Google OAuth callback (if user just logged in via Google)
    if not st.session_state.authenticated:
        if handle_google_oauth_callback():
            return  # Successfully logged in via Google
    
    # Check if user is authenticated
    if not st.session_state.authenticated:
        show_login_page()
        return
    
    # User is authenticated - show main app
    # Beautiful animated header with 3D effect
    st.markdown("""
        <div class="perspective-container">
            <div class="main-header">
                <h1>🚀 AI Sales Intelligence CRM</h1>
                <p>Transform prospects into qualified leads with AI-powered enrichment</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Modern sidebar with gradient styling
    with st.sidebar:
        # User Profile Section
        st.markdown(f"""
            <div style="background: linear-gradient(135deg, rgba(102,126,234,0.3) 0%, rgba(118,75,162,0.3) 100%); 
                        padding: 20px; border-radius: 16px; margin-bottom: 20px; text-align: center;">
                <div style="font-size: 2.5rem; margin-bottom: 5px;">👤</div>
                <div style="color: white; font-weight: 600; font-size: 1.1rem;">{st.session_state.user_name}</div>
                <div style="color: rgba(255,255,255,0.7); font-size: 0.85rem;">{st.session_state.user_email}</div>
                <div style="color: rgba(255,255,255,0.5); font-size: 0.75rem; margin-top: 5px;">
                    via {st.session_state.auth_method}
                </div>
            </div>
        """, unsafe_allow_html=True)
        
        # Logout button
        if st.button("🚪 Logout", use_container_width=True, type="secondary"):
            logout_user()
            st.rerun()
        
        st.markdown("<hr style='border-color: rgba(255,255,255,0.3);'>", unsafe_allow_html=True)
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

    # Welcome card with features - Apple-style 3D
    st.markdown("""
        <div class="feature-card">
            <div style="display: flex; gap: 2rem; flex-wrap: wrap;">
                <div style="flex: 1; min-width: 200px; transform: translateZ(20px);">
                    <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🤖</div>
                    <h4 style="color: #667eea; margin: 0 0 0.5rem 0; font-weight: 600;">AI-Powered</h4>
                    <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">NVIDIA AI analyzes company websites to extract insights</p>
                </div>
                <div style="flex: 1; min-width: 200px; transform: translateZ(20px);">
                    <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🌐</div>
                    <h4 style="color: #667eea; margin: 0 0 0.5rem 0; font-weight: 600;">Web Scraping</h4>
                    <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">Firecrawl automatically finds and scrapes company websites</p>
                </div>
                <div style="flex: 1; min-width: 200px; transform: translateZ(20px);">
                    <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">📊</div>
                    <h4 style="color: #667eea; margin: 0 0 0.5rem 0; font-weight: 600;">Lead Scoring</h4>
                    <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">Automatic lead scoring from 1-10 with status tags</p>
                </div>
                <div style="flex: 1; min-width: 200px; transform: translateZ(20px);">
                    <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🚀</div>
                    <h4 style="color: #667eea; margin: 0 0 0.5rem 0; font-weight: 600;">CRM Integration</h4>
                    <p style="color: #6c757d; margin: 0; font-size: 0.9rem;">One-click push to Airtable CRM</p>
                </div>
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

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        start_button = st.button("🚀 Enrich All", type="primary", width='stretch')
    with col2:
        quick_mode = st.toggle("⚡ Quick Mode", value=False, help="Faster AI analysis with reduced text (~2x faster, still gives full results)")

    if start_button:
        # Reset state
        st.session_state.results = []
        st.session_state.processing_complete = False

        # Create progress bar and status placeholder
        progress_bar = st.progress(0)
        status_text = st.empty()
        results_container = st.container()

        # Process companies in parallel for faster results
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = min(5, len(companies)) if quick_mode else min(3, len(companies))
        mode_text = "QUICK MODE" if quick_mode else "Full AI Analysis"
        status_text.text(f"[{mode_text}] Processing {len(companies)} companies in parallel ({max_workers} at a time)...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks with quick_mode flag
            future_to_company = {executor.submit(process_company, company, quick_mode): company for company in companies}
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

                        table_data.append({
                            "Company": r.get("company_name", ""),
                            "Website": r.get("url", ""),
                            "Industry": r.get("industry", ""),
                            "Size": r.get("size_estimate", ""),
                            "B2B": b2b_display,
                            "Score": r.get("lead_score", 0),
                            "Status": status_display,
                            "Reason": r.get("score_reason", "")[:80] + "..." if len(r.get("score_reason", "")) > 80 else r.get("score_reason", ""),
                            "Headcount W1": r.get("Headcount W1", 0),
                            "Headcount W4": r.get("Headcount W4", 0),
                            "Growth %": r.get("Growth Rate %", 0),
                            "Growth": r.get("Growth Label", ""),
                            "Error": error_msg[:50] + "..." if error_msg and len(error_msg) > 50 else error_msg,
                        })

                    df_display = pd.DataFrame(table_data)
                    st.dataframe(
                        df_display,
                        use_container_width=True,
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
                            "Error": st.column_config.TextColumn("Error", width="medium"),
                        }
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
    # RESULTS SECTION (shown after processing) - UNIFIED SINGLE TABLE
    # =========================================================================
    if st.session_state.results and st.session_state.processing_complete:
        st.markdown('<div class="section-header">📊 Enrichment Results</div>', unsafe_allow_html=True)

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
            <div style="display: flex; gap: 20px; margin-bottom: 20px;">
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 15px 25px; border-radius: 12px; color: white; text-align: center; flex: 1;">
                    <div style="font-size: 1.8rem; font-weight: 600;">{total}</div>
                    <div style="font-size: 0.9rem; opacity: 0.9;">Total</div>
                </div>
                <div style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%); padding: 15px 25px; border-radius: 12px; color: white; text-align: center; flex: 1;">
                    <div style="font-size: 1.8rem; font-weight: 600;">{successful}</div>
                    <div style="font-size: 0.9rem; opacity: 0.9;">Success</div>
                </div>
                <div style="background: linear-gradient(135deg, #dc3545 0%, #fd7e14 100%); padding: 15px 25px; border-radius: 12px; color: white; text-align: center; flex: 1;">
                    <div style="font-size: 1.8rem; font-weight: 600;">{failed}</div>
                    <div style="font-size: 0.9rem; opacity: 0.9;">Failed</div>
                </div>
                <div style="background: linear-gradient(135deg, #ff6b6b 0%, #feca57 100%); padding: 15px 25px; border-radius: 12px; color: white; text-align: center; flex: 1;">
                    <div style="font-size: 1.8rem; font-weight: 600;">🔥 {hot_count}</div>
                    <div style="font-size: 0.9rem; opacity: 0.9;">Hot</div>
                </div>
                <div style="background: linear-gradient(135deg, #feca57 0%, #48dbfb 100%); padding: 15px 25px; border-radius: 12px; color: white; text-align: center; flex: 1;">
                    <div style="font-size: 1.8rem; font-weight: 600;">🌟 {warm_count}</div>
                    <div style="font-size: 0.9rem; opacity: 0.9;">Warm</div>
                </div>
                <div style="background: linear-gradient(135deg, #48dbfb 0%, #0abde3 100%); padding: 15px 25px; border-radius: 12px; color: white; text-align: center; flex: 1;">
                    <div style="font-size: 1.8rem; font-weight: 600;">❄️ {cold_count}</div>
                    <div style="font-size: 0.9rem; opacity: 0.9;">Cold</div>
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

            # B2B Buyer checkmark
            b2b = r.get("b2b_buyer", False)
            b2b_display = "✅ Yes" if b2b else "❌ No"

            # Error status
            error_msg = r.get("error", "")
            status_display = f"{status_icon} {status}" if not error_msg else f"❌ Error"

            table_data.append({
                "Company": r.get("company_name", ""),
                "Website": r.get("url", ""),
                "Industry": r.get("industry", ""),
                "Size": r.get("size_estimate", ""),
                "B2B": b2b_display,
                "Score": r.get("lead_score", 0),
                "Status": status_display,
                "Reason": r.get("score_reason", "")[:80] + "..." if len(r.get("score_reason", "")) > 80 else r.get("score_reason", ""),
                "Headcount W1": r.get("Headcount W1", 0),
                "Headcount W4": r.get("Headcount W4", 0),
                "Growth %": r.get("Growth Rate %", 0),
                "Growth": r.get("Growth Label", ""),
                "Error": error_msg[:50] + "..." if error_msg and len(error_msg) > 50 else error_msg,
            })

        df_display = pd.DataFrame(table_data)

        # Display unified table
        st.dataframe(
            df_display,
            use_container_width=True,
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
                "Error": st.column_config.TextColumn("Error", width="medium"),
            }
        )

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
