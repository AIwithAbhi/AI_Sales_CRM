"""Regulatory news search via Firecrawl, with free RSS/HTML fallbacks."""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, unquote, urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

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

# Domains that are search engines / junk (keep news.google.com — RSS links point there)
_SKIP_HOST_FRAGMENTS = (
    "duckduckgo.com",
    "www.google.com",
    "google.com/search",
    "bing.com/search",
    "yahoo.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "youtube.com",
)


def _firecrawl_key_usable() -> Optional[str]:
    key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    if not key:
        return None
    low = key.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return None
    return key


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


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _url_host_ok(url: str) -> bool:
    if not url.startswith("http"):
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    # Google News article wrappers are valid (publisher URL often not exposed in RSS)
    if host.endswith("news.google.com"):
        return True
    if host in ("www.google.com", "google.com") or host.endswith(".google.com"):
        return False
    if host in ("www.bing.com", "bing.com"):
        return False
    return not any(frag in host for frag in _SKIP_HOST_FRAGMENTS)


def _append_article(
    articles: List[Dict[str, str]],
    seen_urls: set,
    *,
    title: str,
    url: str,
    snippet: str,
    query: str,
) -> None:
    if len(articles) >= MAX_ARTICLES_PER_COMPANY:
        return
    if not _url_host_ok(url):
        return
    url_key = url.lower().rstrip("/")
    if url_key in seen_urls:
        return
    if not title and not snippet:
        return
    seen_urls.add(url_key)
    articles.append({
        "title": title or snippet[:120],
        "url": url,
        "snippet": snippet,
        "query": query,
    })


def _unwrap_google_news_link(link: str) -> str:
    """Prefer the underlying publisher URL when Google News wraps it."""
    if "news.google.com" not in link:
        return link
    for key in ("url=", "url%3D"):
        if key in link:
            m = re.search(r"[?&]url=([^&]+)", link)
            if m:
                return unquote(m.group(1))
    return link


def _search_google_news_rss(company_name: str, limit: int = 8) -> List[Dict[str, str]]:
    """Free Google News RSS — no API key. Best fallback for Industry Updates."""
    # Keep the query simple: nested quotes/parens often return empty feeds.
    query = f"{company_name} regulatory OR compliance OR AML OR KYC OR enforcement OR fine"
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Google News RSS failed for '%s': %s", company_name, exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        logger.warning("Google News RSS parse failed for '%s': %s", company_name, exc)
        return []

    out: List[Dict[str, str]] = []
    seen: set = set()
    for item in root.findall(".//item"):
        if len(out) >= limit:
            break
        title = _clean_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        desc = _clean_html(item.findtext("description") or "")
        link = _unwrap_google_news_link(link)
        if not link.startswith("http"):
            continue
        _append_article(
            out,
            seen,
            title=title,
            url=link,
            snippet=desc,
            query=f"google-news:{company_name}",
        )
    return out


def _search_bing_news_rss(company_name: str, limit: int = 8) -> List[Dict[str, str]]:
    """Bing News RSS often exposes direct publisher URLs."""
    query = f"{company_name} AML OR compliance OR regulatory OR fine"
    url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss"
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Bing News RSS failed for '%s': %s", company_name, exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        logger.warning("Bing News RSS parse failed for '%s': %s", company_name, exc)
        return []

    out: List[Dict[str, str]] = []
    seen: set = set()
    for item in root.findall(".//item"):
        if len(out) >= limit:
            break
        title = _clean_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        desc = _clean_html(item.findtext("description") or "")
        if not link.startswith("http"):
            continue
        _append_article(
            out,
            seen,
            title=title,
            url=link,
            snippet=desc,
            query=f"bing-news:{company_name}",
        )
    return out


def _search_duckduckgo_news(company_name: str, limit: int = 8) -> List[Dict[str, str]]:
    """DuckDuckGo HTML search fallback for regulatory news."""
    query = f"{company_name} regulatory compliance OR AML OR fine OR investigation"
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code >= 400:
            resp.raise_for_status()
        html = resp.text
    except requests.RequestException as exc:
        logger.warning("DuckDuckGo news fallback failed for '%s': %s", company_name, exc)
        return []

    if "result__a" not in html:
        logger.warning("DuckDuckGo returned no result markup for news '%s'", company_name)
        return []

    hrefs = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"|href="([^"]+)"[^>]*class="result__a"',
        html,
        flags=re.IGNORECASE,
    )
    titles = re.findall(
        r'class="result__a"[^>]*>(.*?)</a>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    flat_hrefs = [a or b for a, b in hrefs]
    out: List[Dict[str, str]] = []
    seen: set = set()
    for i, href in enumerate(flat_hrefs):
        if len(out) >= limit:
            break
        link = href.strip()
        if "uddg=" in link:
            m = re.search(r"[?&]uddg=([^&]+)", link)
            if m:
                link = unquote(m.group(1))
        if link.startswith("//"):
            link = "https:" + link
        title = _clean_html(titles[i]) if i < len(titles) else ""
        desc = _clean_html(snippets[i]) if i < len(snippets) else ""
        _append_article(
            out,
            seen,
            title=title,
            url=link,
            snippet=desc,
            query=f"duckduckgo:{company_name}",
        )
    return out


def _search_firecrawl(
    company_name: str,
    api_key: str,
    industry_hint: str,
) -> List[Dict[str, str]]:
    from firecrawl import Firecrawl

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
                _append_article(
                    articles,
                    seen_urls,
                    title=title,
                    url=url,
                    snippet=snippet,
                    query=query,
                )

    _run_queries(queries)
    if len(articles) < MIN_ARTICLES_BEFORE_EXTENDED:
        _run_queries(extended)

    return articles


def _search_fallback(company_name: str) -> List[Dict[str, str]]:
    """Combine free sources when Firecrawl is unavailable or returns nothing."""
    seen: set = set()
    articles: List[Dict[str, str]] = []

    for batch in (
        _search_google_news_rss(company_name),
        _search_bing_news_rss(company_name),
        _search_duckduckgo_news(company_name),
    ):
        for a in batch:
            _append_article(
                articles,
                seen,
                title=a.get("title", ""),
                url=a.get("url", ""),
                snippet=a.get("snippet", ""),
                query=a.get("query", "fallback"),
            )
            if len(articles) >= MAX_ARTICLES_PER_COMPANY:
                return articles
    return articles


def search_regulatory_news(
    company_name: str,
    industry_hint: str = "",
) -> List[Dict[str, str]]:
    """
    Search regulatory/compliance news for a company.

    Prefers Firecrawl when a real API key is set; otherwise (or when Firecrawl
    returns nothing) falls back to Google News RSS and DuckDuckGo.
    """
    api_key = _firecrawl_key_usable()
    articles: List[Dict[str, str]] = []

    if api_key:
        try:
            articles = _search_firecrawl(company_name, api_key, industry_hint)
        except Exception as exc:
            print(f"Firecrawl news search failed for '{company_name}': {exc}")
            articles = []
    else:
        print(
            f"FIRECRAWL_API_KEY missing/placeholder — "
            f"using news fallback for '{company_name}'"
        )

    if len(articles) < MIN_ARTICLES_BEFORE_EXTENDED:
        fallback = _search_fallback(company_name)
        if fallback:
            seen = {a["url"].lower().rstrip("/") for a in articles}
            for a in fallback:
                key = a["url"].lower().rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                articles.append(a)
                if len(articles) >= MAX_ARTICLES_PER_COMPANY:
                    break

    return articles
