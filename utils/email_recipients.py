"""Parse and merge alert recipient email lists."""

import os
import re
from typing import List

from dotenv import load_dotenv

# .env must win over stale OS env (e.g. old RESEND_ACCOUNT_EMAIL)
load_dotenv(override=True)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_recipient_emails(primary: str) -> List[str]:
    """
    Parse comma/semicolon-separated emails and merge ALERT_EXTRA_RECIPIENTS from env.
    Deduplicates while preserving order.
    """
    parts: List[str] = []
    extra = os.getenv("ALERT_EXTRA_RECIPIENTS", "")
    for chunk in (primary, extra):
        if not chunk:
            continue
        for piece in chunk.replace(";", ",").split(","):
            e = piece.strip()
            if e and EMAIL_RE.match(e):
                parts.append(e)

    seen_lower: set = set()
    out: List[str] = []
    for e in parts:
        key = e.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            out.append(e)
    return out


def resend_test_mode() -> bool:
    return "resend" in os.getenv("EMAIL_SMTP_HOST", "").lower()


def resend_domain_verified() -> bool:
    return os.getenv("RESEND_DOMAIN_VERIFIED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def resend_account_email() -> str:
    """Resend signup email allowed in test mode; set RESEND_ACCOUNT_EMAIL in .env."""
    return os.getenv("RESEND_ACCOUNT_EMAIL", "").strip()


def resend_test_mode_message() -> str:
    acct = resend_account_email()
    if acct:
        return (
            f"Resend test mode: only {acct} can receive mail until you verify a "
            "domain at resend.com/domains (or set RESEND_DOMAIN_VERIFIED=true)."
        )
    return (
        "Resend test mode: verify a domain at resend.com/domains, or set "
        "RESEND_ACCOUNT_EMAIL to your Resend signup address."
    )


def resend_blocked_recipients(recipients: List[str]) -> List[str]:
    """Block non-account addresses only in Resend test mode without a verified domain."""
    if not resend_test_mode() or resend_domain_verified():
        return []
    allowed = resend_account_email().lower()
    if not allowed:
        return []
    return [r for r in recipients if r.lower() != allowed]
