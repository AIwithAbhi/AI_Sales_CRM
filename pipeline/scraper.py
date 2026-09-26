"""Web scraping module using Firecrawl (with BeautifulSoup fallback) for company pages."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from firecrawl import Firecrawl

# Per-page and combined budgets for LLM input
MAX_CHARS_PER_PAGE = 3000
COMBINED_MAX_CHARS = 8000
MAX_EXTRA_PAGES = 2

# Higher priority wins; only fill remaining slots with lower priority.
_HIGH_PRIORITY_PATTERNS: Tuple[str, ...] = (
    "sustainability",
    "sustainable",
    "esg",
    "environment",
    "environmental",
    "climate",
    "decarbon",
    "net-zero",
    "netzero",
    "about-us",
    "about",
    "who-we-are",
    "whoweare",
    "our-story",
    "ourstory",
    "company",
    "our-company",
    "ourcompany",
)

_LOW_PRIORITY_PATTERNS: Tuple[str, ...] = (
    "products",
    "product",
    "solutions",
    "solution",
    "offerings",
    "portfolio",
    "artificial-intelligence",
    "software-driven",
)


def _firecrawl_key() -> str:
    api_key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    low = api_key.lower()
    if (
        not api_key
        or low.startswith("your_")
        or low.endswith("_here")
        or "placeholder" in low
    ):
        return ""
    return api_key


def _normalize_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def _truncate(text: str, max_chars: int) -> str:
    text = _normalize_whitespace(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."


def _host_key(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _same_site(homepage_url: str, candidate_url: str) -> bool:
    home = _host_key(homepage_url)
    cand = _host_key(candidate_url)
    if not home or not cand:
        return False
    return cand == home or cand.endswith("." + home)


def _is_skippable_url(url: str) -> bool:
    low = (url or "").lower().split("#")[0].split("?")[0]
    if not low or low.startswith(("mailto:", "tel:", "javascript:")):
        return True
    if any(
        low.endswith(ext)
        for ext in (
            ".pdf",
            ".doc",
            ".docx",
            ".ppt",
            ".pptx",
            ".xls",
            ".xlsx",
            ".zip",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".mp4",
        )
    ):
        return True
    return False


def _path_tokens(url: str, anchor_text: str = "") -> str:
    """Normalized path + anchor text with separators so substring false-positives are rarer."""
    parsed = urlparse(url)
    path = (parsed.path or "").lower().strip("/")
    # Keep hyphens as separators; wrap with slashes for boundary checks.
    path_norm = "/" + path.replace("_", "-") + "/"
    anchor = " " + (anchor_text or "").lower() + " "
    query = " " + (parsed.query or "").lower() + " "
    return path_norm + anchor + query


def _pattern_in_haystack(pattern: str, haystack: str) -> bool:
    """Match pattern as a path segment or hyphenated token, not a random substring."""
    pat = pattern.lower().strip("/")
    if not pat:
        return False
    # Exact segment: /pat/ or /pat-.../ or /...-pat/
    if f"/{pat}/" in haystack:
        return True
    if f"/{pat}-" in haystack or f"-{pat}/" in haystack:
        return True
    # Multi-word patterns already hyphenated (who-we-are)
    if f"/{pat}" in haystack or f"{pat}/" in haystack:
        # Require non-letter boundaries around bare words like 'about'
        if "-" in pat or len(pat) >= 10:
            return pat in haystack
    # Anchor/query free text: word boundary
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(pat)}(?![a-z0-9])", haystack))


def _match_priority(url: str, anchor_text: str = "") -> Optional[int]:
    """
    Return priority rank (lower = better) if URL/path/anchor matches a target
    pattern, else None.
    """
    haystack = _path_tokens(url, anchor_text)
    for i, pat in enumerate(_HIGH_PRIORITY_PATTERNS):
        if _pattern_in_haystack(pat, haystack):
            return i
    offset = len(_HIGH_PRIORITY_PATTERNS)
    for i, pat in enumerate(_LOW_PRIORITY_PATTERNS):
        if _pattern_in_haystack(pat, haystack):
            return offset + i
    return None


def _link_family(url: str) -> str:
    """Topic family so two ESG pages don't consume both secondary slots."""
    haystack = _path_tokens(url)
    esg_tokens = (
        "sustainability",
        "sustainable",
        "esg",
        "environment",
        "environmental",
        "climate",
        "decarbon",
        "net-zero",
        "netzero",
    )
    about_tokens = (
        "about-us",
        "about",
        "who-we-are",
        "whoweare",
        "our-story",
        "ourstory",
        "company",
        "our-company",
        "ourcompany",
    )
    product_tokens = (
        "products",
        "product",
        "solutions",
        "solution",
        "offerings",
        "portfolio",
        "artificial-intelligence",
        "software-driven",
    )
    if any(_pattern_in_haystack(t, haystack) for t in esg_tokens):
        return "esg"
    if any(_pattern_in_haystack(t, haystack) for t in product_tokens):
        return "products"
    if any(_pattern_in_haystack(t, haystack) for t in about_tokens):
        return "about"
    return "other"


