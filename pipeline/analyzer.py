"""AI analysis module using NVIDIA API for company search."""

import json
import os
from typing import Any, Dict, List

import requests
from utils.helpers import retry

# NVIDIA API endpoint
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# System prompt for AI analysis — fact-checked, no hallucinations
SYSTEM_PROMPT = """You are a B2B SaaS sales expert. Analyze ONLY what's visible on the website text provided.
Do NOT guess. If you can't find something, use "Not stated on website", false, or an empty list.

BUYING SIGNALS TO FIND:
- Does the site mention "AI", "automation", "digital transformation"?
- Is there a careers page? If so, are they hiring engineers/developers?
- Do they list case studies, customer logos, or success stories?
- Is there a blog with recent posts (sign of an active company)?
- What's the business model (B2B, B2C, B2B2C)?

EXTRACT and return ONLY a JSON object with these exact fields:
- summary: string (exactly 2 sentences from page facts only; if unknown: "Not stated on website")
- industry: string (exact industry from their website; if unclear use one of:
  Energy, Technology, Finance, Healthcare, Manufacturing, Retail, Consulting, Real Estate, Other;
  if not stated on the page: "Unknown")
- size_estimate: string (one of: "1-50", "51-200", "201-500", "501-1000", "1001+", "Unknown")
  If employee count is stated, map to the band. If size is not clearly supported by the page,
  return "Unknown" — never invent "1-50" as a default.
- b2b_buyer: boolean (true only with page evidence they sell to / buy for businesses; false only
  with page evidence they are purely consumer; if unclear omit evidence and set false with
  b2b_evidence "Not stated on website")
- b2b_evidence: string (one concrete phrase from the page, or "Not stated on website")
- business_model: string (one of: "B2B", "B2C", "B2B2C", "Not stated on website")
- buying_signals: array of specific strings found on the page only. Prefer labels like:
  "mentions AI", "mentions automation", "mentions digital transformation",
  "careers page", "hiring engineers", "case studies", "customer logos",
  "success stories", "active blog", "enterprise pricing"
  Empty array [] if none found. Max 8.
- lead_score_rationale: string (ONE concrete fact from the page that justifies outreach — not vague language.
  Same value is also used as score_reason.)
- score_reason: string (must match lead_score_rationale)
- confidence: string (one of: "HIGH", "MEDIUM", "LOW")
  HIGH = industry + B2B/model + size signals clearly stated;
  MEDIUM = some signals present but gaps;
  LOW = mostly "Not stated on website" or "Unknown"
- headquarters: string (only if stated on page, else "Not stated on website")
- country: string (only if stated on page, else "Not stated on website")
- phone: string (public phone on page, else "Not stated on website")
- email: string (public email on page, else "Not stated on website")
- linkedin: string (LinkedIn URL only if present on page, else "Not stated on website")
- contact_page: string (contact URL if present, else "Not stated on website")
- contact_reason: string (one sentence citing a concrete page fact, or "Not stated on website")

CRITICAL:
- Analyze ONLY what's visible on the website.
- Do NOT invent facts. Do NOT guess.
- Prefer "Not stated on website" / "Unknown" / false / [] over guessing.
- Never invent a size band. If size is unclear, use "Unknown".
- Do NOT output lead_score — scoring is computed separately from signals.

Return ONLY valid JSON. No markdown. No explanation. No code blocks."""

# Fallback when AI analysis fails — honest Unknowns, never fake size/industry facts
DEFAULT_ANALYSIS = {
    "summary": "Analysis unavailable",
    "industry": "Unknown",
    "size_estimate": "Unknown",
    "b2b_buyer": None,
    "b2b_evidence": "Unknown",
    "business_model": "Unknown",
    "buying_signals": [],
    "lead_score": None,
    "lead_score_rationale": "Analysis failed - insufficient data.",
    "score_reason": "Analysis failed - insufficient data.",
    "confidence": "LOW",
    "headquarters": "Unknown",
    "country": "Unknown",
    "phone": "Unknown",
    "email": "Unknown",
    "linkedin": "Unknown",
    "contact_page": "Unknown",
    "contact_reason": "Unknown",
    "analysis_failed": True,
    "analysis_error": "Analysis failed - insufficient data.",
}


