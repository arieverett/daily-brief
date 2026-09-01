from __future__ import annotations

import hashlib

import httpx


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
    digest_input = f"{edition_date}:{to_email}:{subject}:{html}"
    digest = hashlib.sha256(digest_input.encode()).hexdigest()[:24]
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"daily-brief-{digest}",
        },
        json={
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "text": text,
            "tags": [{"name": "brief", "value": "daily"}],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]
