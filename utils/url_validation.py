"""Validate that a candidate URL actually belongs to the target company."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 12
SNIPPET_BYTES = 2048

# Domains that are never company homepages
EXCLUDED_DOMAINS = [
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "wikipedia.org",
    "youtube.com",
    "crunchbase.com",
    "glassdoor.com",
    "indeed.com",
    "bloomberg.com",
    "reuters.com",
    "bbc.co.uk",
    "bbc.com",
    "cnn.com",
    "nytimes.com",
    "wsj.com",
    "ft.com",
    "forbes.com",
    "dw.com",
    "cnbc.com",
    "web.archive.org",
    "archive.org",
]


def _normalize_tokens(company_name: str) -> List[str]:
    """Extract meaningful tokens from a company name for matching."""
    cleaned = re.sub(r"[^\w\s-]", " ", company_name.lower())
    stop = {"inc", "llc", "ltd", "corp", "corporation", "co", "the", "company", "group"}
    tokens = [t for t in cleaned.split() if t and t not in stop and len(t) > 1]
    return tokens


def _slugify(company_name: str) -> str:
    """Build a URL-safe slug from a company name."""
    clean = re.sub(r"[^\w\s-]", "", company_name)
    return re.sub(r"[-\s]+", "", clean).lower()


def is_excluded_domain(url: str) -> bool:
    """Return True if the URL host is a known non-company domain."""
    host = (urlparse(url).hostname or "").lower()
    return any(domain in host for domain in EXCLUDED_DOMAINS)


def follow_redirects(url: str) -> Optional[str]:
    """
    Follow redirect chain via HEAD (falls back to GET) and return final URL.

    Returns None if the URL is unreachable or returns a non-success status.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.head(
            url, headers=headers, allow_redirects=True, timeout=REQUEST_TIMEOUT
        )
        if resp.status_code >= 400 or resp.status_code < 200:
            resp = requests.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
        if 200 <= resp.status_code < 400:
            return str(resp.url)
        logger.warning("URL %s returned status %s", url, resp.status_code)
        return None
    except requests.RequestException as exc:
        logger.warning("Redirect check failed for %s: %s", url, exc)
        return None


def page_mentions_company(url: str, company_name: str) -> bool:
    """
    Fetch the first ~2KB of the page and check for company name tokens.

    At least one meaningful token (or a compacted slug) must appear.
    """
    tokens = _normalize_tokens(company_name)
    if not tokens:
        return False

    headers = {"User-Agent": USER_AGENT, "Range": f"bytes=0-{SNIPPET_BYTES - 1}"}
    try:
        resp = requests.get(
            url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
        if not (200 <= resp.status_code < 400):
            return False
        snippet = resp.content[:SNIPPET_BYTES].decode("utf-8", errors="ignore").lower()
    except requests.RequestException as exc:
        logger.warning("Snippet fetch failed for %s: %s", url, exc)
        return False

    slug = _slugify(company_name)
    if slug and len(slug) >= 4 and slug in snippet.replace(" ", "").replace("-", ""):
        return True

    hits = sum(1 for t in tokens if t in snippet)
    # Require majority of tokens for multi-word names; single token must match
    needed = 1 if len(tokens) == 1 else max(1, (len(tokens) + 1) // 2)
    return hits >= needed


def build_alternate_urls(company_name: str) -> List[str]:
    """Generate likely homepage URL patterns for a company name."""
    slug = re.sub(r"[^\w\s-]", "", company_name)
    slug = re.sub(r"[-\s]+", "-", slug).strip("-").lower()
    compact = slug.replace("-", "")
    # Corporate .com first (compact + hyphenated), then other TLDs.
    # Callers often only seed the first few entries.
    ordered: List[str] = []
    for base in (compact, slug):
        if not base:
            continue
        ordered.extend([f"https://www.{base}.com", f"https://{base}.com"])
    for base in (compact, slug):
        if not base:
            continue
        for tld in (".net", ".org", ".io", ".co", ".edu"):
            ordered.extend([f"https://www.{base}{tld}", f"https://{base}{tld}"])
    seen = set()
    unique: List[str] = []
    for u in ordered:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def validate_company_url(url: str, company_name: str) -> Tuple[bool, str]:
    """
    Validate that a URL belongs to the given company.

    Returns:
        (is_valid, final_url_or_reason)
    """
    if not url or not url.startswith("http"):
        return False, "URL must start with http:// or https://"

    if is_excluded_domain(url):
        return False, f"Excluded domain: {url}"

    final_url = follow_redirects(url)
    if not final_url:
        return False, f"Unreachable or bad status: {url}"

    if is_excluded_domain(final_url):
        return False, f"Redirected to excluded domain: {final_url}"

    if not page_mentions_company(final_url, company_name):
        logger.warning(
            "Page content does not mention company '%s' at %s", company_name, final_url
        )
        return False, f"Company name not found on page: {final_url}"

    return True, final_url


def resolve_valid_company_url(
    company_name: str,
    candidate_urls: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Return the first valid company homepage URL from candidates + alternates.

    Args:
        company_name: Target company name.
        candidate_urls: Optional list from search (Firecrawl, etc.).

    Returns:
        Valid homepage URL, or None if nothing validates.
    """
    ordered: List[str] = []
    for u in candidate_urls or []:
        if u and u not in ordered:
            ordered.append(u)
    for u in build_alternate_urls(company_name):
        if u not in ordered:
            ordered.append(u)

    for url in ordered[:20]:
        ok, result = validate_company_url(url, company_name)
        if ok:
            logger.info("Validated URL for '%s': %s", company_name, result)
            return result
        logger.warning("Rejected URL for '%s': %s", company_name, result)

    logger.warning("No valid URL found for '%s'", company_name)
    return None