def _failed_analysis(reason: str) -> Dict[str, Any]:
    """Return honest Unknown defaults with a specific failure reason."""
    result = DEFAULT_ANALYSIS.copy()
    result["analysis_failed"] = True
    result["analysis_error"] = reason
    result["lead_score_rationale"] = reason
    result["score_reason"] = reason
    result["summary"] = reason
    return result


def probe_nvidia_api() -> Dict[str, Any]:
    """
    Minimal live NVIDIA call for health checks.

    Returns {ok: bool, error?: str, status_code?: int}.
    """
    api_key = (os.getenv("NVIDIA_API_KEY") or "").strip()
    if not api_key:
        return {"ok": False, "error": "NVIDIA_API_KEY not set"}
    low = api_key.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return {"ok": False, "error": "NVIDIA_API_KEY looks like a placeholder"}
    try:
        response = requests.post(
            NVIDIA_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "max_tokens": 4,
                "temperature": 0,
            },
            timeout=20,
        )
        if response.status_code == 401:
            return {
                "ok": False,
                "status_code": 401,
                "error": "NVIDIA API rejected the key (401)",
            }
        if response.status_code >= 400:
            return {
                "ok": False,
                "status_code": response.status_code,
                "error": f"NVIDIA API error ({response.status_code})",
            }
        return {"ok": True, "status_code": response.status_code}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "NVIDIA API timeout"}
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "error": f"NVIDIA API request failed: {exc}"}


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
            return _failed_analysis("NVIDIA_API_KEY not set")
        low = api_key.strip().lower()
        if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
            return _failed_analysis("NVIDIA API rejected the key (401)")

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

        print(f"[*] NVIDIA API: Analyzing {company_name}")

        # Make API call
        response = requests.post(
            NVIDIA_API_URL,
            headers=headers,
            json=payload,
            timeout=180,
        )

        if response.status_code == 401:
            print(f"[ERROR] NVIDIA API rejected the key (401) for '{company_name}'")
            return _failed_analysis("NVIDIA API rejected the key (401)")

        # Check for HTTP errors
        response.raise_for_status()

        # Extract response text from NVIDIA API response
        response_data = response.json()
        response_text = response_data["choices"][0]["message"]["content"]

        print(f"[*] NVIDIA API: Response received for {company_name}")

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

            # Validate response doesn't contain error indicators
            error_indicators = ["state", "errorType", "error", "exception", "traceback", "failed"]
            for indicator in error_indicators:
                if indicator in result:
                    print(f"[ERROR] NVIDIA API returned error response with '{indicator}': {result}")
                    return _failed_analysis("NVIDIA API returned an error payload")

            # Normalize rationale / optional fields before required checks
            rationale = (
                str(result.get("lead_score_rationale") or "").strip()
                or str(result.get("score_reason") or "").strip()
            )
            if rationale:
                result["lead_score_rationale"] = rationale
                result["score_reason"] = rationale

            # Validate required fields exist (lead_score is computed separately)
            required_fields = [
                "summary", "industry", "size_estimate", "b2b_buyer",
            ]
            for field in required_fields:
                if field not in result:
                    print(f"Missing field '{field}' in AI response")
                    return _failed_analysis(
                        f"Analysis response missing required field '{field}'"
                    )

            if not result.get("score_reason"):
                result["score_reason"] = "Not stated on website"
                result["lead_score_rationale"] = "Not stated on website"

            # Normalize types — allow null-ish B2B only when explicitly unknown
            if result.get("b2b_buyer") is None:
                result["b2b_buyer"] = None
            else:
                result["b2b_buyer"] = bool(result.get("b2b_buyer", False))
            signals = result.get("buying_signals") or []
            if isinstance(signals, str):
                signals = [s.strip() for s in signals.split(",") if s.strip()]
            result["buying_signals"] = list(signals)[:8]
            result["b2b_evidence"] = str(
                result.get("b2b_evidence") or "Not stated on website"
            )
            result["business_model"] = str(
                result.get("business_model") or "Not stated on website"
            )
            conf = str(result.get("confidence") or "LOW").strip().upper()
            if conf not in ("HIGH", "MEDIUM", "LOW"):
                conf = "LOW"
            result["confidence"] = conf

            # Fill contact-style fields with explicit non-guess default
            for key in (
                "headquarters", "country", "phone", "email",
                "linkedin", "contact_page", "contact_reason", "summary",
            ):
                if not str(result.get(key) or "").strip():
                    result[key] = "Not stated on website"

            # Map free-text industry; Unknown when not stated
            industry = str(result.get("industry") or "").strip()
            known = {
                "Energy", "Technology", "Finance", "Healthcare",
                "Manufacturing", "Retail", "Consulting", "Real Estate", "Other",
                "Unknown",
            }
            if industry.lower() in ("not stated on website", "n/a", "none", ""):
                result["industry"] = "Unknown"
                if result["confidence"] == "HIGH":
                    result["confidence"] = "MEDIUM"
            elif industry not in known:
                # Keep free-text from website for display
                pass

            size = str(result.get("size_estimate") or "").strip()
            allowed_sizes = {
                "1-50", "51-200", "201-500", "501-1000", "1001+", "Unknown",
            }
            if size not in allowed_sizes:
                # Do not invent 1-50 — treat unrecognized as Unknown
                result["size_estimate"] = "Unknown"

            result["lead_score"] = None
            result["analysis_failed"] = False
            result["analysis_error"] = ""

            print(f"[OK] NVIDIA API: Successfully analyzed {company_name}")
            return result

        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON parse error for {company_name}: {e}")
            print(f"Raw response: {response_text[:200]}...")
            return _failed_analysis("Analysis JSON parse failed")

    except requests.exceptions.Timeout:
        print(f"[ERROR] NVIDIA API timeout for '{company_name}' after all retries")
        return _failed_analysis("NVIDIA API timeout")

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] NVIDIA API request error for '{company_name}': {e}")
        resp = getattr(e, "response", None)
        if resp is not None and getattr(resp, "status_code", None) == 401:
            return _failed_analysis("NVIDIA API rejected the key (401)")
        return _failed_analysis(f"NVIDIA API request error: {e}")

    except Exception as e:
        print(f"[ERROR] Analysis error for '{company_name}': {e}")
        return _failed_analysis(f"Analysis error: {e}")


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
- target_size: string (one of: "1-50", "51-200", "201-500", "501-1000", "1001+", or "Mixed")
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

        print(f"[*] NVIDIA API: Generating {num_recommendations} company recommendations")

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

        print(f"[*] NVIDIA API: Recommendations response received")

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
                print(f"[ERROR] Expected list, got {type(result)}")
                return []

            # Validate response doesn't contain error indicators
            error_indicators = ["state", "errorType", "error", "exception", "traceback", "failed"]
            for item in result:
                if isinstance(item, dict):
                    for indicator in error_indicators:
                        if indicator in item:
                            print(f"[ERROR] Recommendation contains error indicator '{indicator}': {item}")
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

            print(f"[OK] NVIDIA API: Successfully generated {len(result)} recommendations")
            return result

        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON parse error: {e}")
            print(f"Raw response: {response_text[:200]}...")
            return []

    except requests.exceptions.Timeout:
        print("[ERROR] NVIDIA API timeout for company recommendations after all retries")
        return []

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] NVIDIA API request error for company recommendations: {e}")
        return []

    except Exception as e:
        print(f"[ERROR] Company recommendations error: {e}")
        return []
