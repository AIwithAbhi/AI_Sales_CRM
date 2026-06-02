"""AI analysis module using NVIDIA API for company enrichment."""

import json
import os
from typing import Any, Dict, List

import requests
from utils.helpers import retry

# NVIDIA API endpoint
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# System prompt for AI analysis
SYSTEM_PROMPT = """You are a B2B sales intelligence analyst. Analyze the given company homepage text and return a JSON object with these exact fields:
- summary: string (exactly 2 sentences describing what the company does)
- industry: string (one of: Energy, Technology, Finance, Healthcare, Manufacturing, Retail, Consulting, Real Estate, Other)
- size_estimate: string (one of: "1-10 employees", "11-50 employees", "51-200 employees", "200+ employees")
- b2b_buyer: boolean (true if this company likely purchases B2B software tools)
- lead_score: integer between 1 and 10 (scoring criteria: companies in Energy, Technology, or Manufacturing sectors score higher; companies with 51+ employees score higher; B2B buyers score higher; companies with rapid LinkedIn headcount growth score higher)
- score_reason: string (one sentence explaining the lead score)
- headquarters: string (company headquarters location - look for mentions of "headquarters", "HQ", "office", "based in", "located in")
- country: string (country where company is headquartered - look for country names in location mentions)
- phone: string (public company phone number - look for phone numbers in contact sections, footers, or "Contact Us" areas)
- email: string (public contact email - look for email addresses in contact sections, footers, or "Contact Us" areas)
- linkedin: string (LinkedIn company page URL - look for LinkedIn links or construct as https://www.linkedin.com/company/[company-name] if not found)
- contact_page: string (URL to contact page - look for "Contact", "Contact Us", "Get in Touch" links)
- contact_reason: string (one sentence explaining why this company is worth contacting based on their industry, market presence, and business model)

IMPORTANT: 
- Actively search for contact information throughout the entire text, including footers, headers, and contact sections
- Look for phone numbers in various formats: +1-xxx-xxx-xxxx, (xxx) xxx-xxxx, xxx-xxx-xxxx, etc.
- Look for email addresses in contact sections and footers
- If a LinkedIn URL is not explicitly mentioned, construct a reasonable guess based on the company name
- If a contact page URL is not explicitly mentioned, look for relative paths like "/contact" or "/contact-us" and construct the full URL
- If information is not available after thorough search, use empty string "".

Return ONLY valid JSON. No markdown. No explanation. No code blocks."""

# Default fallback response when AI analysis fails
DEFAULT_ANALYSIS = {
    "summary": "Unable to analyze company information.",
    "industry": "Other",
    "size_estimate": "1-10 employees",
    "b2b_buyer": False,
    "lead_score": 0,
    "score_reason": "Analysis failed - no data available.",
    "headquarters": "",
    "country": "",
    "phone": "",
    "email": "",
    "linkedin": "",
    "contact_page": "",
    "contact_reason": "",
}


@retry(max_attempts=2, delay=2.0)
def analyze_company(company_name: str, homepage_text: str, headcount_context: str = "") -> Dict[str, Any]:
    """
    Analyze a company using NVIDIA API (Llama 3.1 405B Instruct).

    Sends the company name and homepage text to NVIDIA API for analysis.
    Returns structured data including summary, industry, size estimate,
    B2B buyer likelihood, and lead score.

    Args:
        company_name: Name of the company to analyze.
        homepage_text: Text content scraped from the company's homepage.
        headcount_context: Optional LinkedIn headcount trend information.

    Returns:
        Dictionary containing:
        - summary: 2-sentence description
        - industry: One of 9 predefined industries
        - size_estimate: Employee count range
        - b2b_buyer: Boolean for B2B software purchase likelihood
        - lead_score: Integer 1-10
        - score_reason: One-sentence explanation

    Note:
        Uses retry logic (2 attempts with 2s delay) on API errors.
        Returns default fallback dict on JSON parse failure.
    """
    try:
        # Get API key from environment
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            print("Error: NVIDIA_API_KEY not set in environment")
            return DEFAULT_ANALYSIS.copy()

        # Build user message with company data
        user_message = f"Company: {company_name}\n\nHomepage text:\n{homepage_text}"
        
        # Add headcount context if provided
        if headcount_context:
            user_message += f"\n\nAdditional context: {headcount_context}"

        # Prepare request payload
        payload = {
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 1024,
            "temperature": 0,
        }

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Make API call
        response = requests.post(
            NVIDIA_API_URL,
            headers=headers,
            json=payload,
            timeout=180,
        )

        # Check for HTTP errors
        response.raise_for_status()

        # Extract response text from NVIDIA API response
        response_data = response.json()
        response_text = response_data["choices"][0]["message"]["content"]

        # Parse JSON response
        try:
            # Strip markdown code blocks if present
            cleaned_text = response_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:].strip()
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:].strip()
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3].strip()
            
            print(f"AI response for '{company_name}': {cleaned_text[:200]}...")
            result = json.loads(cleaned_text)

            # Validate required fields exist
            required_fields = [
                "summary", "industry", "size_estimate",
                "b2b_buyer", "lead_score", "score_reason",
                "headquarters", "country", "phone", "email",
                "linkedin", "contact_page", "contact_reason"
            ]

            for field in required_fields:
                if field not in result:
                    print(f"Missing field '{field}' in AI response")
                    return DEFAULT_ANALYSIS.copy()

            # Validate lead_score is in valid range
            lead_score = result.get("lead_score", 0)
            if not isinstance(lead_score, int) or lead_score < 0 or lead_score > 10:
                result["lead_score"] = 0

            return result

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            print(f"Raw response: {response_text[:200]}...")
            return DEFAULT_ANALYSIS.copy()

    except requests.exceptions.Timeout:
        print(f"NVIDIA API timeout for '{company_name}' after all retries")
        return DEFAULT_ANALYSIS.copy()

    except requests.exceptions.RequestException as e:
        print(f"NVIDIA API request error for '{company_name}': {e}")
        return DEFAULT_ANALYSIS.copy()

    except Exception as e:
        print(f"Analysis error for '{company_name}': {e}")
        return DEFAULT_ANALYSIS.copy()


