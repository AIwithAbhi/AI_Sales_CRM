"""Web search module using Firecrawl to find company homepage URLs."""

import os
from typing import Optional

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
    try:
        # Get API key from environment
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            print("Error: FIRECRAWL_API_KEY not set in environment")
            return None

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

        for result in web_results:
            if hasattr(result, 'url'):
                link = result.url
            elif isinstance(result, dict):
                link = result.get('url', '')
            else:
                link = ''

            if not link or not link.startswith("http"):
                continue

            # Check if link is from excluded domain
            is_excluded = any(
                domain in link.lower() for domain in EXCLUDED_DOMAINS
            )

            if not is_excluded:
                return link

        # No valid URL found
        return None

    except Exception as e:
        print(f"Search error for '{company_name}': {e}")
        return None
