"""AI analysis module using NVIDIA API for company search."""

import json
import os
from typing import Any, Dict, List, Optional, Sequence

import requests
from utils.helpers import retry
from utils.scoring_profiles import (
    apply_heuristic_profile_scores,
    build_system_prompt,
    normalize_profile_scores,
    resolve_profile_ids,
)

# NVIDIA API endpoint
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def _clamp_score_1_10(value: Any, default: int = 1) -> int:
    """Coerce AI score fields to an int in 1..10."""
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(1, min(10, score))


def _normalize_scorecard_confidence(value: Any, default: str = "low") -> str:
    """Normalize scorecard confidence to lowercase high|medium|low."""
    text = str(value or "").strip().lower()
    if text in ("high", "medium", "low"):
        return text
    return default


def _nvidia_key_usable() -> Optional[str]:
    """Return NVIDIA API key only if it looks like a real credential."""
    key = (os.getenv("NVIDIA_API_KEY") or "").strip()
    if not key:
        return None
    low = key.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return None
    return key


def _build_icp_from_companies(company_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Deterministic ICP from scored companies when the NVIDIA API is unavailable.

    Uses Hot/Warm (or highest-scoring) companies to infer industries, size, and traits.
    """
    if not company_summaries:
        return {
            "icp_summary": "No successful companies available to build an ICP.",
            "key_characteristics": [],
            "target_industries": [],
            "target_size": "Mixed",
            "business_model": "Varied",
            "customer_segment": "Varied",
            "geographic_focus": "Varied",
            "regulatory_requirements": "Varied",
            "source": "heuristic",
        }

    ranked = sorted(
        company_summaries,
        key=lambda c: (
            0 if str(c.get("status_tag") or "").lower() == "hot" else
            1 if str(c.get("status_tag") or "").lower() == "warm" else 2,
            -(int(c.get("lead_score") or 0)),
        ),
    )
    strong = [
        c for c in ranked
        if str(c.get("status_tag") or "").lower() in ("hot", "warm")
        or int(c.get("lead_score") or 0) >= 5
    ] or ranked[: max(1, min(5, len(ranked)))]

    from collections import Counter

    industries = Counter(
        str(c.get("industry") or "").strip()
        for c in strong
        if str(c.get("industry") or "").strip()
        and str(c.get("industry")).lower() not in ("other", "not stated on website")
    )
    sizes = Counter(
        str(c.get("size_estimate") or "").strip()
        for c in strong
        if str(c.get("size_estimate") or "").strip()
    )
    models = Counter(
        str(c.get("business_model") or "").strip()
        for c in strong
        if str(c.get("business_model") or "").strip()
        and "not stated" not in str(c.get("business_model")).lower()
    )
    b2b_count = sum(1 for c in strong if c.get("b2b_buyer"))
    names = [str(c.get("company_name") or "").strip() for c in strong if c.get("company_name")]

    top_industries = [i for i, _ in industries.most_common(5)] or ["Mixed"]
    top_size = sizes.most_common(1)[0][0] if sizes else "Mixed"
    top_model = models.most_common(1)[0][0] if models else "Varied"
    segment = (
        "Enterprise" if top_size in ("501-1000", "1001+") else
        "Mid-market" if top_size in ("51-200", "201-500") else
        "SMB" if top_size == "1-50" else
        "Mixed"
    )
    b2b_label = "Primarily B2B" if b2b_count >= max(1, len(strong) // 2 + 1) else "Mixed B2B/B2C"

    chars = [
        f"Strong-fit examples: {', '.join(names[:3])}" if names else "Built from uploaded company results",
        f"Common industries: {', '.join(top_industries[:3])}",
        f"Typical company size: {top_size}",
        f"Buyer profile: {b2b_label}",
        f"Business model pattern: {top_model}",
    ]
    if any(c.get("enterprise_readiness_tier") for c in strong):
        tiers = Counter(
            str(c.get("enterprise_readiness_tier") or "")
            for c in strong
            if c.get("enterprise_readiness_tier")
        )
        if tiers:
            chars.append(f"Enterprise readiness lean: {tiers.most_common(1)[0][0]}")

    summary = (
        f"Ideal customers resemble your strongest-fit companies"
        f"{(' (' + ', '.join(names[:2]) + ')') if names else ''}: "
        f"{', '.join(top_industries[:2])} organizations around size {top_size}, "
        f"with a {b2b_label.lower()} profile. "
        f"(Heuristic ICP — NVIDIA API unavailable; add a valid NVIDIA_API_KEY for AI-generated ICPs.)"
    )

    return {
        "icp_summary": summary,
        "key_characteristics": chars,
        "target_industries": top_industries,
        "target_size": top_size,
        "business_model": top_model,
        "customer_segment": segment,
        "geographic_focus": "Varied",
        "regulatory_requirements": "Varied",
        "source": "heuristic",
    }

# System prompt for AI analysis — fact-checked, no hallucinations
SYSTEM_PROMPT = """You are a B2B SaaS sales expert. Analyze ONLY what's visible on the website text provided.
Do NOT guess. If you can't find something, use "Not stated on website", false, or an empty list.

IMPORTANT DATA LIMITATION:
- You only receive homepage (or homepage-fallback) text. Careers pages, press rooms, and news articles
  are NOT scraped separately for this analysis.
- Missing evidence is NOT proof of absence. Score conservatively when signals are thin.
- When you score low because homepage content lacks visible signals, say so explicitly in the reason
  (e.g. "limited evidence available from homepage content" or "no evidence found in available content")
  — do NOT claim the company lacks a capability that simply is not visible on this page.

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
  if not stated: "Not stated on website")
