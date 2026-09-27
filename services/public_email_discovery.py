"""Discover publicly listed business emails — never invent addresses."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

ROLE_PREFIXES = (
    "compliance@",
    "contact@",
    "info@",
    "sales@",
    "legal@",
    "privacy@",
    "risk@",
    "ir@",
    "investor@",
    "media@",
    "press@",
    "regulatory@",
    "aml@",
    "kyc@",
    "support@",
    "hello@",
)

DISPOSABLE_OR_NOISE = (
    "example.com",
    "email.com",
    "domain.com",
    "sentry.io",
    "wixpress.com",
    "cloudflare.com",
    "schema.org",
    "githubusercontent.com",
)

CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/legal",
    "/privacy",
    "/investors",
    "/press",
    "/about",
)

# Keep email discovery fast — many sites block scrapers and burn timeouts.
_FETCH_TIMEOUT_SEC = 5
_MAX_PAGES_TO_SCAN = 5
_EMAIL_CACHE: Dict[str, Dict[str, Any]] = {}


def _empty_email_result(reason: str = "") -> Dict[str, Any]:
    return {
        "email": None,
        "email_source_url": "",
        "email_source_name": "",
        "email_publicly_available": False,
        "email_confidence": "low",
        "contact_name": "",
        "contact_role": "",
        "discovery_notes": reason,
    }


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _is_noise_email(email: str) -> bool:
    e = email.lower().strip()
    if not e or "@" not in e:
        return True
    domain = e.split("@", 1)[1]
    if any(n in domain for n in DISPOSABLE_OR_NOISE):
        return True
    if e.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
        return True
    if "noreply" in e or "no-reply" in e or "donotreply" in e:
        return True
    return False


def _score_email(email: str, company_domain: str) -> Tuple[int, str]:
    """Higher score = better. Confidence derived from score."""
    e = email.lower()
    local, _, domain = e.partition("@")
    score = 0
    if company_domain and (domain == company_domain or domain.endswith("." + company_domain)):
        score += 40
    for prefix in ROLE_PREFIXES:
        if e.startswith(prefix):
            score += 50
            break
    if "." in local and not any(e.startswith(p) for p in ROLE_PREFIXES):
        # personal-looking — only keep if on company domain (still publicly listed)
        score += 5
    if score >= 70:
        return score, "high"
    if score >= 40:
        return score, "medium"
    return score, "low"


def _extract_emails_from_html(html: str) -> List[str]:
    found = EMAIL_RE.findall(html or "")
    # Also mailto:
    mailto = re.findall(r"mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", html or "", re.I)
    out: List[str] = []
    seen = set()
    for e in found + mailto:
        key = e.lower()
        if key in seen or _is_noise_email(e):
            continue
        seen.add(key)
        out.append(e.strip())
    return out


def _fetch_page_text(url: str) -> Tuple[str, str]:
    """Return (html_or_text, source_name). Prefer a single fast HTTP GET."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=_FETCH_TIMEOUT_SEC)
        if resp.ok and resp.text:
            return resp.text, "company_website"
    except Exception as exc:
        logger.debug("HTTP fetch failed for %s: %s", url, exc)
    # Skip slow Firecrawl/fallback scraper chains here — they add minutes when sites 403.
    return "", "scraped_page"


def _candidate_contact_urls(homepage: str, html: str) -> List[str]:
    base = homepage.rstrip("/")
    parsed = urlparse(homepage)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    urls = [homepage]
    for path in CONTACT_PATHS:
        urls.append(origin + path)

    # Links from homepage HTML
    for match in re.findall(r'href=["\']([^"\']+)["\']', html or "", re.I):
        low = match.lower()
        if any(
            k in low
            for k in (
                "contact",
                "legal",
                "compliance",
                "privacy",
                "investor",
                "press",
                "media",
                "about",
            )
        ):
            abs_url = urljoin(origin + "/", match)
            if abs_url.startswith("http"):
                urls.append(abs_url)

    # Dedup preserving order
    seen = set()
    out: List[str] = []
    for u in urls:
        key = u.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out[:_MAX_PAGES_TO_SCAN]


def discover_public_business_email(company_name: str) -> Dict[str, Any]:
    """
    Search official company pages for a publicly listed business email.

    NEVER invent or guess addresses such as firstname.lastname@domain
    or compliance@domain unless that exact address appears on a page.
    """
    cache_key = (company_name or "").strip().lower()
    if cache_key in _EMAIL_CACHE:
        return dict(_EMAIL_CACHE[cache_key])

    try:
        from pipeline.search import search_company_info

        homepage, _ctx = search_company_info(company_name)
    except Exception as exc:
        logger.warning("Homepage search failed for %s: %s", company_name, exc)
        homepage = None

    if not homepage:
        result = _empty_email_result("No official company website found")
        _EMAIL_CACHE[cache_key] = result
        return dict(result)

    company_domain = _domain_of(homepage)
    home_html, _ = _fetch_page_text(homepage)
    pages = _candidate_contact_urls(homepage, home_html)

    best: Optional[Dict[str, Any]] = None
    best_score = -1

    for page_url in pages:
        try:
            html, source_name = _fetch_page_text(page_url)
            if not html:
                continue
            emails = _extract_emails_from_html(html)
            for email in emails:
                score, confidence = _score_email(email, company_domain)
                # Require company-domain match OR strong role prefix on related domain
                domain = email.lower().split("@", 1)[1]
                on_company = company_domain and (
                    domain == company_domain or domain.endswith("." + company_domain)
                )
                if not on_company and confidence == "low":
                    continue
                if score > best_score:
                    best_score = score
                    path_hint = urlparse(page_url).path.lower()
                    source_label = "Official company website"
                    if "contact" in path_hint:
                        source_label = "Official contact page"
                    elif "investor" in path_hint or path_hint.rstrip("/") in ("/ir",):
                        source_label = "Investor relations page"
                    elif "legal" in path_hint or "compliance" in path_hint:
                        source_label = "Legal/compliance page"
                    elif "press" in path_hint or "media" in path_hint:
                        source_label = "Press/media page"
                    elif "privacy" in path_hint:
                        source_label = "Privacy page"
                    best = {
                        "email": email,
                        "email_source_url": page_url,
                        "email_source_name": source_label,
                        "email_publicly_available": True,
                        "email_confidence": confidence,
                        "contact_name": "",
                        "contact_role": "",
                        "discovery_notes": f"Found on {source_name}",
                    }
                    if confidence == "high" and on_company:
                        break
            if best and best.get("email_confidence") == "high":
                break
        except Exception as exc:
            logger.info(
                "Email discovery page failed company=%s stage=scrape url=%s error=%s",
                company_name,
                page_url,
                exc,
            )
            continue

    if not best:
        result = _empty_email_result("No publicly listed business email found on official pages")
        _EMAIL_CACHE[cache_key] = result
        return dict(result)

    # Only use medium/high for drafts that claim a verified public email
    if best["email_confidence"] == "low":
        best["email_publicly_available"] = True  # still found, but low confidence
    _EMAIL_CACHE[cache_key] = best
    return dict(best)
