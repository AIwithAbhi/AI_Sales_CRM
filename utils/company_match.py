"""Company URL disambiguation: rank candidates and assign match confidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from utils.url_validation import (
    EXCLUDED_DOMAINS,
    _normalize_tokens,
    _slugify,
    build_alternate_urls,
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


def _registrable_hint(host: str) -> str:
    """Best-effort domain core without www / country multi-part TLDs."""
    host = host.lower().removeprefix("www.")
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


def _domain_matches_company(url: str, company_name: str) -> bool:
    host = _host(url).removeprefix("www.")
    slug = _slugify(company_name)
    compact = slug.replace("-", "")
    core = _registrable_hint(host)
    if not compact or len(compact) < 2:
        return False
    return compact == core or compact in host.replace("-", "").replace(".", "")


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
) -> Dict[str, Any]:
    """
    Score one candidate homepage. Higher is better.

    Official/slug domains score high even when currently unreachable so they
    can still be selected/listed when scrape may fail.
    """
    host = _host(url)
    domain = host.removeprefix("www.")
    score = 0
    reasons: List[str] = []
    name_low = company_name.strip().lower()
    title_low = (title or "").strip().lower()
    snippet_low = (snippet or "").strip().lower()
    tokens = _normalize_tokens(company_name)

    if is_excluded_domain(url) or is_news_or_media_url(url):
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

    official = _domain_matches_company(url, company_name)
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
    elif any(t in host for t in tokens if len(t) >= 3):
        score += 25
        reasons.append("domain contains company token")

    depth = _path_depth(url)
    if depth == 0:
        score += 20
        reasons.append("homepage root path")
    elif depth == 1:
        score += 8
    elif depth >= 3:
        score -= 15
        reasons.append("deep path")

    if title_low == name_low or title_low.startswith(name_low + " "):
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

    return {
        "url": url,
        "domain": domain,
        "title": title or domain,
        "snippet": (snippet or "")[:220],
        "score": score,
        "reachable": reachable,
        "is_official_domain": official,
        "reasons": reasons,
    }


def _probe_reachable(url: str, timeout: int = 6) -> Tuple[str, bool]:
    """Return (final_url_or_original, reachable)."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        if resp.status_code >= 400 or resp.status_code < 200:
            resp = requests.get(
                url, headers=headers, allow_redirects=True, timeout=timeout, stream=True
            )
        if 200 <= resp.status_code < 400:
            return str(resp.url), True
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
        seeded.append({
            "url": url,
            "title": item.get("title") or "",
            "snippet": item.get("snippet") or item.get("description") or "",
        })

    for alt in build_alternate_urls(company_name)[:4]:
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
        )
        entry["_seeded_alternate"] = bool(item.get("_seeded_alternate"))
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

    scored: List[Dict[str, Any]] = []
    for entry in prelim[:6]:
        url = entry["url"]
        reachable: Optional[bool] = None
        final_url = url
        if probe and (entry.get("is_official_domain") or entry["score"] >= 50):
            if url.startswith("http://") and entry.get("is_official_domain"):
                https_url = "https://" + url[len("http://") :]
                final_url, reachable = _probe_reachable(https_url, timeout=5)
                if not reachable:
                    final_url, reachable = _probe_reachable(url, timeout=4)
            else:
                final_url, reachable = _probe_reachable(url, timeout=5)
        rescored = score_candidate(
            company_name=company_name,
            url=final_url,
            title=entry.get("title") or "",
            snippet=entry.get("snippet") or "",
            reachable=reachable,
        )
        rescored["_seeded_alternate"] = bool(entry.get("_seeded_alternate"))
        # Speculative www.company.com seeds that do not resolve must not win.
        # Real search hits that are temporarily slow may stay selectable.
        if (
            rescored.get("_seeded_alternate")
            and reachable is False
            and rescored.get("is_official_domain")
        ):
            rescored["score"] = min(int(rescored.get("score") or 0), 25)
            rescored["reasons"] = list(rescored.get("reasons") or []) + [
                "seeded alternate unreachable — demoted"
            ]
        scored.append(rescored)

    scored.sort(
        key=lambda c: (
            -(c["score"]),
            0 if c.get("is_official_domain") else 1,
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
    top = (selectable or scored)[:limit]

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
        elif best["score"] >= 60 and gap >= 20:
            confidence = "High"
            ambiguous = False
            reason = f"Clear top match {best['domain']} (score gap {gap})"
        elif best["score"] >= 40 and gap >= 10:
            confidence = "Medium"
            ambiguous = bool(second and second["score"] >= 35)
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
        "needs_user_pick": ambiguous and confidence != "High",
    }