- size_estimate: string (one of: "1-50", "51-200", "201-500", "501-1000", "1001+", "Unknown")
  If employee count is stated, map to the band. If not stated, estimate ONLY from hiring volume,
  customer logos, global footprint, or office mentions on the page. If there is no real size
  signal, return "Unknown" — never invent "1-50" as a default.
- b2b_buyer: boolean (true only with page evidence they sell to / buy for businesses)
- b2b_evidence: string (one concrete phrase from the page, or "Not stated on website")
- business_model: string (one of: "B2B", "B2C", "B2B2C", "Not stated on website")
- buying_signals: array of specific strings found on the page only. Prefer labels like:
  "mentions AI", "mentions automation", "mentions digital transformation",
  "careers page", "hiring engineers", "case studies", "customer logos",
  "success stories", "active blog", "enterprise pricing"
  Empty array [] if none found. Max 8.
- lead_score_rationale: string (1-2 sentences). MUST quote or closely paraphrase the SPECIFIC
  phrase/section from the scraped text that supports the lead-fit judgment
  (industry, size, and/or B2B buyer). Example: 'page says "enterprise SaaS for Fortune 500
  manufacturers" and lists "1,200 employees"'. If no specific text supports the judgment,
  say so explicitly ("no evidence found in available content" / "limited evidence available
  from homepage content"). Do NOT use generic claims like "looks like a strong lead" without
  a cited phrase. Same value is also used as score_reason.
- score_reason: string (must match lead_score_rationale)
- lead_score_confidence: string, one of "high" | "medium" | "low"
  Confidence measures EVIDENCE QUALITY for the lead-fit signals (industry, size, B2B),
  independent of how high or low the eventual lead_score will be:
  * "high" = clear, specific page phrases for industry and buyer type (and size when claimed);
    a poor-fit company can still be high-confidence when the mismatch is obvious from good content
  * "medium" = some usable signals but gaps or vague wording
  * "low" = thin/short/off-topic page or mostly "Not stated on website"
- confidence: string (one of: "HIGH", "MEDIUM", "LOW")
  Overall analysis completeness (separate from lead_score_confidence):
  HIGH = industry + B2B/model + size signals clearly stated;
  MEDIUM = some signals present but gaps;
  LOW = mostly "Not stated on website"
- headquarters: string (only if stated on page, else "Not stated on website")
- country: string (only if stated on page, else "Not stated on website")
- phone: string (public phone on page, else "Not stated on website")
- email: string (public email on page, else "Not stated on website")
- linkedin: string (LinkedIn URL only if present on page, else "Not stated on website")
- contact_page: string (contact URL if present, else "Not stated on website")
- contact_reason: string (one sentence citing a concrete page fact, or "Not stated on website")
- profile_scores: object — filled per selected scoring profile(s) appended below this base prompt.
  Shape: profile_scores["<profile_id>"]["dimensions"]["<dimension_id>"] =
    { score: 1-10, confidence: "high"|"medium"|"low", reason: string citing scraped text }

LEAD SCORE FEW-SHOT CALIBRATION (study before extracting signals for the real company;
do NOT copy these values; do NOT output lead_score — code computes it from your signals.
Expected lead_score / status_tag below are calibration targets for industry fit + size + B2B):

Example A — strong fit (expect high lead_score ~8-10, Hot; high lead_score_confidence):
Homepage text: "ForgeGrid builds B2B industrial IoT platforms for manufacturers.
Trusted by 400+ enterprise plants worldwide. About: '850 employees across 12 countries.'
Customers: Siemens-tier logos. Pricing: Enterprise plans. Careers: hiring Platform Engineers.
Tagline: 'Sold only to manufacturing and energy operators — not consumers.'"
Expected lead-fit fields:
  industry: "Manufacturing"
  size_estimate: "501-1000"
  b2b_buyer: true
  b2b_evidence: "page says 'Sold only to manufacturing and energy operators — not consumers'
    and 'B2B industrial IoT platforms'"
  buying_signals: ["customer logos", "enterprise pricing", "hiring engineers", "case studies"]
  lead_score_confidence: "high"
  score_reason: "Page cites 'B2B industrial IoT platforms for manufacturers', '850 employees across
    12 countries', and 'Sold only to manufacturing and energy operators' — clear industry, size,
    and B2B buyer evidence."
  (calibration: lead_score ~9, status_tag Hot)

Example B — poor fit (expect low lead_score ~1-4, Cold; high lead_score_confidence if mismatch is clear):
Homepage text: "SunnyPaws Pet Boutique — handmade collars & treats for local dog owners.
Shop online for consumers. About: 'Family-run shop with 4 staff.' Instagram-first brand.
No wholesale. 'We love our neighborhood customers!'"
Expected lead-fit fields:
  industry: "Retail"
  size_estimate: "1-50"
  b2b_buyer: false
  b2b_evidence: "page says 'Shop online for consumers' and 'No wholesale'"
  buying_signals: []
  lead_score_confidence: "high"
  score_reason: "Page says 'Shop online for consumers', 'No wholesale', and 'Family-run shop with
    4 staff' — clear consumer retail mismatch, not a B2B enterprise buyer."
  (calibration: lead_score ~2, status_tag Cold)

Example C — mixed signals (expect mid lead_score ~5-7, Warm; medium lead_score_confidence):
Homepage text: "Northline Services helps companies with 'operations support'.
We work with clients in finance and healthcare. Team of specialists.
Contact us to learn more. No employee count, offices, or buyer type stated beyond
'helps companies'."
Expected lead-fit fields:
  industry: "Consulting"
  size_estimate: "Unknown"
  b2b_buyer: true
  b2b_evidence: "page says 'helps companies' and clients in 'finance and healthcare'"
  buying_signals: []
  lead_score_confidence: "medium"
  score_reason: "Page mentions clients in 'finance and healthcare' and 'helps companies' (B2B hint)
    but size is Unknown — no employee count or office footprint; limited evidence available from
    homepage content for a firm size band."
  (calibration: lead_score ~6, status_tag Warm)

CRITICAL:
- Analyze ONLY what's visible on the website text provided.
- Do NOT invent facts. Do NOT guess.
- Prefer "Not stated on website" / false / [] over guessing.
- Do NOT output lead_score or status_tag — B2B lead scoring is computed separately from your signals.
- Do NOT output aggregate readiness tiers — those are computed in code from profile dimension scores.
- lead_score_confidence MUST be "high", "medium", or "low".
- score_reason / lead_score_rationale MUST reference specific scraped phrases when available.
- When scoring profiles are appended below, fill profile_scores for every selected profile.

Return ONLY valid JSON. No markdown. No explanation. No code blocks."""

# Default fallback response when AI analysis fails
DEFAULT_ANALYSIS = {
    "summary": "Not stated on website",
    "industry": "Other",
    "size_estimate": "1-50",
    "b2b_buyer": False,
    "b2b_evidence": "Not stated on website",
    "business_model": "Not stated on website",
    "buying_signals": [],
    "lead_score": 0,
    "lead_score_rationale": "Analysis failed - no data available.",
    "score_reason": "Analysis failed - no data available.",
    "lead_score_confidence": "low",
    "confidence": "LOW",
    "headquarters": "Not stated on website",
    "country": "Not stated on website",
    "phone": "Not stated on website",
    "email": "Not stated on website",
    "linkedin": "Not stated on website",
    "contact_page": "Not stated on website",
    "contact_reason": "Not stated on website",
    "ai_maturity_score": 1,
    "ai_maturity_reason": "limited evidence available from homepage content",
    "ai_maturity_confidence": "low",
    "transformation_readiness_score": 1,
    "transformation_readiness_reason": "limited evidence available from homepage content",
    "transformation_readiness_confidence": "low",
}


def _build_analysis_from_homepage(
    company_name: str,
    homepage_text: str,
    scoring_profile_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Deterministic homepage analysis when the NVIDIA API is unavailable.

    Extracts industry/size/B2B/signals from visible page text only — no guessing
    beyond keyword evidence. Used so company details stay useful without NVIDIA.
    """
    import re

    text = (homepage_text or "").strip()
    low = text.lower()
    result = DEFAULT_ANALYSIS.copy()

    if not text or text.startswith("[Scraping failed"):
        result["lead_score_rationale"] = (
            "Heuristic analysis — little homepage text available "
            "(NVIDIA API unavailable)."
        )
        result["score_reason"] = result["lead_score_rationale"]
        result["summary"] = (
            f"Limited public page text found for {company_name}. "
            "Add a valid NVIDIA_API_KEY for fuller AI analysis."
        )
        apply_heuristic_profile_scores(result, homepage_text, scoring_profile_ids)
        return result

    industry_rules = [
        (["business school", "university", "mba", "bachelor", "campus", "tuition"],
         "Education"),
        (["software", "saas", "cloud", "api", "developer", "platform"], "Technology"),
        (["bank", "fintech", "payment", "insurance", "invest"], "Finance"),
        (["hospital", "clinic", "pharma", "health", "medical"], "Healthcare"),
        (["manufactur", "factory", "industrial", "supply chain"], "Manufacturing"),
        (["retail", "ecommerce", "e-commerce", "shop", "store"], "Retail"),
        (["consult", "advisory", "professional services"], "Consulting"),
        (["real estate", "property", "housing"], "Real Estate"),
        (["energy", "oil", "gas", "solar", "renewable", "utility"], "Energy"),
    ]
    industry = "Other"
    for keywords, label in industry_rules:
        if any(k in low for k in keywords):
            industry = label
            break

    size = "1-50"
    if any(k in low for k in ("fortune 500", "10,000+", "10000+", "worldwide offices")):
        size = "1001+"
    elif any(k in low for k in ("campuses", "multiple campuses", "global offices", "employees")):
        size = "201-500"
    elif any(k in low for k in ("enterprise", "mid-market", "scale-up")):
        size = "51-200"

    b2b_keywords = (
        "b2b", "enterprise", "corporate", "executive", "business clients",
        "for business", "companies", "organizations", "mba", "professional",
    )
    b2b_hits = [k for k in b2b_keywords if k in low]
    b2b_buyer = len(b2b_hits) >= 1
    b2b_evidence = (
        f"Page mentions: {', '.join(b2b_hits[:3])}"
        if b2b_hits else "Not stated on website"
    )

    signal_map = [
        ("mentions AI", (" artificial intelligence", " ai ", "machine learning", "generative ai")),
        ("mentions automation", ("automation", "automate")),
        ("mentions digital transformation", ("digital transformation", "digital campus", "digitization")),
        ("careers page", ("careers", "join our team", "we're hiring", "we are hiring")),
        ("hiring engineers", ("software engineer", "developer roles", "engineering jobs")),
        ("case studies", ("case study", "case studies", "success story")),
        ("customer logos", ("our clients", "trusted by", "customers include")),
        ("active blog", ("blog", "insights", "newsroom", "latest articles")),
        ("enterprise pricing", ("enterprise plan", "enterprise pricing", "contact sales")),
    ]
    buying_signals: List[str] = []
    for label, keys in signal_map:
        if any(k in low for k in keys):
            buying_signals.append(label)

    model = "Not stated on website"
    if "b2b" in low and "b2c" in low:
        model = "B2B2C"
    elif b2b_buyer and any(k in low for k in ("consumer", "students", "student")):
        model = "B2B2C"
    elif b2b_buyer:
        model = "B2B"
    elif any(k in low for k in ("consumer", "students", "shop now")):
        model = "B2C"

    # Contact hints from page text
    email_match = re.search(
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        text,
        re.I,
    )
    phone_match = re.search(
        r"(?:\+?\d[\d\s().-]{7,}\d)",
        text,
    )
    linkedin_match = re.search(
        r"https?://(?:www\.)?linkedin\.com/[^\s)\"']+",
        text,
        re.I,
    )
    contact_page = ""
    if "contact" in low:
        contact_page = "Contact mentioned on homepage"

    # Short summary from first meaningful sentences
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", text)
        if len(s.strip()) > 40
    ]
    summary_bits = sentences[:2] if sentences else [text[:220].rsplit(" ", 1)[0]]
    summary = " ".join(summary_bits)[:400]
    if not summary:
        summary = f"Homepage content found for {company_name}."

    evidence_bits = [industry, size]
    if buying_signals:
        evidence_bits.append(f"{len(buying_signals)} buying signal(s)")
    rationale = (
        f"Heuristic from homepage keywords ({', '.join(evidence_bits)}). "
        "NVIDIA API unavailable — add NVIDIA_API_KEY for AI analysis."
    )

    confidence = "MEDIUM" if industry != "Other" or buying_signals else "LOW"
    # Evidence quality for lead-fit signals (independent of eventual numeric score)
    if (
        industry not in ("Other", "Not stated on website")
        and size not in ("Unknown", "Not stated on website", "")
        and (b2b_buyer or "consumer" in low or "b2c" in low)
    ):
        lead_conf = "high"
    elif industry != "Other" or b2b_buyer or buying_signals:
        lead_conf = "medium"
    else:
        lead_conf = "low"

    result.update({
        "summary": summary,
        "industry": industry,
        "size_estimate": size,
        "b2b_buyer": b2b_buyer,
        "b2b_evidence": b2b_evidence,
        "business_model": model,
        "buying_signals": buying_signals[:8],
        "lead_score": 0,
        "lead_score_rationale": rationale,
        "score_reason": rationale,
        "lead_score_confidence": lead_conf,
        "confidence": confidence,
        "email": email_match.group(0) if email_match else "Not stated on website",
        "phone": phone_match.group(0).strip() if phone_match else "Not stated on website",
        "linkedin": linkedin_match.group(0) if linkedin_match else "Not stated on website",
        "contact_page": contact_page or "Not stated on website",
        "contact_reason": (
            "Public contact details found on homepage"
            if email_match or phone_match
            else "Not stated on website"
        ),
        "source": "heuristic",
    })
    apply_heuristic_profile_scores(result, homepage_text, scoring_profile_ids)
    return result


