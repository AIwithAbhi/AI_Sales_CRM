"""AI analysis module using NVIDIA API for company enrichment."""

import json
import os
from typing import Any, Dict

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

Return ONLY valid JSON. No markdown. No explanation. No code blocks."""

# Default fallback response when AI analysis fails
DEFAULT_ANALYSIS = {
    "summary": "Unable to analyze company information.",
    "industry": "Other",
    "size_estimate": "1-10 employees",
    "b2b_buyer": False,
    "lead_score": 0,
    "score_reason": "Analysis failed - no data available.",
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
            "model": "meta/llama-3.1-405b-instruct",
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
                "b2b_buyer", "lead_score", "score_reason"
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
