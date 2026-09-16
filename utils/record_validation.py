"""Pre-Airtable record validation and QA checks."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import requests

from utils.lead_scoring import SIZE_BANDS

logger = logging.getLogger(__name__)

VALID_INDUSTRIES = {
    "Energy",
    "Technology",
    "Finance",
    "Healthcare",
    "Manufacturing",
    "Retail",
    "Consulting",
    "Real Estate",
    "Other",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def url_responds_ok(url: str, timeout: int = 8) -> bool:
    """Return True if URL starts with http and responds with 200–399."""
    if not url or not str(url).startswith("http"):
        return False
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        if resp.status_code >= 400:
            resp = requests.get(
                url, headers=headers, allow_redirects=True, timeout=timeout, stream=True
            )
        return 200 <= resp.status_code < 400
    except requests.RequestException as exc:
        logger.warning("URL check failed for %s: %s", url, exc)
        return False


def validate_lead_record(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate a lead record before Airtable push.

    Checks:
      - URL starts with http and responds 200–399
      - Industry not empty / Unknown
      - B2B buyer is bool
      - Lead score is int 1–10
      - Size estimate is a known band

    Returns:
        (is_valid, list_of_error_messages)
        Invalid records should be flagged review_needed instead of pushed.
    """
    errors: List[str] = []

    url = str(record.get("url") or "").strip()
    if not url.startswith("http"):
        errors.append("URL must start with http:// or https://")
    elif not url_responds_ok(url):
        errors.append(f"URL did not respond with 200–399: {url}")

    industry = str(record.get("industry") or "").strip()
    if not industry or industry.lower() in ("unknown", "n/a", "none"):
        errors.append("Industry must not be empty or Unknown")
    elif industry not in VALID_INDUSTRIES:
        # Allow Other; warn but don't fail unknown-but-present labels
        if industry == "Other":
            pass
        else:
            logger.warning("Non-standard industry '%s' — allowing with review flag", industry)

    b2b = record.get("b2b_buyer")
    if not isinstance(b2b, bool):
        errors.append("B2B buyer must be True or False (never null)")

    score = record.get("lead_score")
    if not isinstance(score, int) or isinstance(score, bool) or score < 1 or score > 10:
        errors.append("Lead score must be an integer between 1 and 10")

    size = str(record.get("size_estimate") or "").strip()
    legacy = {"Small", "Medium", "High"}
    if size not in SIZE_BANDS and size not in legacy:
        errors.append(
            f"Size estimate must be one of {list(SIZE_BANDS)} (got '{size}')"
        )

    company = str(record.get("company_name") or "").strip()
    if not company:
        errors.append("company_name is required")

    if errors:
        logger.warning(
            "Validation failed for '%s': %s", company or "?", "; ".join(errors)
        )
    return len(errors) == 0, errors


def apply_review_flag(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attach review_needed / validation_errors based on QA checks.

    Mutates a copy of the record and returns it.
    """
    out = dict(record)
    ok, errors = validate_lead_record(out)
    out["review_needed"] = not ok
    out["validation_errors"] = errors
    if not ok:
        logger.warning(
            "Flagging '%s' for review: %s",
            out.get("company_name"),
            "; ".join(errors),
        )
    return out