@retry(max_attempts=2, delay=2.0)
def analyze_company(
    company_name: str,
    homepage_text: str,
    headcount_context: str = "",
    scoring_profile_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Analyze a company using NVIDIA API.

    Always extracts B2B lead-fit signals (weighted score computed later in code).
    Optionally scores one or more configurable scoring profiles in the same call.
    """
    profile_ids = resolve_profile_ids(scoring_profile_ids)
    system_prompt = build_system_prompt(SYSTEM_PROMPT, profile_ids)
    # More profiles → need more output tokens for few-shots + scores
    max_tokens = 1600 + (600 * max(0, len(profile_ids) - 1))

    try:
        # Get API key from environment
        api_key = _nvidia_key_usable()
        if not api_key:
            print(
                f"NVIDIA_API_KEY missing/placeholder — heuristic analysis for '{company_name}'"
            )
            return _build_analysis_from_homepage(
                company_name, homepage_text, scoring_profile_ids=profile_ids
            )

        # Build user message with company data
        user_message = f"Company: {company_name}\n\nHomepage text:\n{homepage_text}"
        
        # Add headcount context if provided
        if headcount_context:
            user_message += f"\n\nAdditional context: {headcount_context}"

        # Prepare request payload
        payload = {
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": max_tokens,
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
                    return _build_analysis_from_homepage(
                        company_name, homepage_text, scoring_profile_ids=profile_ids
                    )

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
                    return _build_analysis_from_homepage(
                        company_name, homepage_text, scoring_profile_ids=profile_ids
                    )

            if not result.get("score_reason"):
                result["score_reason"] = "Not stated on website"
                result["lead_score_rationale"] = "Not stated on website"

            # Normalize types
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
            result["lead_score_confidence"] = _normalize_scorecard_confidence(
                result.get("lead_score_confidence"), default="low"
            )

            # Configurable scoring profiles (same AI call; additive to B2B lead score)
            normalize_profile_scores(result, profile_ids)

            # Fill contact-style fields with explicit non-guess default
            for key in (
                "headquarters", "country", "phone", "email",
                "linkedin", "contact_page", "contact_reason", "summary",
            ):
                if not str(result.get(key) or "").strip():
                    result[key] = "Not stated on website"

            # Map free-text industry to known enum when possible
            industry = str(result.get("industry") or "").strip()
            known = {
                "Energy", "Technology", "Finance", "Healthcare",
                "Manufacturing", "Retail", "Consulting", "Real Estate", "Other",
            }
            if industry.lower() == "not stated on website":
                result["industry"] = "Other"
                if result["confidence"] == "HIGH":
                    result["confidence"] = "MEDIUM"
            elif industry not in known:
                # Keep free-text from website for display, scoring treats unknown as Other-ish
                pass

            # Placeholder — final score applied in lead_processing via weighted scorer
            result["lead_score"] = 0

            print(f"[OK] NVIDIA API: Successfully analyzed {company_name}")
            return result

        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON parse error for {company_name}: {e}")
            print(f"Raw response: {response_text[:200]}...")
            return _build_analysis_from_homepage(
                company_name, homepage_text, scoring_profile_ids=profile_ids
            )

    except requests.exceptions.Timeout:
        print(f"[ERROR] NVIDIA API timeout for '{company_name}' after all retries")
        return _build_analysis_from_homepage(
            company_name, homepage_text, scoring_profile_ids=profile_ids
        )

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] NVIDIA API request error for '{company_name}': {e}")
        return _build_analysis_from_homepage(
            company_name, homepage_text, scoring_profile_ids=profile_ids
        )

    except Exception as e:
        print(f"[ERROR] Analysis error for '{company_name}': {e}")
        return _build_analysis_from_homepage(
            company_name, homepage_text, scoring_profile_ids=profile_ids
        )


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
        api_key = _nvidia_key_usable()
        if not api_key:
            print("Error: NVIDIA_API_KEY not set or still a placeholder — using heuristic ICP")
            return _build_icp_from_companies(company_summaries)

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
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
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

            result["source"] = "nvidia"
            return result

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            print(f"Raw response: {response_text[:200]}...")
            fallback = _build_icp_from_companies(company_summaries)
            fallback["icp_summary"] = (
                fallback["icp_summary"]
                + " (NVIDIA response was not valid JSON.)"
            )
            return fallback

    except requests.exceptions.Timeout:
        print("NVIDIA API timeout for ICP generation after all retries")
        fallback = _build_icp_from_companies(company_summaries)
        fallback["icp_summary"] = (
            fallback["icp_summary"] + " (NVIDIA API timed out.)"
        )
        return fallback

    except requests.exceptions.RequestException as e:
        print(f"NVIDIA API request error for ICP generation: {e}")
        fallback = _build_icp_from_companies(company_summaries)
        fallback["icp_summary"] = (
            fallback["icp_summary"]
            + " (NVIDIA API error — check NVIDIA_API_KEY.)"
        )
        return fallback

    except Exception as e:
        print(f"ICP generation error: {e}")
        fallback = _build_icp_from_companies(company_summaries)
        fallback["icp_summary"] = (
            fallback["icp_summary"] + f" (ICP fallback after error: {e})"
        )
        return fallback


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
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct"),
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
