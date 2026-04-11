"""Web scraping module using Firecrawl to extract text content from company homepages."""

import os

from firecrawl import Firecrawl

# Maximum characters to return from scraped page
MAX_CHARS = 3000


def scrape_homepage(url: str) -> str:
    """
    Scrape and extract text content from a company's homepage using Firecrawl.

    Uses Firecrawl API to scrape the given URL and convert it into clean,
    LLM-ready markdown format. Returns truncated text to stay within token limits.

    Args:
        url: The homepage URL to scrape.

    Returns:
        Extracted text content (max 3000 characters), or empty string
        on any failure (API error, timeout, etc.).

    Note:
        Firecrawl handles JavaScript-rendered content, anti-bot protections,
        and returns clean markdown perfect for AI analysis.
    """
    try:
        # Get API key from environment
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            print("Error: FIRECRAWL_API_KEY not set in environment")
            return ""

        # Initialize Firecrawl client
        firecrawl = Firecrawl(api_key=api_key)

        # Scrape the URL using Firecrawl
        scrape_result = firecrawl.scrape(
            url,
            formats=["markdown"],
        )

        # Extract markdown content from response (handle object format)
        markdown = ''
        
        if hasattr(scrape_result, 'data') and scrape_result.data:
            data = scrape_result.data
            if hasattr(data, 'markdown'):
                markdown = data.markdown or ''
            elif isinstance(data, dict) and 'markdown' in data:
                markdown = data['markdown'] or ''
        elif hasattr(scrape_result, 'markdown'):
            markdown = scrape_result.markdown or ''

        if not markdown:
            print(f"No content extracted from {url}")
            return ""

        # Clean up whitespace
        full_text = " ".join(markdown.split())

        # Truncate to max characters
        if len(full_text) > MAX_CHARS:
            full_text = full_text[:MAX_CHARS].rsplit(" ", 1)[0] + "..."

        return full_text

    except Exception as e:
        print(f"Scraping error for '{url}': {e}")
        return ""
