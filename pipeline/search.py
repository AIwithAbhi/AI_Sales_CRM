"""Web search module using Firecrawl to find company homepage URLs."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from firecrawl import Firecrawl

from utils.url_validation import (
    EXCLUDED_DOMAINS,
    build_alternate_urls,
    domain_brand_matches_company,
    resolve_valid_company_url,
    validate_company_url,
)

logger = logging.getLogger(__name__)


def _firecrawl_key_usable() -> Optional[str]:
    key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    if not key:
        return None
    low = key.lower()
    if low.startswith("your_") or low.endswith("_here") or "placeholder" in low:
        return None
    return key


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


def _result_fields(result) -> Tuple[str, str, str]:
    link = getattr(result, "url", result.get("url") if isinstance(result, dict) else "")
    title = getattr(result, "title", result.get("title") if isinstance(result, dict) else "")
    desc = getattr(
        result,
        "description",
        result.get("description") if isinstance(result, dict) else "",
    )
    return str(link or ""), str(title or ""), str(desc or "")


def _brand_from_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host or "." not in host:
        return ""
    # Prefer second-level label: siemens.com → siemens; jobs.siemens.com → siemens
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"}:
        label = parts[-3]
    else:
        label = parts[-2] if len(parts) >= 2 else parts[0]
    # Skip generic hosts
    if label in {"google", "bing", "yahoo", "linkedin", "facebook", "twitter", "youtube"}:
        return ""
    return label


def _brand_from_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    # "Jobs & careers | Siemens" → Siemens; "Siemens home | Siemens" → Siemens
    if "|" in t:
        t = t.split("|")[-1].strip()
    t = re.sub(r"\s*[-–—]\s*.*$", "", t).strip()
    # Drop leading boilerplate
    for prefix in ("Jobs at ", "Welcome to ", "About ", "Contact "):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix) :].strip()
    # First 1–3 words as brand candidate
    words = [w for w in re.split(r"\s+", t) if w]
    if not words:
        return ""
    # Prefer single distinctive token when short
    if len(words) == 1:
        return re.sub(r"[^\w]", "", words[0]).lower()
    # Multi-word: take until a connector
    stop = {"home", "jobs", "careers", "products", "official", "website", "inc", "llc", "ltd"}
    kept: List[str] = []
    for w in words[:3]:
        wl = re.sub(r"[^\w]", "", w).lower()
        if wl in stop:
            break
        kept.append(wl)
    return "".join(kept) if kept else ""


def _levenshtein(a: str, b: str) -> int:
    a, b = a.lower(), b.lower()
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins, delete, sub = cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def _names_close(query: str, brand: str) -> bool:
    q = re.sub(r"[^\w]", "", (query or "").lower())
    b = re.sub(r"[^\w]", "", (brand or "").lower())
    if not q or not b or q == b:
        return False
    if abs(len(q) - len(b)) > 3:
        return False
    ratio = SequenceMatcher(None, q, b).ratio()
    dist = _levenshtein(q, b)
    # Semines↔Siemens: ratio ~0.71, dist 3 — allow for length ≥ 6
    max_dist = 3 if len(q) >= 6 else 2
    return ratio >= 0.70 or dist <= max_dist


def _infer_typo_suggestion(
    company_name: str,
    hit_rows: List[Dict[str, str]],
) -> Optional[Dict[str, Any]]:
    """
    If Firecrawl hits strongly cluster on one nearby brand, suggest it.

    Evidence-only: never invent a brand that did not appear in search results.
    """
    if not hit_rows:
        return None

    brand_votes: Counter = Counter()
    brand_urls: Dict[str, List[str]] = {}
    brand_titles: Dict[str, str] = {}

    for row in hit_rows:
        url = row.get("url") or ""
        title = row.get("title") or ""
        host_brand = _brand_from_host(url)
        title_brand = _brand_from_title(title)
        # Prefer host brand when title agrees or is empty
        brand = host_brand or title_brand
        if host_brand and title_brand and host_brand != title_brand:
            # Prefer host when title is noisy
            brand = host_brand
        if not brand or len(brand) < 3:
            continue
        brand_votes[brand] += 1
        brand_urls.setdefault(brand, [])
        if url and url not in brand_urls[brand]:
            brand_urls[brand].append(url)
        if title and brand not in brand_titles:
            brand_titles[brand] = title

    if not brand_votes:
        return None

    top_brand, top_count = brand_votes.most_common(1)[0]
    total = sum(brand_votes.values())
    if top_count < 3 and top_count / max(total, 1) < 0.6:
        return None
    if top_count / max(total, 1) < 0.5:
        return None

    q_compact = re.sub(r"[^\w]", "", company_name.lower())
    if top_brand == q_compact:
        return None
    if not _names_close(company_name, top_brand):
        return None

    # Display name: capitalize like Siemens from brand token
    display = brand_titles.get(top_brand) or top_brand
    # Clean display to a short company label when title is long
    if "|" in display:
        display = display.split("|")[-1].strip()
    display = re.sub(r"\s*[-–—].*$", "", display).strip() or top_brand.title()
    # If display still doesn't look like the brand, use Title Case brand
    if top_brand not in re.sub(r"[^\w]", "", display.lower()):
        display = top_brand.title()

    urls = brand_urls.get(top_brand) or []
    # Prefer corporate apex / main homepage over careers/portal deep links
    preferred = (
        max(urls, key=lambda u: (_corporate_homepage_score(u, top_brand), -len(u)))
        if urls
        else ""
    )

    # Canonical display for close brands (Semines → Siemens)
    if top_brand.lower() == "siemens":
        display = "Siemens"
    elif len(display) > 48:
        display = top_brand.title()

    return {
        "suggested_name": display,
        "suggested_brand": top_brand,
        "suggested_url": preferred or "",
        "evidence_count": top_count,
        "evidence_total": total,
        "reason": f"Did you mean {display}?",
        "snippet": (brand_titles.get(top_brand) or "")[:160],
    }


_PORTAL_SUBS = {
    "my", "mydhl", "login", "portal", "app", "apps", "account", "accounts",
    "express", "ship", "shipping", "tracking", "track", "mail", "webmail",
    "cloud", "api", "cdn", "shop", "store", "checkout", "pay", "billing",
    "support", "help", "jobs", "careers", "career", "signin", "signup",
}


def _is_service_portal_url(url: str) -> bool:
    """
    True for customer portals / login / tracking / shop hosts.

    Example: mydhl.express.dhl — never a corporate homepage.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "/").lower()
    if not host:
        return False
    labels = host.split(".")
    # Multi-label service hosts (mydhl.express.dhl)
    if len(labels) >= 3:
        sub = labels[0]
        if sub in _PORTAL_SUBS or any(
            sub.startswith(p) for p in ("my", "login", "portal", "app", "track")
        ):
            return True
        if any(x in labels[:-1] for x in ("express", "portal", "login", "shop", "store")):
            return True
    if any(
        x in path
        for x in (
            "/login", "/signin", "/account", "/portal", "/app/",
            "/tracking", "/track/", "/checkout", "/cart",
        )
    ):
        return True
    return False


