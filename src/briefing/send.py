"""Resend delivery with recipient normalization and duplicate-send protection."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

RESEND_EMAILS_URL = "https://api.resend.com/emails"
DELIVERY_TIMEOUT_SECONDS = 30.0
DELIVERY_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (5.0, 15.0, 30.0)


@dataclass(frozen=True)
class SendResult:
    """Outcome of one delivery attempt that did not raise."""

    message_id: str
    duplicate: bool = False
    scheduled_at: str = ""


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
    edition_name: str,
    recipients: list[str],
    delivery_nonce: str = "",
) -> str:
    """Return a retry-stable key for one automatic edition delivery."""
    recipient_key = ",".join(sorted(address.casefold() for address in recipients))
    digest_input = f"{edition_name}:{edition_date}:{recipient_key}:{delivery_nonce}"
    digest = hashlib.sha256(digest_input.encode()).hexdigest()[:24]
    return f"daily-brief-{digest}"


def format_scheduled_at(when: datetime) -> str:
    """Resend accepts ISO 8601; send UTC so there is no offset ambiguity."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _error_name(response: httpx.Response) -> str:
    try:
        return str(response.json().get("name", ""))
    except ValueError:
        return ""


def _is_retryable(response: httpx.Response) -> bool:
    if response.status_code == 409:
        # Another attempt with the same key is still in flight; wait for it to settle.
        return _error_name(response) == "concurrent_idempotent_requests"
    return response.status_code == 429 or response.status_code >= 500


def send_email(
    *,
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
    edition_date: str,
    edition_name: str,
    delivery_nonce: str = "",
    scheduled_at: datetime | None = None,
    sleep=time.sleep,
    post=httpx.post,
) -> SendResult:
    """Send one rendered edition, retrying transient failures with the same key.

    Automatic runs share one idempotency key per edition and day. If an earlier run
    already handed today's edition to Resend, Resend answers 409
    ``invalid_idempotent_request`` (same key, different payload) and this returns a
    ``duplicate`` result instead of failing or sending twice.
    """
    recipients = parse_recipients(to_email)
    payload: dict = {
        "from": from_email,
        "to": recipients,
        "subject": subject,
        "html": html,
        "text": text,
        "tags": [{"name": "brief", "value": "daily"}],
    }
    scheduled = format_scheduled_at(scheduled_at) if scheduled_at else ""
    if scheduled:
        payload["scheduled_at"] = scheduled

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": _idempotency_key(
            edition_date=edition_date,
            edition_name=edition_name,
            recipients=recipients,
            delivery_nonce=delivery_nonce,
        ),
        "User-Agent": "DailyBrief/1.0",
    }

    last_problem = ""
    for attempt in range(DELIVERY_ATTEMPTS):
        try:
            response = post(
                RESEND_EMAILS_URL,
                headers=headers,
                json=payload,
                timeout=DELIVERY_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as exc:
            # Safe to retry: the same key and payload return the original result if the
            # first request actually reached Resend.
            last_problem = f"network error: {exc!r}"
        else:
            if response.status_code == 409 and _error_name(response) == "invalid_idempotent_request":
                return SendResult(message_id="", duplicate=True, scheduled_at=scheduled)
            if not response.is_error:
                message_id = response.json().get("id")
                if not message_id:
                    raise ValueError("Resend accepted the request but returned no message id")
                return SendResult(message_id=str(message_id), scheduled_at=scheduled)
            if not _is_retryable(response):
                raise httpx.HTTPStatusError(
                    f"Resend rejected the email ({response.status_code}): {response.text}",
                    request=response.request,
                    response=response,
                )
            last_problem = f"HTTP {response.status_code}: {response.text[:300]}"

        if attempt + 1 < DELIVERY_ATTEMPTS:
            delay = RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)]
            print(f"  Resend attempt {attempt + 1} failed ({last_problem}); retrying in "
                  f"{delay:.0f}s", flush=True)
            sleep(delay)

    raise RuntimeError(f"Resend delivery failed after {DELIVERY_ATTEMPTS} attempts: {last_problem}")
