"""Web search module using Firecrawl to find company homepage URLs."""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

from firecrawl import Firecrawl

from utils.url_validation import (
    EXCLUDED_DOMAINS,
    build_alternate_urls,
    resolve_valid_company_url,
)

logger = logging.getLogger(__name__)


def search_company_info_fallback(company_name: str) -> Tuple[Optional[str], str]:
    """
    Fallback when Firecrawl is unavailable: try alternate URL patterns with validation.
    """
    url = resolve_valid_company_url(company_name, build_alternate_urls(company_name))
    if url:
        return url, f"Validated constructed URL for {company_name}"
    logger.warning("Fallback URL validation failed for '%s'", company_name)
    return None, ""


def get_homepage_url(company_name: str) -> Optional[str]:
    """Search the web and return a validated company homepage URL."""
    url, _ = search_company_info(company_name)
    return url


def _extract_web_results(search_results) -> list:
    """Normalize Firecrawl search response into a list of result objects/dicts."""
    web_results = []
    if hasattr(search_results, "data"):
        data = search_results.data
        if hasattr(data, "web"):
            web_results = data.web or []
        elif isinstance(data, dict) and "web" in data:
            web_results = data["web"] or []
    elif hasattr(search_results, "web"):
        web_results = search_results.web or []
    return web_results


def search_company_info(company_name: str) -> Tuple[Optional[str], str]:
    """
    Search for a company and return (validated_homepage_url, search_context).

    Candidates from Firecrawl are validated (redirects + company-name page check).
    Falls back to alternate URL patterns if search fails or no candidate validates.
    """
    candidate_urls: List[str] = []
    search_context = ""

    try:
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            logger.warning("FIRECRAWL_API_KEY not set, using fallback search")
            return search_company_info_fallback(company_name)

        firecrawl = Firecrawl(api_key=api_key)
        query = f"{company_name} official website"
        search_results = firecrawl.search(query=query, limit=10)
        web_results = _extract_web_results(search_results)

        context_parts: List[str] = []
        for result in web_results:
            link = getattr(result, "url", result.get("url") if isinstance(result, dict) else "")
            title = getattr(result, "title", result.get("title") if isinstance(result, dict) else "")
            desc = getattr(
                result,
                "description",
                result.get("description") if isinstance(result, dict) else "",
            )

            if not link or not str(link).startswith("http"):
                continue

            if any(domain in link.lower() for domain in EXCLUDED_DOMAINS):
                continue

            if link not in candidate_urls:
                candidate_urls.append(link)

            if title or desc:
                context_parts.append(f"Title: {title}\nDescription: {desc}")

        search_context = "\n\n".join(context_parts[:5])

        validated = resolve_valid_company_url(company_name, candidate_urls)
        if validated:
            return validated, search_context

        logger.warning(
            "No Firecrawl candidate validated for '%s', trying alternates", company_name
        )
        return search_company_info_fallback(company_name)

    except Exception as e:
        error_msg = str(e)
        logger.error("Firecrawl search error for '%s': %s", company_name, e)
        if "Payment Required" in error_msg or "insufficient credits" in error_msg.lower():
            logger.warning("Insufficient Firecrawl credits, using fallback search")
            return search_company_info_fallback(company_name)
        return None, search_context
