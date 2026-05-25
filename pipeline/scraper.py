"""Web scraping module using Firecrawl to extract text content from company homepages."""

import os
import requests
from bs4 import BeautifulSoup

from firecrawl import Firecrawl

# Maximum characters to return from scraped page
MAX_CHARS = 3000


def scrape_homepage_fallback(url: str) -> str:
    """
    Fallback scraper using requests and BeautifulSoup when Firecrawl fails.
    
    This is a free alternative that doesn't require API credits.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Get text
        text = soup.get_text()
        
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        # Truncate to max characters
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "..."
        
        print(f"Fallback scraping successful for {url}")
        return text
        
    except Exception as e:
        print(f"Fallback scraping error for '{url}': {e}")
        return ""


def scrape_homepage(url: str) -> str:
    """
    Scrape and extract text content from a company's homepage using Firecrawl.
    
    Falls back to free scraping if Firecrawl fails due to insufficient credits.

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
        Falls back to free scraping if Firecrawl credits are exhausted.
    """
    try:
        # Get API key from environment
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            print("FIRECRAWL_API_KEY not set, using fallback scraper")
            return scrape_homepage_fallback(url)

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
            print(f"No content extracted from {url}, using fallback")
            return scrape_homepage_fallback(url)

        # Clean up whitespace
        full_text = " ".join(markdown.split())

        # Truncate to max characters
        if len(full_text) > MAX_CHARS:
            full_text = full_text[:MAX_CHARS].rsplit(" ", 1)[0] + "..."

        return full_text

    except Exception as e:
        error_msg = str(e)
        print(f"Firecrawl error for '{url}': {e}")
        
        # Check if it's a credit/payment issue and use fallback
        if "Payment Required" in error_msg or "insufficient credits" in error_msg.lower():
            print("Insufficient Firecrawl credits, using fallback scraper")
            return scrape_homepage_fallback(url)
        
        return ""
