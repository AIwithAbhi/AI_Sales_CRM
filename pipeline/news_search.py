"""Regulatory news search via Firecrawl web search."""

import os
from typing import Any, Dict, List

from firecrawl import Firecrawl

CORE_QUERY_TEMPLATES = [
    "{company} regulatory",
    "{company} compliance",
    "{company} AML",
    "{company} KYC",
    "{company} enforcement",
    "{company} investigation",
]

EXTENDED_QUERY_TEMPLATES = [
    "{company} fine",
    "{company} penalty",
    "{company} data protection",
    "{company} GDPR",
    "{company} sanctions",
    "{company} financial crime",
    "{company} regulator",
    "{company} compliance requirements",
]

INDUSTRY_QUERY_TEMPLATES = {
    "finance": [
        "{company} banking regulation",
        "{company} FinCEN OR FCA OR OCC",
    ],
    "technology": [
        "{company} data protection investigation",
        "{company} privacy enforcement",
    ],
    "healthcare": [
        "{company} HIPAA OR HHS OCR",
        "{company} healthcare compliance investigation",
    ],
    "energy": [
        "{company} regulatory compliance fine",
    ],
}

MAX_ARTICLES_PER_COMPANY = 8
MIN_ARTICLES_BEFORE_EXTENDED = 3


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


def _build_query_list(company_name: str, industry_hint: str = "") -> List[str]:
    templates = list(CORE_QUERY_TEMPLATES)
    hint = (industry_hint or "").strip().lower()
    for key, extras in INDUSTRY_QUERY_TEMPLATES.items():
        if key in hint:
            templates.extend(extras)
            break
    return [t.format(company=company_name) for t in templates]


def search_regulatory_news(
    company_name: str,
    industry_hint: str = "",
) -> List[Dict[str, str]]:
    """
    Search regulatory/compliance news for a company using Firecrawl.

    Runs a focused core query set first; expands to additional templates only
    if few articles are found. Returns deduplicated articles.
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        return []

    firecrawl = Firecrawl(api_key=api_key)
    seen_urls: set = set()
    articles: List[Dict[str, str]] = []

    queries = _build_query_list(company_name, industry_hint)
    extended = [t.format(company=company_name) for t in EXTENDED_QUERY_TEMPLATES]

    def _run_queries(query_list: List[str]) -> None:
        nonlocal articles
        for query in query_list:
            if len(articles) >= MAX_ARTICLES_PER_COMPANY:
                return
            try:
                search_results = firecrawl.search(query=query, limit=5)
            except Exception as e:
                print(f"News search error for '{company_name}' ({query}): {e}")
                continue

            for result in _extract_web_results(search_results):
                if len(articles) >= MAX_ARTICLES_PER_COMPANY:
                    return

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

    _run_queries(queries)
    if len(articles) < MIN_ARTICLES_BEFORE_EXTENDED:
        _run_queries(extended)

    return articles
