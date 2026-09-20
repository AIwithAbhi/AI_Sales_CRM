"""Web search module using Firecrawl to find company homepage URLs."""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional, Tuple
from urllib.parse import quote_plus, unquote, urlparse

import requests
from firecrawl import Firecrawl

from utils.url_validation import (
    EXCLUDED_DOMAINS,
    build_alternate_urls,
    resolve_valid_company_url,
)

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
WIKI_API = "https://en.wikipedia.org/w/api.php"


def _firecrawl_key_usable() -> Optional[str]:
    """Return Firecrawl API key only if it looks like a real credential."""
    key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    if not key:
        return None
    low = key.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return None
    return key


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _wikipedia_candidate_urls(company_name: str) -> Tuple[List[str], str]:
    """
    Resolve likely official websites via Wikipedia (no API key required).

    Returns (candidate_urls, search_context).
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        search = requests.get(
            WIKI_API,
            params={
                "action": "opensearch",
                "search": company_name,
                "limit": 5,
                "namespace": 0,
                "format": "json",
            },
            headers=headers,
            timeout=15,
        )
        search.raise_for_status()
        data = search.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Wikipedia search failed for '%s': %s", company_name, exc)
        return [], ""

    titles = list(data[1]) if isinstance(data, list) and len(data) > 1 else []
    if not titles:
        return [], ""

    # Prefer an exact / close title match
    name_low = company_name.strip().lower()
    ordered_titles = sorted(
        titles,
        key=lambda t: (
            0 if t.lower() == name_low else
            1 if name_low in t.lower() or t.lower() in name_low else
            2
        ),
    )

    candidates: List[str] = []
    context_parts: List[str] = []

    for title in ordered_titles[:3]:
        try:
            page = requests.get(
                WIKI_API,
                params={
                    "action": "query",
                    "prop": "extlinks|extracts",
                    "titles": title,
                    "exintro": 1,
                    "explaintext": 1,
                    "ellimit": 30,
                    "format": "json",
                },
                headers=headers,
                timeout=15,
            )
            page.raise_for_status()
            pages = page.json().get("query", {}).get("pages", {})
        except (requests.RequestException, ValueError, AttributeError) as exc:
            logger.warning("Wikipedia page fetch failed for '%s': %s", title, exc)
            continue

        for _pid, pdata in pages.items():
            extract = (pdata.get("extract") or "").strip()
            if extract:
                context_parts.append(f"Title: {title}\nDescription: {extract[:400]}")
            for el in pdata.get("extlinks") or []:
                link = ""
                if isinstance(el, dict):
                    link = str(el.get("*") or el.get("url") or "")
                else:
                    link = str(el)
                if not link.startswith("http"):
                    continue
                host = (urlparse(link).hostname or "").lower()
                if any(domain in host for domain in EXCLUDED_DOMAINS):
                    continue
                if "wikipedia.org" in host or "wikimedia.org" in host:
                    continue
                if link not in candidates:
                    candidates.append(link)

    # Prefer education / shorter official-looking hosts first
    def _rank(u: str) -> Tuple[int, int]:
        host = (urlparse(u).hostname or "").lower()
        score = 50
        if host.endswith(".edu") or ".edu." in host:
            score -= 20
        if "official" in u.lower() or host.startswith("www."):
            score -= 5
        # Prefer roots over deep paths
        path = urlparse(u).path or "/"
        score += path.count("/")
        return (score, len(host))

    candidates.sort(key=_rank)
    return candidates, "\n\n".join(context_parts[:3])


def _duckduckgo_candidate_urls(company_name: str, limit: int = 10) -> Tuple[List[str], str]:
    """
    Free HTML search fallback when Firecrawl is unavailable.

    Returns (candidate_urls, search_context).
    """
    query = f"{company_name} official website"
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        # DDG often returns 202 with a challenge page in cloud IPs
        if resp.status_code >= 400:
            resp.raise_for_status()
        html = resp.text
    except requests.RequestException as exc:
        logger.warning("DuckDuckGo fallback search failed for '%s': %s", company_name, exc)
        return [], ""

    if "result__a" not in html:
        logger.warning("DuckDuckGo returned no result markup for '%s'", company_name)
        return [], ""

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

    candidates: List[str] = []
    context_parts: List[str] = []
    flat_hrefs = [a or b for a, b in hrefs]

    for i, href in enumerate(flat_hrefs):
        link = href.strip()
        if "uddg=" in link:
            m = re.search(r"[?&]uddg=([^&]+)", link)
            if m:
                link = unquote(m.group(1))
        if link.startswith("//"):
            link = "https:" + link
        if not link.startswith("http"):
            continue
        host = (urlparse(link).hostname or "").lower()
        if any(domain in host for domain in EXCLUDED_DOMAINS):
            continue
        if "duckduckgo.com" in host:
            continue
        if link not in candidates:
            candidates.append(link)
        title = _clean_html(titles[i]) if i < len(titles) else ""
        desc = _clean_html(snippets[i]) if i < len(snippets) else ""
        if title or desc:
            context_parts.append(f"Title: {title}\nDescription: {desc}")
        if len(candidates) >= limit:
            break

    return candidates, "\n\n".join(context_parts[:5])


def search_company_info_fallback(company_name: str) -> Tuple[Optional[str], str]:
    """
    Fallback when Firecrawl is unavailable: Wikipedia + DuckDuckGo + URL patterns.
    """
    wiki_urls, wiki_ctx = _wikipedia_candidate_urls(company_name)
    ddg_urls, ddg_ctx = _duckduckgo_candidate_urls(company_name)

    candidates: List[str] = []
    for u in wiki_urls + ddg_urls + build_alternate_urls(company_name):
        if u and u not in candidates:
            candidates.append(u)

    search_context = wiki_ctx or ddg_ctx
    url = resolve_valid_company_url(company_name, candidates)
    if url:
        ctx = search_context or f"Validated fallback URL for {company_name}"
        return url, ctx
    logger.warning("Fallback URL validation failed for '%s'", company_name)
    return None, search_context


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
    Falls back to Wikipedia/DuckDuckGo + alternate URL patterns if search fails.
    """
    candidate_urls: List[str] = []
    search_context = ""

    api_key = _firecrawl_key_usable()
    if not api_key:
        logger.warning(
            "FIRECRAWL_API_KEY missing/invalid placeholder, using fallback search"
        )
        return search_company_info_fallback(company_name)

    try:
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
            "No Firecrawl candidate validated for '%s', trying fallback search",
            company_name,
        )
        return search_company_info_fallback(company_name)

    except Exception as e:
        error_msg = str(e)
        logger.error("Firecrawl search error for '%s': %s", company_name, e)
        logger.warning("Using fallback search after Firecrawl error: %s", error_msg[:120])
        return search_company_info_fallback(company_name)
