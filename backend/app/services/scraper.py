import os
import requests
from bs4 import BeautifulSoup
from firecrawl import Firecrawl

from backend.app.config import settings
from backend.app.utils.logging import logger

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
        
        logger.info(f"Fallback scraping successful for {url}")
        return text
        
    except Exception as e:
        logger.error(f"Fallback scraping error for '{url}': {e}")
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
    """
    try:
        # Get API key from settings
        api_key = settings.FIRECRAWL_API_KEY
        if not api_key:
            logger.warning(f"FIRECRAWL_API_KEY not set, using fallback scraper for URL: {url}")
            return scrape_homepage_fallback(url)

        # Initialize Firecrawl client
        firecrawl = Firecrawl(api_key=api_key)

        logger.info(f"Scraping homepage '{url}' using Firecrawl...")
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
            logger.warning(f"No content extracted from {url} via Firecrawl, trying fallback scraper")
            return scrape_homepage_fallback(url)

        # Clean up whitespace
        full_text = " ".join(markdown.split())

        # Truncate to max characters
        if len(full_text) > MAX_CHARS:
            full_text = full_text[:MAX_CHARS].rsplit(" ", 1)[0] + "..."

        logger.info(f"Scraping successful. Text length: {len(full_text)} characters")
        return full_text

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Firecrawl scrape error for '{url}': {e}")
        
        # Check if it's a credit/payment issue and use fallback
        if "Payment Required" in error_msg or "insufficient credits" in error_msg.lower():
            logger.warning("Insufficient Firecrawl credits, trying fallback scraper")
            return scrape_homepage_fallback(url)
        
        return ""