def _corporate_homepage_score(url: str, company_name: str = "") -> int:
    """
    Higher = more likely a corporate apex / main homepage.

    Prefers brand.com / www.brand.com over service-portal subdomains
    (e.g. mydhl.express.dhl) and deep paths. Lookalike brands score low.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return -100
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "/").lower().rstrip("/") or "/"
    if not host:
        return -100

    if _is_service_portal_url(url):
        return -80

    # Hard reject lookalikes in ranking (dhlexported.com for DHL)
    if not domain_brand_matches_company(url, company_name):
        return -50

    score = 0
    labels = host.split(".")
    # Apex: brand.tld (2 labels) or brand.co.uk-style (3 with co/com)
    is_apex = len(labels) == 2 or (
        len(labels) == 3 and labels[-2] in {"co", "com", "org", "net", "gov", "ac"}
    )
    if is_apex:
        score += 80
    elif len(labels) == 3:
        score += 30  # regional or single subdomain (us.vestas.com)
    else:
        score -= 40

    # Homepage-looking paths
    if path in ("", "/"):
        score += 50
    elif path in ("/en", "/en-us", "/en_us", "/us", "/uk", "/home", "/global"):
        score += 35
    elif any(x in path for x in ("/jobs", "/career", "/careers", "/support", "/contact")):
        score -= 25
    else:
        score -= min(20, len(path) // 4)

    slug = re.sub(r"[^\w]", "", (company_name or "").lower())
    brand = _brand_from_host(url)
    brand_n = re.sub(r"[^a-z0-9]", "", brand)
    if brand_n and slug and brand_n == slug and is_apex:
        score += 45
    elif brand_n and slug and is_apex:
        score += 15

    return score

def _text_mentions_company(text: str, company_name: str) -> bool:
    """Lightweight company-token check against scraped text."""
    from utils.url_validation import _normalize_tokens, _slugify

    snippet = (text or "").lower()
    if not snippet:
        return False
    tokens = _normalize_tokens(company_name)
    if not tokens:
        return False
    slug = _slugify(company_name)
    if slug and len(slug) >= 4 and slug in snippet.replace(" ", "").replace("-", ""):
        return True
    hits = sum(1 for t in tokens if t in snippet)
    if len(tokens) == 1:
        return hits >= 1
    if len(tokens) == 2:
        return hits >= 2
    return hits >= max(2, (len(tokens) + 1) // 2)


def _firecrawl_confirms_company(url: str, company_name: str) -> bool:
    """
    When local HTTP validation times out, ask Firecrawl whether the page
    is reachable and mentions the company (corporate apex fallback).
    """
    api_key = _firecrawl_key_usable()
    if not api_key:
        return False
    try:
        firecrawl = Firecrawl(api_key=api_key)
        scraped = firecrawl.scrape(url, formats=["markdown"])
        md = ""
        if hasattr(scraped, "markdown"):
            md = scraped.markdown or ""
        elif isinstance(scraped, dict):
            md = scraped.get("markdown") or ""
            data = scraped.get("data") if isinstance(scraped.get("data"), dict) else {}
            if not md and data:
                md = data.get("markdown") or ""
        return _text_mentions_company(md[:8000], company_name)
    except Exception as exc:
        logger.warning("Firecrawl confirm failed for %s: %s", url, exc)
        return False


def _validate_with_telemetry(
    company_name: str,
    candidate_urls: List[str],
) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """
    Try candidates then alternates; prefer corporate apex over portals.

    Never selects service-portal / lookalike domains as the homepage.
    When a high-scoring corporate apex times out via local HTTP (common for
    dhl.com from some networks), fall back to Firecrawl confirmation.
    """
    ordered: List[str] = []
    for u in candidate_urls:
        if u and u not in ordered:
            ordered.append(u)
    for u in build_alternate_urls(company_name):
        if u not in ordered:
            ordered.append(u)

    ordered.sort(
        key=lambda u: (-_corporate_homepage_score(u, company_name), len(u))
    )

    rejections: List[Dict[str, str]] = []
    validated: List[str] = []
    for url in ordered[:16]:
        if _is_service_portal_url(url):
            rejections.append({
                "url": url,
                "reason": "Skipped service portal / login / tracking host",
            })
            continue
        if not domain_brand_matches_company(url, company_name):
            rejections.append({
                "url": url,
                "reason": "Skipped lookalike / non-matching domain brand",
            })
            continue

        ok, result = validate_company_url(url, company_name)
        if ok:
            final = str(result)
            if _is_service_portal_url(final) or not domain_brand_matches_company(
                final, company_name
            ):
                rejections.append({
                    "url": final,
                    "reason": "Rejected portal or lookalike after redirect",
                })
                continue
            validated.append(final)
            if _corporate_homepage_score(final, company_name) >= 100:
                break
            continue

        reason = str(result)
        rejections.append({"url": url, "reason": reason})
        unreachable = (
            "Unreachable" in reason
            or "timed out" in reason.lower()
            or "timeout" in reason.lower()
            or "Read timed out" in reason
        )
        # Corporate apex only — never promote portals via this fallback
        if (
            unreachable
            and _corporate_homepage_score(url, company_name) >= 100
            and _firecrawl_confirms_company(url, company_name)
        ):
            logger.info(
                "Accepted corporate apex via Firecrawl confirm for '%s': %s",
                company_name,
                url,
            )
            validated.append(url)
            if _corporate_homepage_score(url, company_name) >= 100:
                break

    if not validated:
        return None, rejections

    best = max(
        validated,
        key=lambda u: (_corporate_homepage_score(u, company_name), -len(u)),
    )
    return best, rejections

def _same_entity_brand(a: str, b: str) -> bool:
    """True only for identical or trivial suffix variants — not srk vs srkconsulting."""
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if _levenshtein(a, b) <= 1 and abs(len(a) - len(b)) <= 1:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter):
        rest = longer[len(shorter) :].lstrip("-_")
        if rest in {"inc", "llc", "ltd", "corp", "group", "hq", "co", "io", "ai"}:
            return True
    return False


_NOISE_BRANDS = {
    "contact", "support", "home", "about", "careers", "jobs", "imdb", "wikipedia",
    "linkedin", "facebook", "twitter", "youtube", "biggest", "express", "parcelsapp",
    "shipaparcel", "unitedstatesof", "google", "bing", "yahoo", "reddit", "crunchbase",
    "supportpage", "us", "en", "www", "com", "org", "net",
}


def _norm_brand(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _query_stems(query: str) -> List[str]:
    """Significant tokens from the query used to cluster corporate families."""
    raw = (query or "").strip().lower()
    parts = [p for p in re.split(r"[\s\-_]+", raw) if p]
    stems = [p for p in parts if len(p) >= 4]
    compact = _norm_brand(raw)
    if compact and compact not in stems:
        stems.append(compact)
    # Short abbreviation queries (e.g. SRK, DHL) — use the whole string
    if compact and len(compact) <= 4 and compact not in stems:
        stems.append(compact)
    return stems


def _brands_related(a: str, b: str, query: str) -> bool:
    """
    Related when same brand or both belong to the query's corporate family.

    Short abbreviations still split when a brand does not contain the query
    (srk vs shahrukhkhan), which is the SRK ambiguity case.
    """
    if _same_entity_brand(a, b):
        return True
    na, nb = _norm_brand(a), _norm_brand(b)
    if not na or not nb:
        return False
    for stem in _query_stems(query):
        if len(stem) >= 3 and stem in na and stem in nb:
            return True
    return False


def _hit_mentions_query(row: Dict[str, Any], query: str) -> bool:
    """True when title/snippet clearly references the typed query."""
    q = (query or "").strip().lower()
    if not q:
        return False
    blob = f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()
    if q in blob:
        return True
    tokens = [t for t in re.split(r"\W+", q) if len(t) >= 3]
    if len(tokens) >= 2 and all(t in blob for t in tokens):
        return True
    # Short abbreviation (SRK) appearing as a token
    compact = _norm_brand(q)
    if 2 <= len(compact) <= 4:
        return bool(re.search(rf"\b{re.escape(compact)}\b", blob, re.I))
    return False


def _is_plausible_brand(
    brand: str,
    rows: List[Dict[str, str]],
    query: str,
) -> bool:
    """
    A brand is a plausible match for the query only if it shares the query
    stem, is a near-typo of the query, or its hit text mentions the query.

    Unrelated SERP noise (directories, other companies) is not plausible —
    that is "no confident match", not ambiguity.
    """
    nb = _norm_brand(brand)
    if not nb:
        return False
    for stem in _query_stems(query):
        if len(stem) >= 3 and stem in nb:
            return True
    if _names_close(query, brand):
        return True
    if any(_hit_mentions_query(r, query) for r in rows):
        return True
    return False


def _detect_entity_ambiguity(
    hit_rows: List[Dict[str, Any]],
    query: str,
) -> Tuple[bool, List[str], List[Dict[str, str]]]:
    """
    Detect 2+ clearly different *plausible* entities among search hits.

    Ambiguous = multiple plausible candidates for the same query (e.g. SRK as
    person vs jewelry brand). Unrelated SERP noise with zero plausible
    matches is NOT ambiguous — callers should report "No confident match".

    Related corporate properties (bosch.us / bosch-home.com) count as one entity.
    """
    brand_hits: Dict[str, List[Dict[str, str]]] = {}
    for row in hit_rows:
        if row.get("excluded"):
            continue
        url = row.get("url") or ""
        title = row.get("title") or ""
        brand = _brand_from_host(url)
        if not brand or len(brand) < 2:
            continue
        if brand.lower() in _NOISE_BRANDS or _norm_brand(brand) in _NOISE_BRANDS:
            continue
        brand_hits.setdefault(brand, []).append({
            "url": url,
            "title": title,
            "snippet": (row.get("snippet") or "")[:160],
            "domain": (urlparse(url).hostname or "").removeprefix("www."),
            "brand": brand,
        })

    # Drop brands that are not plausible answers for this query
    brand_hits = {
        b: rows
        for b, rows in brand_hits.items()
        if _is_plausible_brand(b, rows, query)
    }

    labels = sorted(brand_hits.keys(), key=lambda b: (-len(brand_hits[b]), b))
    clusters: List[List[str]] = []
    for label in labels:
        placed = False
        for cluster in clusters:
            if any(_brands_related(label, existing, query) for existing in cluster):
                cluster.append(label)
                placed = True
                break
        if not placed:
            clusters.append([label])

    distinct: List[str] = []
    for cluster in clusters:
        distinct.append(
            sorted(cluster, key=lambda b: (-len(brand_hits.get(b) or []), b))[0]
        )

    # Ambiguous only with 2+ plausible entity clusters
    ambiguous = len(distinct) >= 2
    selectable: List[Dict[str, str]] = []
    if ambiguous:
        for label in distinct[:5]:
            cluster = next(c for c in clusters if label in c)
            rows: List[Dict[str, str]] = []
            for member in cluster:
                rows.extend(brand_hits.get(member) or [])
            if not rows:
                continue
            pick = max(
                rows,
                key=lambda c: (
                    _corporate_homepage_score(c["url"], query),
                    -len(c.get("url") or ""),
                ),
            )
            selectable.append({
                "url": pick["url"],
                "title": pick.get("title") or label,
                "snippet": pick.get("snippet") or "",
                "domain": pick.get("domain") or "",
                "brand": label,
            })
    return ambiguous, distinct, selectable


def discover_company_search(company_name: str) -> Dict[str, Any]:
    """
    Firecrawl-first discovery with telemetry and optional typo suggestion.

    Never auto-switches to a suggested name — caller must ask the user.
    Caps match confidence when multiple distinct entities appear.
    """
    empty: Dict[str, Any] = {
        "url": None,
        "search_context": "",
        "source": "none",
        "candidates": [],
        "rejections": [],
        "typo_suggestion": None,
        "match_ambiguous": False,
        "entity_labels": [],
        "selectable_candidates": [],
    }

    candidate_urls: List[str] = []
    hit_rows: List[Dict[str, Any]] = []
    search_context = ""
    source = "fallback"

    api_key = _firecrawl_key_usable()
    if not api_key:
        logger.warning("FIRECRAWL_API_KEY missing/placeholder, using fallback search")
        url = resolve_valid_company_url(company_name, build_alternate_urls(company_name))
        empty.update({
            "url": url,
            "search_context": f"Validated constructed URL for {company_name}" if url else "",
            "source": "fallback",
        })
        return empty

    try:
        firecrawl = Firecrawl(api_key=api_key)
        query = f"{company_name} official website"
        search_results = firecrawl.search(query=query, limit=10)
        web_results = _extract_web_results(search_results)
        context_parts: List[str] = []

        for result in web_results:
            link, title, desc = _result_fields(result)
            if not link.startswith("http"):
                continue
            excluded = any(domain in link.lower() for domain in EXCLUDED_DOMAINS)
            row = {
                "url": link,
                "title": title,
                "snippet": desc[:220],
                "excluded": excluded,
            }
            hit_rows.append(row)
            if excluded:
                continue
            if link not in candidate_urls:
                candidate_urls.append(link)
            if title or desc:
                context_parts.append(f"Title: {title}\nDescription: {desc}")

        search_context = "\n\n".join(context_parts[:5])
        source = "firecrawl"
        url, rejections = _validate_with_telemetry(company_name, candidate_urls)

        ambiguous, entity_labels, selectable = _detect_entity_ambiguity(
            hit_rows, company_name
        )
        # Ambiguous only with 2+ selectable plausible candidates
        if ambiguous and len(selectable) < 2:
            ambiguous = False
            entity_labels = []
            selectable = []

        typo = None
        if not url and not ambiguous:
            typo = _infer_typo_suggestion(
                company_name, [h for h in hit_rows if not h.get("excluded")]
            )

        return {
            "url": url,
            "search_context": search_context,
            "source": source,
            "candidates": [
                {
                    "url": h["url"],
                    "title": h.get("title") or "",
                    "snippet": h.get("snippet") or "",
                    "excluded": bool(h.get("excluded")),
                }
                for h in hit_rows
            ],
            "rejections": rejections,
            "typo_suggestion": typo,
            "match_ambiguous": ambiguous,
            "entity_labels": entity_labels,
            "selectable_candidates": selectable,
        }
    except Exception as e:
        error_msg = str(e)
        logger.error("Firecrawl search error for '%s': %s", company_name, e)
        if "Payment Required" in error_msg or "insufficient credits" in error_msg.lower():
            logger.warning("Insufficient Firecrawl credits, using fallback search")
            url = resolve_valid_company_url(company_name, build_alternate_urls(company_name))
            return {
                "url": url,
                "search_context": "",
                "source": "fallback",
                "candidates": [],
                "rejections": [],
                "typo_suggestion": None,
                "match_ambiguous": False,
                "entity_labels": [],
                "selectable_candidates": [],
            }
        return empty


def search_company_info_fallback(company_name: str) -> Tuple[Optional[str], str]:
    """
    Fallback when Firecrawl is unavailable: try alternate URL patterns with validation.
    """
    url = resolve_valid_company_url(company_name, build_alternate_urls(company_name))
    if url:
        return url, f"Validated constructed URL for {company_name}"
    logger.warning("Fallback URL validation failed for '%s'", company_name)
    return None, ""


def get_homepage_url(company_name: str) -> Optional[str]:
    """Search the web and return a validated company homepage URL."""
    url, _ = search_company_info(company_name)
    return url


def search_company_info(company_name: str) -> Tuple[Optional[str], str]:
    """
    Search for a company and return (validated_homepage_url, search_context).

    Candidates from Firecrawl are validated (redirects + company-name page check).
    Falls back to alternate URL patterns if search fails or no candidate validates.
    """
    discovered = discover_company_search(company_name)
    url = discovered.get("url")
    ctx = discovered.get("search_context") or ""
    if url:
        return url, ctx
    if discovered.get("source") == "firecrawl":
        # Alternates already attempted inside discover; mirror old fallback message
        logger.warning(
            "No Firecrawl candidate validated for '%s', trying alternates", company_name
        )
        # Alternates already included in discover validation path
        return None, ctx
    return search_company_info_fallback(company_name)
