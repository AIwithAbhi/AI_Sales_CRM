"""Company URL disambiguation: rank candidates and assign match confidence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from utils.url_validation import (
    EXCLUDED_DOMAINS,
    _slugify,
    build_alternate_urls,
    distinctive_company_tokens,
    is_excluded_domain,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Hosts that are almost never a company homepage
NEWS_AND_MEDIA_DOMAINS = [
    "bbc.co.uk",
    "bbc.com",
    "cnn.com",
    "nytimes.com",
    "wsj.com",
    "ft.com",
    "forbes.com",
    "businessinsider.com",
    "techcrunch.com",
    "theguardian.com",
    "washingtonpost.com",
    "dw.com",
    "cnbc.com",
    "yahoo.com",
    "finance.yahoo.com",
    "money.cnn.com",
    "marketwatch.com",
    "seekingalpha.com",
    "web.archive.org",
    "archive.org",
    "medium.com",
    "substack.com",
    "reddit.com",
    "quora.com",
    "tiktok.com",
    "pinterest.com",
]

ARTICLE_PATH_HINTS = (
    "/news/",
    "/article",
    "/articles/",
    "/press/",
    "/press-release",
    "/blog/",
    "/story/",
    "/stories/",
    "/opinion/",
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


_MULTI_PART_PUBLIC_SUFFIXES = (
    "ac.uk",
    "co.uk",
    "gov.uk",
    "org.uk",
    "com.au",
    "co.jp",
    "com.br",
    "co.in",
    "com.mx",
)


def _registrable_hint(host: str) -> str:
    """Best-effort domain core without www / country multi-part TLDs."""
    host = host.lower().removeprefix("www.")
    for suffix in _MULTI_PART_PUBLIC_SUFFIXES:
        if host == suffix:
            return host
        if host.endswith("." + suffix):
            remainder = host[: -(len(suffix) + 1)]
            label = remainder.split(".")[-1] if remainder else host
            return label
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return host


def is_news_or_media_url(url: str) -> bool:
    host = _host(url)
    if any(d in host for d in NEWS_AND_MEDIA_DOMAINS):
        return True
    path = (urlparse(url).path or "").lower()
    return any(h in path for h in ARTICLE_PATH_HINTS)


def _title_relevant_to_company(title: str, company_name: str) -> bool:
    """Same relevance rule as Wikipedia title filter (kept local to avoid cycles)."""
    from utils.url_validation import _ascii_fold

    t = _ascii_fold(title or "").strip().lower()
    n = _ascii_fold(company_name or "").strip().lower()
    if not t or not n:
        return False
    t_parts = t.split()
    n_parts = n.split()
    if (
        len(n_parts) == 1
        and len(t_parts) >= 2
        and t_parts[-1] == n
        and not any(
            k in t
            for k in (
                " company",
                " group",
                " s.a",
                " inc",
                " ltd",
                " corporation",
                " holdings",
            )
        )
    ):
        return False
    if t == n or t.startswith(n + " ") or t.startswith(n + " (") or n in t or t in n:
        return True
    q_toks = set(distinctive_company_tokens(company_name))
    t_toks = set(distinctive_company_tokens(title))
    if not q_toks:
        return False
    return q_toks.issubset(t_toks) or t_toks.issubset(q_toks)


def search_context_matches_company(company_name: str, search_context: str) -> bool:
    """
    True when search/Wikipedia context appears to describe the query company.

    Blocks analyzing foreign-brand extracts (e.g. ESB Business School text
    under an EU Business School or unrelated query) after a scrape failure.
    """
    ctx = (search_context or "").strip()
    name = (company_name or "").strip()
    if not ctx or not name:
        return False

    ctx_low = ctx.lower()
    name_low = name.lower()
    distinctive = distinctive_company_tokens(name)

    # Reject wiki Title: lines that are clearly a different entity.
    titles = [
        line.split(":", 1)[1].strip()
        for line in ctx.splitlines()
        if line.lower().startswith("title:")
    ]
    if titles and not any(_title_relevant_to_company(t, name) for t in titles):
        return False

    if name_low in ctx_low:
        return True
    if distinctive:
        hits = sum(1 for t in distinctive if t in ctx_low)
        return hits >= max(1, (len(distinctive) + 1) // 2)
    # No distinctive tokens and name absent — too weak to trust as this company
    return False


def _is_academic_or_gov_host(host: str) -> bool:
    """True for .edu / .gov / .ac.* style hosts (including country forms like .edu.rs)."""
    h = (host or "").lower().removeprefix("www.")
    labels = [p for p in h.split(".") if p]
    if not labels:
        return False
    if labels[-1] in ("edu", "gov", "mil"):
        return True
    if len(labels) >= 2 and labels[-2] in ("edu", "gov", "ac", "mil"):
        return True
    return False


def _domain_matches_company(url: str, company_name: str) -> bool:
    """True when the host is clearly the company's own domain.

    Exact registrable core (dhl.com) or hyphen-bounded brand labels
    (dhl-usa.com, siemens-energy.com) count. Loose substrings like
    dhlexported.com do NOT — that was selecting the wrong site.
    """
    host = _host(url).removeprefix("www.")
    slug = _slugify(company_name)
    compact = slug.replace("-", "")
    if not compact or len(compact) < 2:
        return False
    core = _registrable_hint(host)
    core_compact = core.replace("-", "")
    if compact == core or compact == core_compact or slug == core:
        return True
    # Hyphen-bounded brand in any label before the public suffix
    labels = [p for p in host.split(".") if p]
    for label in labels[:-1]:
        label_compact = label.replace("-", "")
        if label_compact == compact:
            return True
        if label.startswith(compact + "-") or label.startswith(slug + "-"):
            return True
        if label.endswith("-" + compact) or label.endswith("-" + slug):
            return True
    return False


def _exact_company_core_domain(url: str, company_name: str) -> bool:
    """True when registrable domain core equals the company slug (dhl.com)."""
    host = _host(url).removeprefix("www.")
    compact = _slugify(company_name).replace("-", "")
    if not compact:
        return False
    core = _registrable_hint(host).replace("-", "")
    return compact == core


def _brand_apex_host(url: str, company_name: str) -> Optional[str]:
    """Return apex host (siemens.com) when URL is on the company core domain."""
    host = _host(url).removeprefix("www.")
    if not host or not _exact_company_core_domain(url, company_name):
        return None
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return None
    apex = f"{parts[-2]}.{parts[-1]}"
    if _exact_company_core_domain(f"https://{apex}/", company_name):
        return apex
    return None


def _is_brand_apex_url(url: str, company_name: str) -> bool:
    """True for www.brand.tld / brand.tld (not cn.brand.tld)."""
    host = _host(url).removeprefix("www.")
    apex = _brand_apex_host(url, company_name)
    return bool(apex and host == apex)


# Wikipedia extlinks often include directories/sponsors — never treat as official.
_WIKI_NON_OFFICIAL_HOST_PARTS = (
    "register",
    "directory",
    "chamber",
    "association",
    "facebook",
    "linkedin",
    "twitter",
    "youtube",
    "instagram",
    "crunchbase",
    "bloomberg",
    "reuters",
    "wikipedia",
    "wikimedia",
    "sponsor",
    "partners",
    "agep.",
    "ccig.",
)


def _wiki_link_plausible_official(url: str) -> bool:
    """False for directories/social/news that appear in Wikipedia extlinks."""
    host = _host(url).removeprefix("www.")
    if not host or is_excluded_domain(url) or is_news_or_media_url(url):
        return False
    if any(p in host for p in _WIKI_NON_OFFICIAL_HOST_PARTS):
        return False
    return True


def _short_brand_domain(url: str) -> bool:
    """True for very short corporate cores like se.com / ibm.com / 3m.com."""
    host = _host(url).removeprefix("www.")
    # Never treat academic/country compound suffixes as short brands
    # (british-history.ac.uk must not look like brand "ac").
    if any(host == s or host.endswith("." + s) for s in _MULTI_PART_PUBLIC_SUFFIXES):
        return False
    core = _registrable_hint(host)
    compact = core.replace("-", "")
    return 2 <= len(compact) <= 4


def _short_brand_apex_host(url: str) -> Optional[str]:
    """Return apex host for short brands (se.com from blog.se.com)."""
    if not _short_brand_domain(url):
        return None
    host = _host(url).removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return None
    return f"{parts[-2]}.{parts[-1]}"


def _is_brand_subdomain(url: str) -> bool:
    """True when URL is a subdomain of the registrable host (blog.se.com)."""
    host = _host(url).removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    return len(parts) >= 3


def _label_has_token(host: str, token: str) -> bool:
    """True when token matches a DNS label exactly or via hyphen boundaries."""
    if not token or len(token) < 2:
        return False
    labels = [p for p in (host or "").lower().removeprefix("www.").split(".") if p]
    for label in labels[:-1] if len(labels) > 1 else labels:
        lc = label.replace("-", "")
        if (
            lc == token
            or label == token
            or label.startswith(token + "-")
            or label.endswith("-" + token)
            or f"-{token}-" in f"-{label}-"
        ):
            return True
    return False


def _candidate_has_distinctive_domain(
    candidate: Dict[str, Any],
    company_name: str,
) -> bool:
    """True when domain is official or contains a distinctive company token."""
    if candidate.get("is_official_domain"):
        return True
    host = (_host(str(candidate.get("url") or "")) or "").lower()
    return any(
        _label_has_token(host, t) for t in distinctive_company_tokens(company_name)
    )


def _path_depth(url: str) -> int:
    path = (urlparse(url).path or "/").strip("/")
    if not path:
        return 0
    return len([p for p in path.split("/") if p])


def score_candidate(
    *,
    company_name: str,
    url: str,
    title: str = "",
    snippet: str = "",
    reachable: Optional[bool] = None,
    wiki_backed: bool = False,
) -> Dict[str, Any]:
    """
    Score one candidate homepage. Higher is better.

    Official/slug domains score high even when currently unreachable so they
    can still be selected/listed when scrape may fail.
    wiki_backed: URL came from a Wikipedia page whose title matches the query
    (covers short official domains like se.com for Schneider Electric).
    """
    host = _host(url)
    domain = host.removeprefix("www.")
    score = 0
    reasons: List[str] = []
    name_low = company_name.strip().lower()
    title_low = (title or "").strip().lower()
    snippet_low = (snippet or "").strip().lower()
    exact_title = title_low == name_low or title_low.startswith(name_low + " ")

    if is_excluded_domain(url):
        return {
            "url": url,
            "domain": domain,
            "title": title or domain,
            "snippet": (snippet or "")[:220],
            "score": -100,
            "reachable": reachable,
            "is_official_domain": False,
            "reasons": ["excluded blocked domain"],
        }

    # News/media hosts stay blocked. Article-shaped paths on the company's
    # own domain (Wikipedia often links /press/ archives) still count as
    # evidence for that domain — scored with a deep-path penalty below.
    official = _domain_matches_company(url, company_name)
    if is_news_or_media_url(url) and not official:
        return {
            "url": url,
            "domain": domain,
            "title": title or domain,
            "snippet": (snippet or "")[:220],
            "score": -100,
            "reachable": reachable,
            "is_official_domain": False,
            "reasons": ["excluded news/media or blocked domain"],
        }
    if official:
        score += 80
        reasons.append("domain matches company name")
        # Prefer real corporate TLDs over speculative alternates (.io/.co)
        tld = "." + domain.rsplit(".", 1)[-1] if "." in domain else ""
        tld_boost = {
            ".com": 25,
            ".net": 15,
            ".org": 12,
            ".edu": 10,
            ".co": 0,
            ".io": -15,
        }.get(tld, 0)
        score += tld_boost
        if tld_boost:
            reasons.append(f"tld preference {tld}")
        # Exact core (dhl.com) beats hyphenated regional (dhl-usa.com)
        if _exact_company_core_domain(url, company_name):
            score += 20
            reasons.append("exact company-core domain")
            if _is_brand_apex_url(url, company_name):
                score += 15
                reasons.append("brand apex host")
            else:
                score -= 20
                reasons.append("regional/country subdomain of brand")
    else:
        # Only distinctive tokens (not "business"/"school"/…) may boost a domain.
        # Require hyphen/label boundaries so "dhl" does not match "dhlexported".
        distinctive = distinctive_company_tokens(company_name)
        matched = [t for t in distinctive if _label_has_token(host, t)]
        if matched:
            score += 25
            reasons.append(
                "domain contains distinctive company token ("
                + ", ".join(matched[:3])
                + ")"
            )

    depth = _path_depth(url)
    if depth == 0:
        score += 20
        reasons.append("homepage root path")
    elif depth == 1:
        score += 8
    elif depth >= 3:
        score -= 15
        reasons.append("deep path")

    if exact_title:
        score += 25
        reasons.append("exact title match")
    elif name_low and name_low in title_low:
        score += 12
        reasons.append("title contains company name")

    if any(
        k in snippet_low
        for k in (
            "multinational",
            "fortune",
            "employees",
            "headquarter",
            "global logistics",
            "official website",
        )
    ):
        score += 10
        reasons.append("corporate snippet signals")

    if any(k in host for k in ("-usa", "-uk", "franchise")):
        score -= 10
        reasons.append("possible regional/subsidiary host")

    if reachable is True:
        score += 5
    elif reachable is False and official:
        reasons.append("official domain (currently unreachable)")
    elif reachable is False:
        score -= 25
        reasons.append("unreachable")

    # Academic/gov TLDs are almost never the corporate homepage unless the
    # domain itself matches the company name (e.g. stanford.edu). Soften the
    # penalty when the candidate title is an exact company match (typical for
    # Wikipedia-homed school/university official sites like euruni.edu).
    if _is_academic_or_gov_host(host) and not official:
        if title_low == name_low or title_low.startswith(name_low + " "):
            score -= 10
            reasons.append("academic/gov TLD with matching title")
        else:
            score -= 45
            reasons.append("academic/gov TLD without name match")

    # Title/snippet alone must not produce Medium+ confidence — polluted search
    # results often copy the query into <title> for unrelated pages.
    # Require a distinctive token in the domain (generic words like "business"
    # do not count — otherwise "EU Business School" → esb-business-school.de).
    # Exception: Wikipedia-backed homepage links from a matching page title
    # (e.g. Schneider Electric → se.com) are real official-site evidence.
    distinctive = distinctive_company_tokens(company_name)
    has_domain_evidence = official or any(
        _label_has_token(host, t) for t in distinctive
    )
    wiki_homepage = bool(
        wiki_backed
        and exact_title
        and _path_depth(url) <= 1
        and _wiki_link_plausible_official(url)
    )
    # Only short brand domains (se.com) or already-evidenced hosts get the
    # uncapped wiki boost — blocks swissprivateschoolregister-style extlinks.
    if wiki_homepage and not has_domain_evidence and _short_brand_domain(url):
        if _is_brand_subdomain(url):
            # blog.se.com etc. — evidence for the brand, but prefer apex
            score += 20
            reasons.append("Wikipedia-linked short-brand subdomain")
            has_domain_evidence = True
        else:
            score += 45
            reasons.append("Wikipedia-linked short official domain from matching page")
            has_domain_evidence = True
    elif wiki_homepage and not has_domain_evidence:
        # Mild boost for plausible wiki homepages (euruni.edu) but keep cap
        # unless distinctive tokens appear — avoids High on random directories.
        score += 10
        reasons.append("Wikipedia-linked homepage (no domain token match)")
    if not has_domain_evidence and score > 35:
        score = 35
        reasons.append("capped — no distinctive company tokens in domain")

    return {
        "url": url,
        "domain": domain,
        "title": title or domain,
        "snippet": (snippet or "")[:220],
        "score": score,
        "reachable": reachable,
        "is_official_domain": official,
        "wiki_backed": bool(wiki_backed),
        "reasons": reasons,
    }


def _probe_reachable(url: str, timeout: float = 2.5) -> Tuple[str, bool]:
    """Return (final_url_or_original, reachable). Short timeout — fail fast.

    Single streaming GET (not HEAD→GET) — many hosts reject HEAD and the
    double round-trip was doubling probe latency on slow sites.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(
            url, headers=headers, allow_redirects=True, timeout=timeout, stream=True
        )
        try:
            if 200 <= resp.status_code < 400:
                return str(resp.url), True
        finally:
            resp.close()
    except requests.RequestException:
        pass
    return url, False


