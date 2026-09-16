"""SMTP email delivery for regulatory news alerts (Gmail or Resend SMTP)."""

import html as _html
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

from utils.email_recipients import (
    resend_blocked_recipients,
    resend_test_mode_message,
)


def _esc(value: Any) -> str:
    """Escape text for safe inclusion in HTML email bodies."""
    return _html.escape(str(value or ""))


def _smtp_config() -> Dict[str, Any]:
    sender = os.getenv("EMAIL_SENDER", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    # Resend: EMAIL_SMTP_USER=resend; Gmail: leave unset (uses EMAIL_SENDER)
    smtp_user = os.getenv("EMAIL_SMTP_USER", "").strip() or sender
    return {
        "sender": sender,
        "smtp_user": smtp_user,
        "password": password,
        "host": host,
        "port": port,
    }


def smtp_configured() -> bool:
    cfg = _smtp_config()
    return bool(cfg["sender"] and cfg["password"] and cfg["smtp_user"])


def _smtp_connect(cfg: Dict[str, Any]):
    """Connect and authenticate; supports STARTTLS (587) and SSL (465)."""
    if cfg["port"] in (465, 2465):
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=60)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=60)
        server.starttls()
    server.login(cfg["smtp_user"], cfg["password"])
    return server


def _send_html(to_email: str, subject: str, html_body: str) -> Dict[str, Any]:
    cfg = _smtp_config()
    if not cfg["sender"] or not cfg["password"]:
        return {"ok": False, "error": "EMAIL_SENDER and EMAIL_PASSWORD must be set"}
    if not cfg["smtp_user"]:
        return {"ok": False, "error": "EMAIL_SMTP_USER or EMAIL_SENDER must be set"}

    msg = MIMEMultipart()
    msg["From"] = cfg["sender"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        with _smtp_connect(cfg) as server:
            server.send_message(msg)
        return {"ok": True, "error": None}
    except Exception as e:
        err = str(e)
        if "only send testing emails to your own email" in err.lower():
            err = resend_test_mode_message()
        return {"ok": False, "error": err}


def send_html_to_recipients(
    recipients: List[str], subject: str, html_body: str
) -> Dict[str, Any]:
    """Send the same HTML email to each recipient; reports per-address results."""
    if not recipients:
        return {"ok": False, "error": "No recipient emails", "sent_to": [], "failed_to": {}}

    blocked = resend_blocked_recipients(recipients)
    pre_failed: Dict[str, str] = {}
    if blocked:
        msg = resend_test_mode_message()
        pre_failed = {addr: msg for addr in blocked}
        allowed = [r for r in recipients if r not in blocked]
        if not allowed:
            return {"ok": False, "error": msg, "sent_to": [], "failed_to": pre_failed}
        recipients = allowed

    sent_to: List[str] = []
    failed_to: Dict[str, str] = dict(pre_failed)

    for addr in recipients:
        result = _send_html(addr, subject, html_body)
        if result.get("ok"):
            sent_to.append(addr)
        else:
            failed_to[addr] = result.get("error", "Send failed")

    if sent_to and not failed_to:
        return {"ok": True, "error": None, "sent_to": sent_to, "failed_to": failed_to}
    if sent_to and failed_to:
        parts = [f"{e}: {msg}" for e, msg in failed_to.items()]
        return {
            "ok": True,
            "error": "Partial: " + "; ".join(parts),
            "sent_to": sent_to,
            "failed_to": failed_to,
            "partial": True,
        }
    first_err = next(iter(failed_to.values()), "All sends failed")
    return {"ok": False, "error": first_err, "sent_to": sent_to, "failed_to": failed_to}


def send_urgent_alert(
    to_email,
    company: str,
    headline: str,
    url: str,
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """Send HTML urgent regulatory alert email."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    why = analysis.get("why_matters", "")
    talking = analysis.get("talking_points", "")

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; margin: 20px; color: #1a1a1a;">
      <div style="background: #7f1d1d; color: white; padding: 16px; border-radius: 8px;">
        <h2 style="margin:0;">URGENT REGULATORY ALERT</h2>
      </div>
      <div style="margin-top: 20px;">
        <p><strong>Company:</strong> {company}</p>
        <p><strong>Headline:</strong> {headline}</p>
        <p><a href="{url}">Read article</a></p>
        <h3>Why this matters</h3>
        <p>{why}</p>
        <h3>Suggested talking points</h3>
        <p style="background:#f8f9fa; padding:12px; border-left:4px solid #dc2626;">{talking}</p>
        <p style="color:#6b7280; font-size:12px;">Alert generated: {now}</p>
      </div>
    </body>
    </html>
    """

    subject = f"URGENT: {company} Regulatory Alert"
    if isinstance(to_email, list):
        return send_html_to_recipients(to_email, subject, html)
    return _send_html(to_email, subject, html)


def send_company_digest(
    recipients: List[str],
    company: str,
    summary: Dict[str, str],
    articles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Send ONE consolidated regulatory digest email for a company, listing all of its
    relevant news items together with an AI overview and prioritized sales actions.
    """
    if not articles:
        return {"ok": False, "error": "No articles to send", "sent_to": [], "failed_to": {}}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    any_urgent = any(a.get("urgency") == "urgent" for a in articles)
    overview = _esc(summary.get("summary", "")) if summary else ""
    actions = _esc(summary.get("priority_actions", "")) if summary else ""

    banner_color = "#7f1d1d" if any_urgent else "#22223b"
    banner_label = "URGENT REGULATORY ALERT" if any_urgent else "REGULATORY NEWS DIGEST"

    items_html = ""
    for a in articles:
        is_urgent = a.get("urgency") == "urgent"
        badge_bg = "#dc2626" if is_urgent else "#4a4e69"
        badge_text = "URGENT" if is_urgent else "Relevant"
        url = a.get("url", "")
        link = (
            f'<p style="margin:6px 0;"><a href="{_esc(url)}" style="color:#6060a3;">Read article</a></p>'
            if url else ""
        )
        items_html += f"""
        <div style="border:1px solid #e5e7eb; border-radius:8px; padding:14px; margin-top:12px;">
          <span style="background:{badge_bg}; color:#fff; font-size:11px; font-weight:700;
                       padding:3px 8px; border-radius:999px;">{badge_text}</span>
          <p style="margin:8px 0 4px; font-weight:700;">{_esc(a.get('headline', ''))}</p>
          {link}
          <p style="margin:6px 0;"><strong>Why it matters:</strong> {_esc(a.get('why_matters', ''))}</p>
          <p style="margin:6px 0; background:#f8f9fa; padding:10px; border-left:4px solid #c9ada7;">
            <strong>Talking points:</strong> {_esc(a.get('talking_points', ''))}</p>
        </div>
        """

    overview_html = (
        f'<h3 style="margin:22px 0 6px;">Overview</h3><p>{overview}</p>' if overview else ""
    )
    actions_html = (
        f'<h3 style="margin:22px 0 6px;">Priority actions</h3>'
        f'<p style="background:#f2e9e4; padding:12px; border-radius:8px;">{actions}</p>'
        if actions else ""
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; margin: 20px; color: #1a1a1a;">
      <div style="background: {banner_color}; color: white; padding: 16px; border-radius: 8px;">
        <h2 style="margin:0;">{banner_label}</h2>
        <p style="margin:6px 0 0; opacity:.85;">{_esc(company)} — {len(articles)} update(s)</p>
      </div>
      {overview_html}
      {actions_html}
      <h3 style="margin:22px 0 6px;">News items</h3>
      {items_html}
      <p style="color:#6b7280; font-size:12px; margin-top:18px;">Digest generated: {now}</p>
    </body>
    </html>
    """

    prefix = "URGENT: " if any_urgent else "Regulatory Alert: "
    subject = f"{prefix}{company} — {len(articles)} update(s)"
    return send_html_to_recipients(recipients, subject, html)


def send_test_email(recipients: List[str]) -> Dict[str, Any]:
    """Send a minimal test email to verify SMTP configuration."""
    html = """
    <html><body>
      <h2>Test Email — Regulatory Alerts</h2>
      <p>Your SMTP configuration is working. You can run regulatory news searches.</p>
    </body></html>
    """
    return send_html_to_recipients(recipients, "Test: Regulatory News Alerts", html)