def select_secondary_urls(
    homepage_url: str,
    links: Sequence[str],
    *,
    max_extra: int = MAX_EXTRA_PAGES,
) -> List[str]:
    """
    Pick up to max_extra same-site high-value pages from homepage links.

    Never invents URLs — only absolute URLs derived from links present on the page.
    Priority: sustainability/ESG/about first, then products/solutions if slots remain.
    Diversifies by topic family so two ESG URLs do not fill both slots when a
    products/about page is also linked.
    """
    if max_extra <= 0 or not links:
        return []

    home_norm = (homepage_url or "").split("#")[0].split("?")[0].rstrip("/").lower()
    ranked: List[Tuple[int, str]] = []
    seen = set()

    for raw in links:
        if not raw or not isinstance(raw, str):
            continue
        abs_url = urljoin(homepage_url, raw.strip())
        if _is_skippable_url(abs_url):
            continue
        if not _same_site(homepage_url, abs_url):
            continue
        clean = abs_url.split("#")[0].split("?")[0]
        key = clean.rstrip("/").lower()
        if not key or key == home_norm or key in seen:
            continue
        priority = _match_priority(clean)
        if priority is None:
            continue
        seen.add(key)
        ranked.append((priority, clean))

    ranked.sort(key=lambda item: (item[0], item[1]))

    picked: List[str] = []
    used_families = set()

    def _take(cand: str) -> None:
        picked.append(cand)
        used_families.add(_link_family(cand))

    # First pick: best priority overall
    if ranked:
        _take(ranked[0][1])

    # Second pick: diversify. If first was ESG, prefer products/solutions
    # (Bosch-style AI/software signals) over another about/company page.
    if max_extra > 1 and ranked:
        prefer_order: List[str] = []
        first_family = next(iter(used_families)) if used_families else ""
        if first_family == "esg":
            prefer_order = ["products", "about", "other"]
        elif first_family == "about":
            prefer_order = ["esg", "products", "other"]
        elif first_family == "products":
            prefer_order = ["esg", "about", "other"]
        else:
            prefer_order = ["esg", "about", "products", "other"]

        for family in prefer_order:
            if len(picked) >= max_extra:
                break
            for _prio, cand in ranked:
                if cand in picked:
                    continue
                if _link_family(cand) == family:
                    _take(cand)
                    break

    # Fill any remaining slots
    for _prio, cand in ranked:
        if len(picked) >= max_extra:
            break
        if cand in picked:
            continue
        if _link_family(cand) in used_families and len(picked) < max_extra:
            # allow second of same family only if nothing else left
            continue
        _take(cand)

    if len(picked) < max_extra:
        for _prio, cand in ranked:
            if len(picked) >= max_extra:
                break
            if cand not in picked:
                picked.append(cand)

    return picked


def _extract_links_from_html(homepage_url: str, html: str) -> List[str]:
    """Collect hrefs from HTML (nav/footer included) before text cleanup."""
    links: List[str] = []
    seen = set()
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        # Regex fallback if HTML is badly formed
        for match in re.findall(r'href=["\']([^"\']+)["\']', html or "", flags=re.I):
            abs_url = urljoin(homepage_url, match)
            key = abs_url.split("#")[0].rstrip("/").lower()
            if key and key not in seen:
                seen.add(key)
                links.append(abs_url.split("#")[0])
        return links

    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(homepage_url, href)
        key = abs_url.split("#")[0].rstrip("/").lower()
        if key and key not in seen:
            seen.add(key)
            links.append(abs_url.split("#")[0])
    return links


