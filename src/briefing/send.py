"""Resend delivery with recipient normalization and duplicate-send protection."""

from __future__ import annotations

import hashlib

import httpx

RESEND_EMAILS_URL = "https://api.resend.com/emails"
DELIVERY_TIMEOUT_SECONDS = 30.0


def parse_recipients(value: str) -> list[str]:
    """Parse comma-separated recipients, preserving order while removing duplicates."""
    recipients: list[str] = []
    seen: set[str] = set()
    for raw_address in value.split(","):
        address = raw_address.strip()
        key = address.casefold()
        if not address or key in seen:
            continue
        recipients.append(address)
        seen.add(key)

    if not recipients:
        raise ValueError("At least one recipient email is required")
    return recipients


def _idempotency_key(
    *,
    edition_date: str,
    recipients: list[str],
    subject: str,
    html: str,
) -> str:
    digest_input = f"{edition_date}:{','.join(recipients)}:{subject}:{html}"
    digest = hashlib.sha256(digest_input.encode()).hexdigest()[:24]
    return f"daily-brief-{digest}"


def send_email(
    *,
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
    edition_date: str,
) -> str:
    """Send one rendered edition and return Resend's message id."""
    recipients = parse_recipients(to_email)
    response = httpx.post(
        RESEND_EMAILS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": _idempotency_key(
                edition_date=edition_date,
                recipients=recipients,
                subject=subject,
                html=html,
            ),
            "User-Agent": "DailyBrief/1.0",
        },
        json={
            "from": from_email,
            "to": recipients,
            "subject": subject,
            "html": html,
            "text": text,
            "tags": [{"name": "brief", "value": "daily"}],
        },
        timeout=DELIVERY_TIMEOUT_SECONDS,
    )
    if response.is_error:
        raise httpx.HTTPStatusError(
            f"Resend rejected the email ({response.status_code}): {response.text}",
            request=response.request,
            response=response,
        )

    message_id = response.json().get("id")
    if not message_id:
        raise ValueError("Resend accepted the request but returned no message id")
    return str(message_id)
