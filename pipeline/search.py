"""Web search module using Firecrawl to find company homepage URLs."""

import os
import re
from typing import Optional, Tuple

from firecrawl import Firecrawl

# Domains to exclude from search results (not company homepages)
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
]


def search_company_info_fallback(company_name: str) -> Tuple[Optional[str], str]:
    """
    Fallback search using simple URL construction when Firecrawl fails.
    
    This constructs likely URLs based on company name patterns.
    """
    try:
        # Clean company name for URL construction
        clean_name = re.sub(r'[^\w\s-]', '', company_name)
        clean_name = re.sub(r'[-\s]+', '-', clean_name).lower()
        
        # Try common URL patterns
        possible_urls = [
            f"https://www.{clean_name}.com",
            f"https://{clean_name}.com",
            f"https://www.{clean_name}.co",
            f"https://{clean_name}.co",
            f"https://www.{clean_name}.io",
            f"https://{clean_name}.io",
        ]
        
        # Return the first URL as a guess (we'll validate when scraping)
        return possible_urls[0], f"Constructed URL for {company_name}"
        
    except Exception as e:
        print(f"Fallback search error for '{company_name}': {e}")
        return None, ""


def get_homepage_url(company_name: str) -> Optional[str]:
    """
    Search the web to find a company's official homepage URL.

    Uses Firecrawl search API to find the first organic result that is not
    from excluded domains (social media, Wikipedia, etc.).

    Args:
        company_name: Name of the company to search for.

    Returns:
        The homepage URL as a string, or None if no valid URL found.
    """
    url, _ = search_company_info(company_name)
    return url


def search_company_info(company_name: str) -> Tuple[Optional[str], str]:
    """
    Search the web for a company and return (homepage_url, search_context).

    Uses Firecrawl search API to find the first organic result that is not
    from excluded domains (social media, Wikipedia, etc.), and collects
    search result summaries (titles and descriptions) as context.
    
    Falls back to URL construction if Firecrawl fails due to insufficient credits.

    Args:
        company_name: Name of the company to search for.

    Returns:
        A tuple of (homepage_url, search_context).
    """
    try:
        # Get API key from environment
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            print("FIRECRAWL_API_KEY not set, using fallback search")
            return search_company_info_fallback(company_name)

        # Initialize Firecrawl client
        firecrawl = Firecrawl(api_key=api_key)

        # Build search query - prioritize official website
        query = f"{company_name} official website"

        # Use Firecrawl search to get results
        search_results = firecrawl.search(
            query=query,
            limit=10,
        )

        # Extract web results from response (handle object format)
        web_results = []
        
        # Try to get web results from search_results object
        if hasattr(search_results, 'data'):
            data = search_results.data
            if hasattr(data, 'web'):
                web_results = data.web or []
            elif isinstance(data, dict) and 'web' in data:
                web_results = data['web'] or []
        elif hasattr(search_results, 'web'):
            web_results = search_results.web or []

        homepage_url = None
        search_context_parts = []

        for result in web_results:
            link = getattr(result, 'url', result.get('url') if isinstance(result, dict) else '')
            title = getattr(result, 'title', result.get('title') if isinstance(result, dict) else '')
            desc = getattr(result, 'description', result.get('description') if isinstance(result, dict) else '')

            if not link or not link.startswith("http"):
                continue

            # Check if link is from excluded domain
            is_excluded = any(
                domain in link.lower() for domain in EXCLUDED_DOMAINS
            )

            if not is_excluded and not homepage_url:
                homepage_url = link

            if title or desc:
                search_context_parts.append(f"Title: {title}\nDescription: {desc}")

        search_context = "\n\n".join(search_context_parts[:5])
        
        # If no homepage found, use fallback
        if not homepage_url:
            print(f"No homepage found via Firecrawl, using fallback for {company_name}")
            return search_company_info_fallback(company_name)
        
        return homepage_url, search_context

    except Exception as e:
        error_msg = str(e)
        print(f"Firecrawl search error for '{company_name}': {e}")
        
        # Check if it's a credit/payment issue and use fallback
        if "Payment Required" in error_msg or "insufficient credits" in error_msg.lower():
            print("Insufficient Firecrawl credits, using fallback search")
            return search_company_info_fallback(company_name)
        
        return None, ""