@retry(max_attempts=2, delay=2.0)
def generate_icp(company_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate Ideal Customer Profile (ICP) from analyzed company data using NVIDIA LLM.

    Analyzes patterns across all uploaded companies to identify:
    - Industry patterns
    - Business model patterns
    - Customer segments
    - Company size patterns
    - Geographic patterns
    - Operational complexity
    - Regulatory characteristics

    Args:
        company_summaries: List of company analysis results containing:
            - company_name: str
            - summary: str
            - industry: str
            - size_estimate: str
            - b2b_buyer: bool
            - lead_score: int

    Returns:
        Dictionary containing:
        - icp_summary: str (2-3 sentence summary of the detected ICP)
        - key_characteristics: list of str (key characteristics of the ICP)
        - target_industries: list of str (industries that match the ICP)
        - target_size: str (company size that matches the ICP)
        - business_model: str (detected business model pattern)
        - customer_segment: str (detected customer segment)
        - geographic_focus: str (geographic pattern if detected)
        - regulatory_requirements: str (regulatory characteristics if detected)

    Note:
        Uses retry logic (2 attempts with 2s delay) on API errors.
        Returns default fallback dict on failure.
    """
    try:
        # Get API key from environment
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            print("Error: NVIDIA_API_KEY not set in environment")
            return {"icp_summary": "Unable to generate ICP - API key not configured."}

        # Build company summaries for analysis
        company_data = []
        for company in company_summaries:
            company_data.append({
                "name": company.get("company_name", ""),
                "industry": company.get("industry", ""),
                "size": company.get("size_estimate", ""),
                "b2b_buyer": company.get("b2b_buyer", False),
                "summary": company.get("summary", ""),
                "lead_score": company.get("lead_score", 0)
            })

        # Build user message with company data
        user_message = f"""Analyze these {len(company_data)} companies and identify patterns to create an Ideal Customer Profile (ICP):

"""
        for company in company_data:
            user_message += f"""
- {company['name']}: Industry={company['industry']}, Size={company['size']}, B2B={company['b2b_buyer']}, Summary={company['summary']}
"""

        user_message += """

Based on this analysis, return a JSON object with these exact fields:
- icp_summary: string (2-3 sentence summary describing the ideal customer profile based on detected patterns)
- key_characteristics: list of strings (5-7 key characteristics that define this ICP)
- target_industries: list of strings (industries that match this ICP)
- target_size: string (company size range that matches this ICP)
- business_model: string (detected business model pattern - e.g., SaaS, Marketplace, Platform, Service, etc.)
- customer_segment: string (detected customer segment - e.g., Enterprise, SMB, Mid-market, etc.)
- geographic_focus: string (geographic pattern if detected - e.g., Global, US-focused, Europe-focused, etc.)
- regulatory_requirements: string (regulatory characteristics if detected - e.g., Highly regulated, Lightly regulated, etc.)

IMPORTANT:
- Do NOT hardcode any specific industry or pattern
- Analyze the actual data to detect patterns
- If a pattern is not clear, use "Mixed" or "Varied"
- Return ONLY valid JSON. No markdown. No explanation. No code blocks.
"""

        # Prepare request payload
        payload = {
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
            "messages": [
                {"role": "system", "content": "You are an expert B2B sales strategist specializing in Ideal Customer Profile (ICP) analysis. Analyze company data to identify patterns and create accurate ICPs."},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 1024,
            "temperature": 0.3,
        }

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Make API call
        response = requests.post(
            NVIDIA_API_URL,
            headers=headers,
            json=payload,
            timeout=180,
        )

        # Check for HTTP errors
        response.raise_for_status()

        # Extract response text from NVIDIA API response
        response_data = response.json()
        response_text = response_data["choices"][0]["message"]["content"]

        # Parse JSON response
        try:
            # Strip markdown code blocks if present
            cleaned_text = response_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:].strip()
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:].strip()
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3].strip()
            
            print(f"ICP generation response: {cleaned_text[:200]}...")
            result = json.loads(cleaned_text)

            # Validate required fields exist
            required_fields = [
                "icp_summary", "key_characteristics", "target_industries",
                "target_size", "business_model", "customer_segment",
                "geographic_focus", "regulatory_requirements"
            ]

            for field in required_fields:
                if field not in result:
                    print(f"Missing field '{field}' in ICP response")
                    result[field] = ""

            return result

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            print(f"Raw response: {response_text[:200]}...")
            return {"icp_summary": "Unable to generate ICP - JSON parse error."}

    except requests.exceptions.Timeout:
        print("NVIDIA API timeout for ICP generation after all retries")
        return {"icp_summary": "Unable to generate ICP - API timeout."}

    except requests.exceptions.RequestException as e:
        print(f"NVIDIA API request error for ICP generation: {e}")
        return {"icp_summary": "Unable to generate ICP - API error."}

    except Exception as e:
        print(f"ICP generation error: {e}")
        return {"icp_summary": "Unable to generate ICP - unknown error."}


@retry(max_attempts=2, delay=2.0)
def recommend_companies(icp: Dict[str, Any], num_recommendations: int = 5) -> List[Dict[str, Any]]:
    """
    Recommend companies that match the generated ICP using NVIDIA LLM.

    Uses the ICP to identify real companies that match the detected profile.
    The AI determines similarity dynamically without hardcoding industries.

    Args:
        icp: Dictionary containing the ICP data from generate_icp()
        num_recommendations: Number of company recommendations to generate (default: 5)

    Returns:
        List of dictionaries containing:
        - company_name: str
        - website: str
        - industry: str
        - description: str
        - similarity_score: int (1-10 score indicating how well it matches the ICP)
        - match_reason: str (explanation of why this company matches the ICP)

    Note:
        Uses retry logic (2 attempts with 2s delay) on API errors.
        Returns empty list on failure.
    """
    try:
        # Get API key from environment
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            print("Error: NVIDIA_API_KEY not set in environment")
            return []

        # Build user message with ICP data
        user_message = f"""Based on this Ideal Customer Profile (ICP):

ICP Summary: {icp.get('icp_summary', '')}

Key Characteristics:
{chr(10).join(f"- {char}" for char in icp.get('key_characteristics', []))}

Target Industries: {', '.join(icp.get('target_industries', []))}
Target Size: {icp.get('target_size', '')}
Business Model: {icp.get('business_model', '')}
Customer Segment: {icp.get('customer_segment', '')}
Geographic Focus: {icp.get('geographic_focus', '')}
Regulatory Requirements: {icp.get('regulatory_requirements', '')}

Recommend {num_recommendations} real companies that strongly match this ICP.

For each recommendation, provide:
- Company Name
- Website URL
- Industry
- Brief Description
- Similarity Score (1-10, where 10 is a perfect match)
- Match Reason (1-2 sentences explaining why this company matches the ICP)

IMPORTANT:
- Recommend REAL companies that actually exist
- Do NOT hardcode any specific industry
- Determine similarity dynamically based on the ICP
- Return ONLY valid JSON as a list of objects.
- No markdown. No explanation. No code blocks.

Return format:
[
  {{
    "company_name": "Company Name",
    "website": "https://example.com",
    "industry": "Industry",
    "description": "Brief description",
    "similarity_score": 8,
    "match_reason": "Explanation of why it matches"
  }},
  ...
]
"""

        # Prepare request payload
        payload = {
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
            "messages": [
                {"role": "system", "content": "You are an expert B2B sales researcher with deep knowledge of companies across all industries. Recommend real companies that match a given Ideal Customer Profile."},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 2048,
            "temperature": 0.5,
        }

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Make API call
        response = requests.post(
            NVIDIA_API_URL,
            headers=headers,
            json=payload,
            timeout=180,
        )

        # Check for HTTP errors
        response.raise_for_status()

        # Extract response text from NVIDIA API response
        response_data = response.json()
        response_text = response_data["choices"][0]["message"]["content"]

        # Parse JSON response
        try:
            # Strip markdown code blocks if present
            cleaned_text = response_text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:].strip()
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:].strip()
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3].strip()
            
            print(f"Company recommendations response: {cleaned_text[:200]}...")
            result = json.loads(cleaned_text)

            # Validate it's a list
            if not isinstance(result, list):
                print(f"Expected list, got {type(result)}")
                return []

            # Validate each recommendation has required fields
            required_fields = [
                "company_name", "website", "industry", "description",
                "similarity_score", "match_reason"
            ]

            for rec in result:
                for field in required_fields:
                    if field not in rec:
                        print(f"Missing field '{field}' in recommendation")
                        rec[field] = ""

            return result

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            print(f"Raw response: {response_text[:200]}...")
            return []

    except requests.exceptions.Timeout:
        print("NVIDIA API timeout for company recommendations after all retries")
        return []

    except requests.exceptions.RequestException as e:
        print(f"NVIDIA API request error for company recommendations: {e}")
        return []

    except Exception as e:
        print(f"Company recommendations error: {e}")
        return []
