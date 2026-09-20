"""Lead insight helpers for the web API."""

from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# International + local phone patterns (EU campuses use +34 / +41 / +49, etc.)
PHONE_RE = re.compile(
    r"(?:\+|00)\d{1,3}[\s./-]?(?:\(?\d{1,4}\)?[\s./-]?)?\d(?:[\d\s./-]{5,16})\d"
    r"|\(\d{3}\)\s*\d{3}[-\s]?\d{4}"
    r"|\b\d{3}[-\s]?\d{3}[-\s]?\d{4}\b"
)
CONTACT_HREF_RE = re.compile(
    r'href=["\']([^"\']*(?:contact-us|contact_us|contactus|/contact|'
    r"get-in-touch|reach-us|request-more-info|enquiry|inquiry)[^\"']*)[\"']",
    re.I,
)


def filter_public_email(emails: str) -> str:
    if not emails or emails == "Not Found":
        return "Not Available"

    email_list = [e.strip() for e in emails.split(",") if e.strip()]
    priority_prefixes = [
        "contact@", "sales@", "support@", "info@", "hello@",
        "info.", "admissions", "enquiry", "inquiry",
    ]

    for prefix in priority_prefixes:
        for email in email_list:
            if email.lower().startswith(prefix) or prefix in email.lower().split("@")[0]:
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

    cleaned = re.sub(r"[^\d+]", "", str(phone).strip())
    digits = "".join(c for c in cleaned if c.isdigit())
    if len(digits) < 7 or len(digits) > 15:
        return "Not Available"

    # Keep international numbers readable (+34 93 201 81 71)
    if cleaned.startswith("+") or str(phone).strip().startswith("+"):
        spaced = re.sub(r"\s+", " ", str(phone).strip())
        return spaced

    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:11]}"
    if len(digits) == 7:
        return f"{digits[0:3]}-{digits[3:7]}"
    if len(digits) == 8:
        return f"{digits[0:4]}-{digits[4:8]}"
    return str(phone).strip() or "Not Available"


def _prefer_email(emails: List[str]) -> str:
    if not emails:
        return ""
    preferred = [
        e for e in emails
        if any(
            pref in e.lower()
            for pref in ("contact", "info", "support", "sales", "admissions", "hello")
        )
    ]
    return preferred[0] if preferred else emails[0]


def _extract_emails_phones(raw: str) -> Dict[str, str]:
    """Pull emails/phones from HTML or text, including HTML-entity obfuscation."""
    if not raw:
        return {"email": "", "phone": ""}
    decoded = html_lib.unescape(raw)
    # Also decode numeric entities like &#105;&#110;&#102;&#111;...
    decoded = re.sub(
        r"(?:&#\d+;)+",
        lambda m: html_lib.unescape(m.group(0)),
        decoded,
    )
    emails = EMAIL_RE.findall(decoded)
    # Drop image/tracking junk
    emails = [
        e for e in emails
        if not e.lower().endswith((".png", ".jpg", ".gif", ".webp", ".svg"))
        and "example.com" not in e.lower()
        and "sentry" not in e.lower()
    ]
    phones = [p.strip() for p in PHONE_RE.findall(decoded)]
    # Prefer tel: links when present
    tel_links = re.findall(r"tel:([+\d][\d\s().-]{6,})", decoded, flags=re.I)
    if tel_links:
        phones = [t.strip() for t in tel_links] + phones
    return {
        "email": _prefer_email(emails),
        "phone": phones[0] if phones else "",
    }


def _fetch_html(page_url: str, timeout: int = 12) -> str:
    try:
        resp = requests.get(
            page_url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return ""
        return resp.text or ""
    except requests.RequestException as exc:
        logger.info("Contact page fetch failed for %s: %s", page_url, exc)
        return ""


def _discover_contact_urls(homepage_url: str, homepage_html: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for match in CONTACT_HREF_RE.findall(homepage_html or ""):
        abs_url = urljoin(homepage_url, match)
        key = abs_url.split("#")[0].split("?")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(abs_url.split("#")[0])
    base = f"{urlparse(homepage_url).scheme}://{urlparse(homepage_url).netloc}"
    for path in (
        "/contact-us.html",
        "/contact-us",
        "/contact.html",
        "/contact",
        "/en/contact-us.html",
        "/get-in-touch",
    ):
        abs_url = urljoin(base + "/", path.lstrip("/"))
        key = abs_url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            found.append(abs_url)
    return found[:6]


def extract_contact_fallback(text: str, url: str, company_name: str) -> Dict[str, str]:
    """
    Extract public contact details from homepage text, then enrich from the
    site's contact page when email/phone are missing on the homepage.
    """
    result = {"phone": "", "email": "", "linkedin": "", "contact_page": ""}

    from_text = _extract_emails_phones(text or "")
    result["email"] = from_text["email"]
    result["phone"] = from_text["phone"]

    linkedin_pattern = r"https?://(?:www\.)?linkedin\.com/company/[\w-]+"
    linkedins = re.findall(linkedin_pattern, text or "")
    if linkedins:
        result["linkedin"] = linkedins[0]
    else:
        slug = company_name.lower().replace(" ", "-").replace(".", "").replace(",", "")
        result["linkedin"] = f"https://www.linkedin.com/company/{slug}"

    homepage_html = _fetch_html(url) if url else ""
    if homepage_html:
        home_contacts = _extract_emails_phones(homepage_html)
        if not result["email"] and home_contacts["email"]:
            result["email"] = home_contacts["email"]
        if not result["phone"] and home_contacts["phone"]:
            result["phone"] = home_contacts["phone"]

    contact_urls = _discover_contact_urls(url, homepage_html)
    if contact_urls:
        result["contact_page"] = contact_urls[0]

    # Homepage often hides phone/email on a dedicated contact page
    if (not result["email"] or not result["phone"]) and contact_urls:
        for contact_url in contact_urls:
            html = _fetch_html(contact_url)
            if not html:
                continue
            page_contacts = _extract_emails_phones(html)
            if page_contacts["email"] or page_contacts["phone"]:
                result["contact_page"] = contact_url
                if not result["email"] and page_contacts["email"]:
                    result["email"] = page_contacts["email"]
                if not result["phone"] and page_contacts["phone"]:
                    result["phone"] = page_contacts["phone"]
            if result["email"] and result["phone"]:
                break

    if not result["contact_page"] and url:
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        result["contact_page"] = urljoin(base, "/contact-us.html")

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