def _text_from_html(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for script in soup(["script", "style", "nav", "footer", "header"]):
        script.decompose()
    text = soup.get_text()
    return _truncate(text, MAX_CHARS_PER_PAGE)


def scrape_page_fallback(url: str) -> Tuple[str, List[str]]:
    """HTTP + BeautifulSoup scrape; returns (text, discovered_links)."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text or ""
        links = _extract_links_from_html(url, html)
        text = _text_from_html(html)
        print(f"Fallback scraping successful for {url} ({len(text)} chars, {len(links)} links)")
        return text, links
    except Exception as e:
        print(f"Fallback scraping error for '{url}': {e}")
        return "", []


def scrape_homepage_fallback(url: str) -> str:
    """Backward-compatible fallback that returns text only."""
    text, _ = scrape_page_fallback(url)
    return text


def _links_from_firecrawl_result(scrape_result: Any) -> List[str]:
    links: List[str] = []
    candidates: Any = None
    if scrape_result is None:
        return links
    if hasattr(scrape_result, "links") and scrape_result.links is not None:
        candidates = scrape_result.links
    elif hasattr(scrape_result, "data") and scrape_result.data is not None:
        data = scrape_result.data
        if hasattr(data, "links") and data.links is not None:
            candidates = data.links
        elif isinstance(data, dict):
            candidates = data.get("links")
    elif isinstance(scrape_result, dict):
        candidates = scrape_result.get("links")

    if not candidates:
        return links
    for item in candidates:
        if isinstance(item, str):
            links.append(item)
        elif isinstance(item, dict) and item.get("url"):
            links.append(str(item["url"]))
        elif hasattr(item, "url") and getattr(item, "url"):
            links.append(str(item.url))
    return links


def _markdown_from_firecrawl_result(scrape_result: Any) -> str:
    markdown = ""
    if scrape_result is None:
        return markdown
    if hasattr(scrape_result, "data") and scrape_result.data:
        data = scrape_result.data
        if hasattr(data, "markdown"):
            markdown = data.markdown or ""
        elif isinstance(data, dict):
            if "error" in data or "state" in data:
                print(f"[ERROR] Firecrawl returned error response: {data}")
                return ""
            markdown = data.get("markdown") or ""
    elif hasattr(scrape_result, "markdown"):
        markdown = scrape_result.markdown or ""
    elif isinstance(scrape_result, dict):
        markdown = scrape_result.get("markdown") or ""
    return markdown or ""


def scrape_page(url: str) -> Tuple[str, List[str]]:
    """
    Scrape one URL. Prefer Firecrawl (markdown + links); fall back to HTTP.

    Returns (text, links). Text is truncated to MAX_CHARS_PER_PAGE.
    """
    api_key = _firecrawl_key()
    if not api_key:
        print("FIRECRAWL_API_KEY not set/placeholder, using fallback scraper")
        return scrape_page_fallback(url)

    try:
        firecrawl = Firecrawl(api_key=api_key)
        print(f"[Firecrawl] Attempting to scrape {url}")
        scrape_result = firecrawl.scrape(
            url,
            formats=["markdown", "links"],
        )
        print(f"[Firecrawl] Response received for {url}")
        markdown = _markdown_from_firecrawl_result(scrape_result)
        links = _links_from_firecrawl_result(scrape_result)
        if not markdown:
            print(f"No content extracted from {url}, using fallback")
            return scrape_page_fallback(url)
        text = _truncate(markdown, MAX_CHARS_PER_PAGE)
        print(
            f"[OK] Firecrawl: Successfully scraped {url} "
            f"({len(text)} chars, {len(links)} links)"
        )
        return text, links
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Firecrawl error for '{url}': {e}")
        if "Payment Required" in error_msg or "insufficient credits" in error_msg.lower():
            print("Insufficient Firecrawl credits, using fallback scraper")
            return scrape_page_fallback(url)
        # Transient Firecrawl failure — still try free HTTP
        return scrape_page_fallback(url)


def scrape_homepage(url: str) -> str:
    """
    Scrape homepage text only (backward compatible).

    Prefer scrape_company_site() for deep scrape + metadata.
    """
    text, _ = scrape_page(url)
    return text


def _append_within_budget(
    parts: List[str],
    section: str,
    budget: int,
) -> int:
    """Append section to parts within remaining budget; return new remaining."""
    remaining = budget
    if remaining <= 0 or not section:
        return remaining
    if len(section) <= remaining:
        parts.append(section)
        return remaining - len(section)
    truncated = _truncate(section, remaining)
    if truncated:
        parts.append(truncated)
        return 0
    return remaining


def scrape_company_site(
    url: str,
    *,
    max_extra_pages: int = MAX_EXTRA_PAGES,
    combined_max_chars: int = COMBINED_MAX_CHARS,
) -> Dict[str, Any]:
    """
    Scrape homepage plus up to max_extra_pages high-value linked pages.

    Secondary pages are selected only from links present on the homepage
    (never invented). Combined text is capped at combined_max_chars
    (homepage first, then secondary pages).

    Returns:
      text, scrape_pages_used, scrape_page_urls, homepage_chars, combined_chars
    """
    empty: Dict[str, Any] = {
        "text": "",
        "scrape_pages_used": 0,
        "scrape_page_urls": [],
        "homepage_chars": 0,
        "combined_chars": 0,
    }
    if not url:
        return empty

    home_text, home_links = scrape_page(url)
    if not home_text:
        return empty

    page_urls = [url]
    parts: List[str] = []
    remaining = combined_max_chars

    home_section = f"=== Homepage ({url}) ===\n{home_text}"
    remaining = _append_within_budget(parts, home_section, remaining)

    secondary = select_secondary_urls(url, home_links, max_extra=max_extra_pages)
    for sec_url in secondary:
        if remaining <= 200:
            break
        sec_text, _ = scrape_page(sec_url)
        if not sec_text:
            continue
        path = urlparse(sec_url).path or sec_url
        sec_section = f"\n\n=== Secondary page ({path}) ===\nURL: {sec_url}\n{sec_text}"
        before = remaining
        remaining = _append_within_budget(parts, sec_section, remaining)
        if remaining < before:
            page_urls.append(sec_url)

    combined = "".join(parts)
    return {
        "text": combined,
        "scrape_pages_used": len(page_urls),
        "scrape_page_urls": page_urls,
        "homepage_chars": len(home_text),
        "combined_chars": len(combined),
    }
