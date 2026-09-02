from __future__ import annotations

import hashlib

import httpx


def parse_recipients(value: str) -> list[str]:
    recipients = [address.strip() for address in value.split(",") if address.strip()]
    if not recipients:
        raise ValueError("At least one recipient email is required")
    return recipients


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
    recipients = parse_recipients(to_email)
    digest_input = f"{edition_date}:{','.join(recipients)}:{subject}:{html}"
    digest = hashlib.sha256(digest_input.encode()).hexdigest()[:24]
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"daily-brief-{digest}",
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
        timeout=30.0,
    )
    if response.is_error:
        raise httpx.HTTPStatusError(
            f"Resend rejected the email ({response.status_code}): {response.text}",
            request=response.request,
            response=response,
        )
    return response.json()["id"]
