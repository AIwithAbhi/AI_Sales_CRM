"""Lead insight helpers for the web API."""

import re
from typing import Any, Dict
from urllib.parse import urljoin, urlparse


def filter_public_email(emails: str) -> str:
    if not emails or emails == "Not Found":
        return "Not Available"

    email_list = [e.strip() for e in emails.split(",") if e.strip()]
    priority_prefixes = ["contact@", "sales@", "support@", "info@", "hello@"]

    for prefix in priority_prefixes:
        for email in email_list:
            if email.lower().startswith(prefix):
                return email

    personal_prefixes = [
        "john@", "jane@", "mike@", "sarah@", "david@", "emily@",
        "chris@", "alex@", "matt@", "jessica@", "michael@",
        "lisa@", "robert@", "jennifer@", "william@", "elizabeth@",
    ]
    for email in email_list:
        if not any(email.lower().startswith(p) for p in personal_prefixes):
            return email

    return "Not Available"


def validate_and_format_phone(phone: str) -> str:
    if not phone or phone == "Not Found":
        return "Not Available"

    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 7 or len(digits) > 15:
        return "Not Available"

    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:11]}"
    if len(digits) > 10:
        parts = [digits[i : i + 3] for i in range(0, len(digits), 3)]
        return "+" + " ".join(parts)
    if len(digits) == 7:
        return f"{digits[0:3]}-{digits[3:7]}"
    if len(digits) == 8:
        return f"{digits[0:4]}-{digits[4:8]}"
    return "Not Available"


def extract_contact_fallback(text: str, url: str, company_name: str) -> Dict[str, str]:
    result = {"phone": "", "email": "", "linkedin": "", "contact_page": ""}

    phone_patterns = [
        r"\+?[\d\s\-\(\)]{10,}",
        r"\(\d{3}\)\s*\d{3}[-\s]?\d{4}",
        r"\d{3}[-\s]?\d{3}[-\s]?\d{4}",
    ]
    for pattern in phone_patterns:
        phones = re.findall(pattern, text)
        if phones:
            result["phone"] = phones[0].strip()
            break

    email_pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    emails = re.findall(email_pattern, text)
    if emails:
        preferred = [
            e for e in emails
            if any(pref in e.lower() for pref in ["contact", "info", "support", "sales"])
        ]
        result["email"] = preferred[0] if preferred else emails[0]

    linkedin_pattern = r"https?://(?:www\.)?linkedin\.com/company/[\w-]+"
    linkedins = re.findall(linkedin_pattern, text)
    if linkedins:
        result["linkedin"] = linkedins[0]
    else:
        slug = company_name.lower().replace(" ", "-").replace(".", "").replace(",", "")
        result["linkedin"] = f"https://www.linkedin.com/company/{slug}"

    contact_patterns = [
        r'href=["\']([^"\']*(?:contact|contact-us|get-in-touch|reach-us)[^"\']*)["\']',
        r'href=["\']([^"\']*/contact[^"\']*)["\']',
    ]
    for pattern in contact_patterns:
        contact_urls = re.findall(pattern, text, re.IGNORECASE)
        if contact_urls:
            contact_url = contact_urls[0]
            if contact_url.startswith("/"):
                base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                result["contact_page"] = urljoin(base, contact_url)
            else:
                result["contact_page"] = contact_url
            break

    if not result["contact_page"]:
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        for path in ["/contact", "/contact-us", "/contactus", "/get-in-touch"]:
            result["contact_page"] = urljoin(base, path)
            break

    return result


def generate_lead_explanation(result: Dict[str, Any]) -> str:
    score = result.get("lead_score", 0)
    industry = result.get("industry", "")
    size = result.get("size_estimate", "")
    b2b = result.get("b2b_buyer", False)
    growth_label = result.get("growth_label", "")

    factors = []
    if industry in ["Energy", "Technology", "Manufacturing"]:
        factors.append(f"operates in the high-priority {industry} sector")
    elif industry and industry != "Other":
        factors.append(f"operates in the {industry} industry")

    if size in ("1001+", "501-1000", "High"):
        factors.append(f"is an enterprise-size organization ({size})")
    elif size in ("201-500", "51-200", "Medium"):
        factors.append(f"is a mid-market organization ({size})")
    elif size in ("1-50", "Small"):
        factors.append(f"is a small organization ({size})")
    elif size:
        factors.append(f"has company size: {size}")

    signals = result.get("buying_signals") or []
    if signals:
        factors.append(f"shows buying signals ({', '.join(str(s) for s in signals[:3])})")

    if b2b:
        factors.append("demonstrates strong B2B software purchase potential")

    if growth_label == "Rapid growth":
        factors.append("shows rapid LinkedIn headcount growth")
    elif growth_label == "Growing":
        factors.append("shows positive headcount growth")

    if factors:
        factor_text = ", ".join(factors[:-1]) + (", and " + factors[-1] if len(factors) > 1 else "")
        return f"This company received a score of {score}/10 because it {factor_text}."

    return f"This company received a score of {score}/10 based on available company data."


def get_lead_qualification_breakdown(result: Dict[str, Any]) -> Dict[str, str]:
    industry = result.get("industry", "")
    size = result.get("size_estimate", "")
    b2b = result.get("b2b_buyer", False)
    growth_label = result.get("growth_label", "")

    breakdown: Dict[str, str] = {}

    if industry in ["Energy", "Technology", "Manufacturing"]:
        breakdown["Industry Match"] = "✓ Strong Match"
    elif industry and industry != "Other":
        breakdown["Industry Match"] = "✓ Good Match"
    else:
        breakdown["Industry Match"] = "○ Standard"

    if size in ("1001+", "501-1000", "High"):
        breakdown["Company Size"] = f"✓ Enterprise ({size})"
    elif size in ("201-500", "51-200", "Medium"):
        breakdown["Company Size"] = f"✓ Mid-market ({size})"
    elif size in ("1-50", "Small"):
        breakdown["Company Size"] = f"○ Small ({size})"
    else:
        breakdown["Company Size"] = "○ Unknown"

    breakdown["B2B Potential"] = "✓ High" if b2b else "○ Low"

    signals = result.get("buying_signals") or []
    if signals:
        breakdown["Buying Signals"] = f"✓ {len(signals)}: {', '.join(str(s) for s in signals[:3])}"
    else:
        breakdown["Buying Signals"] = "○ None detected"

    sb = result.get("score_breakdown") or {}
    if sb:
        breakdown["Score Weights"] = (
            f"Industry {sb.get('industry_points', 0)} / "
            f"Size {sb.get('size_points', 0)} / "
            f"B2B {sb.get('b2b_points', 0)} / "
            f"Signals {sb.get('signal_points', 0)}"
        )

    if growth_label == "Rapid growth":
        breakdown["Growth Trend"] = "✓ Rapid Growth"
    elif growth_label == "Growing":
        breakdown["Growth Trend"] = "✓ Growing"
    elif growth_label == "Stable":
        breakdown["Growth Trend"] = "○ Stable"
    else:
        breakdown["Growth Trend"] = "○ Unknown"

    return breakdown
