"""Web search module using Firecrawl to find company homepage URLs."""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, unquote, urlparse

import requests
from firecrawl import Firecrawl

from utils.url_validation import (
    EXCLUDED_DOMAINS,
    build_alternate_urls,
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


def _wiki_title_relevant(title: str, company_name: str) -> bool:
    """Keep Wikipedia pages that clearly refer to the queried company."""
    t = (title or "").strip().lower()
    n = (company_name or "").strip().lower()
    if not t or not n:
        return False
    if t == n or n in t or t in n:
        return True
    # Require overlap on distinctive tokens (not just "business"/"school").
    from utils.url_validation import distinctive_company_tokens

    q_toks = set(distinctive_company_tokens(company_name))
    t_toks = set(distinctive_company_tokens(title))
    if not q_toks:
        # Short / generic-heavy names: rely on exact/substring checks above.
        return False
    # All distinctive query tokens should appear in the page title tokens
    # (prevents EU Business School → ESB Business School via shared generics).
    return q_toks.issubset(t_toks) or t_toks.issubset(q_toks)


def _wikipedia_candidate_urls(company_name: str) -> Tuple[List[str], str]:
    """
    Resolve likely official websites via Wikipedia (no API key required).

    Returns (candidate_urls, search_context).
    """
    raw, ctx = _wikipedia_raw_candidates(company_name)
    return [r["url"] for r in raw], ctx


def _wikipedia_raw_candidates(company_name: str) -> Tuple[List[Dict[str, str]], str]:
    """
    Wikipedia pages closely matching company_name → extlinks with page titles.

    Unrelated opensearch hits (e.g. ESB Business School for EU Business School)
    are excluded so their domains cannot be scored as the query company.
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
            timeout=8,
        )
        search.raise_for_status()
        data = search.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Wikipedia search failed for '%s': %s", company_name, exc)
        return [], ""

    titles = list(data[1]) if isinstance(data, list) and len(data) > 1 else []
    if not titles:
        return [], ""

    name_low = company_name.strip().lower()
    relevant = [t for t in titles if _wiki_title_relevant(t, company_name)]
    if not relevant:
        # Fall back to exact / substring only if distinctive-token filter was empty
        relevant = [
            t
            for t in titles
            if t.lower() == name_low
            or name_low in t.lower()
            or t.lower() in name_low
        ]
    ordered_titles = sorted(
        relevant,
        key=lambda t: (
            0 if t.lower() == name_low else
            1 if name_low in t.lower() or t.lower() in name_low else
            2
        ),
    )

    candidates: List[Dict[str, str]] = []
    seen_urls = set()
    context_parts: List[str] = []

    def _fetch_wiki_page(title: str) -> Tuple[str, str, List[str]]:
        """Return (title, extract, extlink urls)."""
        page = requests.get(
            WIKI_API,
            params={
                "action": "query",
                "prop": "extlinks|extracts",
                "titles": title,
                "exintro": 1,
                "explaintext": 1,
                "ellimit": 20,
                "format": "json",
            },
            headers=headers,
            timeout=8,
        )
        page.raise_for_status()
        pages = page.json().get("query", {}).get("pages", {})
        extract = ""
        links: List[str] = []
        for _pid, pdata in pages.items():
            extract = (pdata.get("extract") or "").strip()
            for el in pdata.get("extlinks") or []:
                link = ""
                if isinstance(el, dict):
                    link = str(el.get("*") or el.get("url") or "")
                else:
                    link = str(el)
                if link.startswith("http"):
                    links.append(link)
        return title, extract, links

    # Exact Wikipedia title match is enough — skip the second page fetch.
    if ordered_titles and ordered_titles[0].lower() == name_low:
        titles_to_fetch = ordered_titles[:1]
    else:
        titles_to_fetch = ordered_titles[:2]
    page_results: List[Tuple[str, str, List[str]]] = []
    if len(titles_to_fetch) == 1:
        try:
            page_results.append(_fetch_wiki_page(titles_to_fetch[0]))
        except (requests.RequestException, ValueError, AttributeError) as exc:
            logger.warning(
                "Wikipedia page fetch failed for '%s': %s", titles_to_fetch[0], exc
            )
    elif titles_to_fetch:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {pool.submit(_fetch_wiki_page, t): t for t in titles_to_fetch}
            for fut in as_completed(futs):
                try:
                    page_results.append(fut.result())
                except (requests.RequestException, ValueError, AttributeError) as exc:
                    logger.warning(
                        "Wikipedia page fetch failed for '%s': %s", futs[fut], exc
                    )

    # Preserve title preference order
    order = {t: i for i, t in enumerate(titles_to_fetch)}
    page_results.sort(key=lambda row: order.get(row[0], 99))

    for title, extract, links in page_results:
        if extract:
            context_parts.append(f"Title: {title}\nDescription: {extract[:400]}")
        page_snippet = (
            f"Title: {title}\nDescription: {extract[:280]}"
            if extract
            else f"Wikipedia page: {title}"
        )
        for link in links:
            host = (urlparse(link).hostname or "").lower()
            if any(domain in host for domain in EXCLUDED_DOMAINS):
                continue
            if "wikipedia.org" in host or "wikimedia.org" in host:
                continue
            if "google.com/search" in link.lower():
                continue
            key = link.rstrip("/").lower()
            if key in seen_urls:
                continue
            seen_urls.add(key)
            candidates.append({
                "url": link,
                "title": title,
                "snippet": page_snippet,
            })

    def _rank(item: Dict[str, str]) -> Tuple[int, int]:
        u = item["url"]
        host = (urlparse(u).hostname or "").lower()
        score = 50
        if host.endswith(".edu") or ".edu." in host:
            score -= 10  # softer than before — many schools use .edu
        if "official" in u.lower() or host.startswith("www."):
            score -= 5
        path = urlparse(u).path or "/"
        score += path.count("/")
        return (score, len(host))

    candidates.sort(key=_rank)
    return candidates[:15], "\n\n".join(context_parts[:3])


def _duckduckgo_candidate_urls(company_name: str, limit: int = 10) -> Tuple[List[str], str]:
    """
    Free HTML search fallback when Firecrawl is unavailable.

    Returns (candidate_urls, search_context).
    """
    query = f"{company_name} official website"
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=6)
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


def _collect_fallback_raw_candidates(
    company_name: str,
) -> Tuple[List[Dict[str, str]], str]:
    wiki_raw, wiki_ctx = _wikipedia_raw_candidates(company_name)
    # Skip DuckDuckGo when Wikipedia already gave usable homepage candidates —
    # DDG is often empty/challenged from cloud IPs and only adds latency.
    ddg_urls: List[str] = []
    ddg_ctx = ""
    if len(wiki_raw) < 2 or not wiki_ctx:
        ddg_urls, ddg_ctx = _duckduckgo_candidate_urls(company_name)
    search_context = wiki_ctx or ddg_ctx or ""
    raw: List[Dict[str, str]] = []

    # Prefer official-looking wiki links first (domain contains company slug)
    from utils.company_match import _domain_matches_company

    preferred = [
        r for r in wiki_raw if _domain_matches_company(r["url"], company_name)
    ]
    others = [r for r in wiki_raw if r not in preferred]
    for item in (preferred + others)[:12]:
        raw.append({
            "url": item["url"],
            "title": item.get("title") or company_name,
            "snippet": item.get("snippet")
            or f"Wikipedia-linked site for {company_name}",
        })
    for u in ddg_urls[:5]:
        # Do NOT stamp the query as the result title — that invented
        # "exact title match" boosts for unrelated DuckDuckGo URLs.
        host = (urlparse(u).hostname or "").removeprefix("www.")
        raw.append({
            "url": u,
            "title": host or "",
            "snippet": (ddg_ctx or "")[:220],
        })
    return raw, search_context


def discover_company_match(
    company_name: str,
    *,
    probe: bool = True,
) -> Dict[str, Any]:
    """
    Discover and rank homepage candidates for a company name.

    Returns match metadata including candidates, selected URL, confidence,
    and whether the user should pick (single-search disambiguation).
    """
    from utils.company_match import (
        _brand_apex_host,
        _domain_matches_company,
        rank_company_candidates,
    )

    raw: List[Dict[str, str]] = []
    search_context = ""
    source = "fallback"

    api_key = _firecrawl_key_usable()
    if api_key:
        try:
            firecrawl = Firecrawl(api_key=api_key)
            query = f"{company_name} official website"
            search_results = firecrawl.search(query=query, limit=10)
            web_results = _extract_web_results(search_results)
            context_parts: List[str] = []
            for result in web_results:
                link = getattr(
                    result, "url", result.get("url") if isinstance(result, dict) else ""
                )
                title = getattr(
                    result,
                    "title",
                    result.get("title") if isinstance(result, dict) else "",
                )
                desc = getattr(
                    result,
                    "description",
                    result.get("description") if isinstance(result, dict) else "",
                )
                if not link or not str(link).startswith("http"):
                    continue
                if any(domain in str(link).lower() for domain in EXCLUDED_DOMAINS):
                    continue
                raw.append({
                    "url": str(link),
                    "title": str(title or ""),
                    "snippet": str(desc or ""),
                })
                if title or desc:
                    context_parts.append(f"Title: {title}\nDescription: {desc}")
            search_context = "\n\n".join(context_parts[:5])
            source = "firecrawl"
        except Exception as e:
            logger.error("Firecrawl search error for '%s': %s", company_name, e)
            raw, search_context = _collect_fallback_raw_candidates(company_name)
            source = "fallback"
    else:
        logger.warning(
            "FIRECRAWL_API_KEY missing/invalid placeholder, using fallback search"
        )
        raw, search_context = _collect_fallback_raw_candidates(company_name)

    # Always enrich with Wikipedia context when available
    if not search_context:
        _urls, wiki_ctx = _wikipedia_candidate_urls(company_name)
        search_context = wiki_ctx or ""
        if _urls and not raw:
            # Use host as title — never invent exact title match from the query.
            raw = [
                {
                    "url": u,
                    "title": (urlparse(u).hostname or "").removeprefix("www."),
                    "snippet": (wiki_ctx or "")[:220],
                }
                for u in _urls[:10]
            ]

    ranked = rank_company_candidates(company_name, raw, probe=probe, limit=5)
    selected = ranked.get("selected") or {}
    confidence = ranked.get("match_confidence") or "Low"
    url = selected.get("url") if selected else None

    # Prefer official domain URL even when unreachable (scrape may fail later)
    if selected and selected.get("is_official_domain"):
        url = selected.get("url")
        apex = _brand_apex_host(url or "", company_name) if url else None
        if apex:
            url = f"https://www.{apex}/"
        elif url and url.startswith("http://"):
            url = "https://" + url[len("http://") :]
        else:
            domain = (selected.get("domain") or "").strip().lower().removeprefix("www.")
            if domain and _domain_matches_company(f"https://{domain}/", company_name):
                url = f"https://www.{domain}/"

    # Only High-confidence matches auto-proceed to scrape/score.
    # Medium/Low keep candidates for "Did you mean?" but do not silently commit.
    if confidence != "High":
        url = None

    return {
        "url": url,
        "search_context": search_context or f"Match candidates for {company_name}",
        "candidates": ranked.get("candidates") or [],
        "match_confidence": confidence,
        "match_ambiguous": bool(ranked.get("match_ambiguous")),
        "match_reason": ranked.get("match_reason") or "",
        "needs_user_pick": bool(ranked.get("needs_user_pick")),
        "selected_domain": (selected or {}).get("domain") or "",
        "proposed_url": (selected or {}).get("url") or "",
        "source": source,
    }


def search_company_info_fallback(company_name: str) -> Tuple[Optional[str], str]:
    """
    Fallback when Firecrawl is unavailable: Wikipedia + DuckDuckGo + URL patterns.
    """
    match = discover_company_match(company_name)
    return match.get("url"), match.get("search_context") or ""


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
    Search for a company and return (homepage_url, search_context).

    Uses ranked disambiguation (Firecrawl when available, else Wikipedia/DDG).
    Official domains are preferred even if currently unreachable.
    """
    match = discover_company_match(company_name)
    return match.get("url"), match.get("search_context") or ""
