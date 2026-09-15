"""Regulatory news search via Firecrawl web search."""

import os
from typing import Any, Dict, List

from firecrawl import Firecrawl

REGULATORY_QUERY_TEMPLATES = [
    "{company} AML compliance",
    "{company} regulatory fine",
    "{company} KYC investigation",
    "{company} money laundering sanction",
]

MAX_ARTICLES_PER_COMPANY = 8


def _extract_web_results(search_results: Any) -> List[Any]:
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


def _result_field(result: Any, key: str) -> str:
    if hasattr(result, key):
        val = getattr(result, key, "")
    elif isinstance(result, dict):
        val = result.get(key, "")
    else:
        val = ""
    return str(val or "").strip()


def search_regulatory_news(company_name: str) -> List[Dict[str, str]]:
    """
    Search regulatory/compliance news for a company using Firecrawl.

    Returns deduplicated articles with title, url, snippet, query.
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        return []

    firecrawl = Firecrawl(api_key=api_key)
    seen_urls: set = set()
    articles: List[Dict[str, str]] = []

    for template in REGULATORY_QUERY_TEMPLATES:
        if len(articles) >= MAX_ARTICLES_PER_COMPANY:
            break

        query = template.format(company=company_name)
        try:
            search_results = firecrawl.search(query=query, limit=6)
        except Exception as e:
            print(f"News search error for '{company_name}' ({query}): {e}")
            continue

        for result in _extract_web_results(search_results):
            if len(articles) >= MAX_ARTICLES_PER_COMPANY:
                break

            url = _result_field(result, "url")
            title = _result_field(result, "title")
            snippet = _result_field(result, "description")

            if not url or not url.startswith("http"):
                continue
            url_key = url.lower().rstrip("/")
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)

            if not title and not snippet:
                continue

            articles.append({
                "title": title or snippet[:120],
                "url": url,
                "snippet": snippet,
                "query": query,
            })

    return articles