def rank_company_candidates(
    company_name: str,
    raw_candidates: List[Dict[str, str]],
    *,
    probe: bool = True,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Rank search candidates and decide auto-selection vs ambiguity.

    Scores offline first, then probes only the top few URLs.
    """
    seeded: List[Dict[str, str]] = []
    seen = set()
    for item in raw_candidates:
        url = (item.get("url") or "").strip()
        if not url or not url.startswith("http"):
            continue
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        wiki_backed = bool(item.get("wiki_backed") or item.get("_wiki_backed"))
        seeded.append({
            "url": url,
            "title": item.get("title") or "",
            "snippet": item.get("snippet") or item.get("description") or "",
            "wiki_backed": wiki_backed,
        })
        # Wikipedia often links /press/ archives on the real corporate domain.
        # Promote the brand apex homepage as search-evidenced (not a speculative seed).
        apex = _brand_apex_host(url, company_name)
        if apex:
            home = f"https://www.{apex}/"
            home_key = home.rstrip("/").lower()
            if home_key not in seen:
                seen.add(home_key)
                seeded.append({
                    "url": home,
                    "title": item.get("title") or company_name,
                    "snippet": item.get("snippet")
                    or f"Homepage derived from search hit on {apex}",
                    "wiki_backed": wiki_backed,
                })
        # Wiki-backed short domains (se.com): also seed https://www. form of host
        elif wiki_backed and _path_depth(url) <= 1:
            host = _host(url).removeprefix("www.")
            if host:
                home = f"https://www.{host}/"
                home_key = home.rstrip("/").lower()
                if home_key not in seen:
                    seen.add(home_key)
                    seeded.append({
                        "url": home,
                        "title": item.get("title") or "",
                        "snippet": item.get("snippet")
                        or f"Homepage derived from Wikipedia link on {host}",
                        "wiki_backed": True,
                    })

    for alt in build_alternate_urls(company_name)[:8]:
        # Prefer .com / www first only — skip speculative .io/.co seeds unless
        # they already appeared in search results.
        if any(alt.endswith(t) for t in (".io", ".co", ".io/", ".co/")):
            continue
        key = alt.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            seeded.append({
                "url": alt,
                "title": company_name,
                "snippet": f"Likely official website for {company_name}",
                "_seeded_alternate": True,
            })

    prelim: List[Dict[str, Any]] = []
    for item in seeded:
        url = item["url"]
        if is_excluded_domain(url) or any(d in url.lower() for d in EXCLUDED_DOMAINS):
            continue
        if is_news_or_media_url(url) and not _domain_matches_company(url, company_name):
            continue
        entry = score_candidate(
            company_name=company_name,
            url=url,
            title=item.get("title") or "",
            snippet=item.get("snippet") or "",
            reachable=None,
            wiki_backed=bool(item.get("wiki_backed")),
        )
        entry["_seeded_alternate"] = bool(item.get("_seeded_alternate"))
        entry["wiki_backed"] = bool(item.get("wiki_backed") or entry.get("wiki_backed"))
        if entry["score"] <= -50:
            continue
        prelim.append(entry)

    prelim.sort(
        key=lambda c: (
            -(c["score"]),
            0 if c.get("is_official_domain") else 1,
            _path_depth(c["url"]),
            len(c.get("domain") or ""),
        )
    )

    # Probe a mix of search hits + seeded alts. Unreachable official .com seeds
    # previously crowded out real (weaker) search results from the top-N window.
    search_hits = [c for c in prelim if not c.get("_seeded_alternate")]
    seeded_hits = [c for c in prelim if c.get("_seeded_alternate")]
    probe_pool: List[Dict[str, Any]] = []
    seen_probe = set()
    for entry in search_hits[:3] + seeded_hits[:2]:
        key = (entry.get("url") or "").rstrip("/").lower()
        if not key or key in seen_probe:
            continue
        seen_probe.add(key)
        probe_pool.append(entry)

    scored: List[Dict[str, Any]] = []

    def _probe_one(entry: Dict[str, Any]) -> Dict[str, Any]:
        url = entry["url"]
        reachable: Optional[bool] = None
        final_url = url
        if probe and (entry.get("is_official_domain") or entry["score"] >= 50):
            # Prefer https for official http:// seeds — one attempt only (no
            # sequential http retry that doubles timeout on dead hosts).
            probe_url = url
            if url.startswith("http://") and entry.get("is_official_domain"):
                probe_url = "https://" + url[len("http://") :]
            final_url, reachable = _probe_reachable(probe_url, timeout=2.5)
            if not reachable and probe_url != url:
                final_url = url
                reachable = False
        rescored = score_candidate(
            company_name=company_name,
            url=final_url,
            title=entry.get("title") or "",
            snippet=entry.get("snippet") or "",
            reachable=reachable,
            wiki_backed=bool(entry.get("wiki_backed")),
        )
        rescored["_seeded_alternate"] = bool(entry.get("_seeded_alternate"))
        rescored["wiki_backed"] = bool(entry.get("wiki_backed") or rescored.get("wiki_backed"))
        if (
            rescored.get("_seeded_alternate")
            and reachable is False
            and rescored.get("is_official_domain")
        ):
            rescored["score"] = min(int(rescored.get("score") or 0), 25)
            rescored["reasons"] = list(rescored.get("reasons") or []) + [
                "seeded alternate unreachable — demoted"
            ]
        return rescored

    # Probe in parallel — sequential 5s×N timeouts were the main match delay.
    if probe and probe_pool:
        workers = min(6, len(probe_pool))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_probe_one, entry) for entry in probe_pool]
            for fut in as_completed(futures):
                scored.append(fut.result())
    else:
        for entry in probe_pool:
            scored.append(_probe_one(entry))

    scored.sort(
        key=lambda c: (
            -(c["score"]),
            0 if c.get("is_official_domain") else 1,
            0 if not _is_brand_subdomain(c.get("url") or "") else 1,
            _path_depth(c["url"]),
            len(c.get("domain") or ""),
        )
    )
    # Drop unreachable seeded-only guesses from the selectable set
    selectable = [
        c
        for c in scored
        if not (
            c.get("_seeded_alternate")
            and c.get("reachable") is False
        )
    ]
    # Prefer real search hits over falling back to unreachable seed list
    top = (selectable[:limit] if selectable else scored[:limit])

    best = top[0] if top else None
    second = top[1] if len(top) > 1 else None
    ambiguous = False
    confidence = "Low"
    reason = "No strong company homepage candidate found"

    if best:
        gap = best["score"] - (second["score"] if second else -999)
        seeded_only = bool(best.get("_seeded_alternate"))
        seeded_unverified = seeded_only and best.get("reachable") is not True
        # Speculative www.slug.com guesses must be live-verified before High/auto-select
        if seeded_unverified:
            confidence = "Low"
            ambiguous = False
            reason = (
                f"Unverified seeded domain guess {best['domain']} "
                f"(not confirmed reachable; no search evidence)"
            )
            top = [
                c
                for c in top
                if not c.get("_seeded_alternate") or c.get("reachable") is True
            ][:limit]
            if not top:
                # Fall back to any non-seed search hits still in scored
                top = [
                    c
                    for c in scored
                    if not (
                        c.get("_seeded_alternate")
                        and c.get("reachable") is False
                    )
                ][:limit]
            if not top:
                return {
                    "candidates": [],
                    "selected": None,
                    "match_confidence": "Low",
                    "match_ambiguous": False,
                    "match_reason": reason,
                    "needs_user_pick": False,
                }
            best = top[0]
            second = top[1] if len(top) > 1 else None
            gap = best["score"] - (second["score"] if second else -999)
            seeded_only = bool(best.get("_seeded_alternate"))
            seeded_unverified = seeded_only and best.get("reachable") is not True
            if seeded_unverified:
                return {
                    "candidates": top,
                    "selected": None,
                    "match_confidence": "Low",
                    "match_ambiguous": len(top) > 1,
                    "match_reason": reason,
                    "needs_user_pick": len(top) > 0,
                }

        if best.get("is_official_domain") and best["score"] >= 70:
            confidence = "High"
            ambiguous = False
            reason = (
                f"Official domain match {best['domain']}"
                + (
                    " (may be slow/unreachable to scrape)"
                    if best.get("reachable") is False
                    else ""
                )
            )
        elif (
            best.get("wiki_backed")
            and best["score"] >= 70
            and _path_depth(best.get("url") or "") <= 1
            and _wiki_link_plausible_official(best.get("url") or "")
            and (
                _short_brand_domain(best.get("url") or "")
                or _candidate_has_distinctive_domain(best, company_name)
            )
        ):
            confidence = "High"
            ambiguous = False
            reason = (
                f"Wikipedia-backed official site {best['domain']}"
                + (
                    " (may be slow/unreachable to scrape)"
                    if best.get("reachable") is False
                    else ""
                )
            )
        elif (
            best["score"] >= 60
            and gap >= 20
            and _candidate_has_distinctive_domain(best, company_name)
        ):
            confidence = "High"
            ambiguous = False
            reason = f"Clear top match {best['domain']} (score gap {gap})"
        elif best["score"] >= 40 and gap >= 10:
            confidence = "Medium"
            ambiguous = True
            reason = f"Likely match {best['domain']}; secondary candidates exist"
        else:
            confidence = "Low"
            ambiguous = len(top) > 1
            reason = f"Weak/ambiguous match; best={best['domain']} score={best['score']}"

    return {
        "candidates": top,
        "selected": best,
        "match_confidence": confidence,
        "match_ambiguous": ambiguous,
        "match_reason": reason,
        # Only High auto-proceeds. Medium/Low always need an explicit pick when
        # there is anything to choose from (interactive "Did you mean?").
        "needs_user_pick": confidence != "High" and len(top) > 0,
    }
