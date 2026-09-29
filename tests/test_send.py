from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from briefing.send import _idempotency_key, parse_recipients, send_email


def test_parse_recipients_accepts_one_or_multiple_addresses():
    assert parse_recipients("ari@example.com") == ["ari@example.com"]
    assert parse_recipients("mom@example.com, ari@example.com") == [
        "mom@example.com",
        "ari@example.com",
    ]


def test_parse_recipients_deduplicates_case_insensitively():
    assert parse_recipients("ari@example.com, ARI@example.com, mom@example.com") == [
        "ari@example.com",
        "mom@example.com",
    ]


def test_parse_recipients_rejects_an_empty_value():
    with pytest.raises(ValueError, match="At least one recipient"):
        parse_recipients(" , ")


def test_idempotency_key_is_stable_for_automatic_retries():
    arguments = {
        "edition_date": "2026-09-09",
        "edition_name": "standard",
        "recipients": ["ari@example.com"],
    }
    first = _idempotency_key(**arguments)
    assert first == _idempotency_key(**arguments)
    assert first.startswith("daily-brief-")
    assert first == _idempotency_key(
        **{**arguments, "recipients": ["ARI@example.com"]}
    )
    assert first != _idempotency_key(
        **{**arguments, "delivery_nonce": "manual-run-123"}
    )


# --- Delivery behavior ---------------------------------------------------------

REQUEST = httpx.Request("POST", "https://api.resend.com/emails")


def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=REQUEST)


def _send(post, **overrides):
    arguments = {
        "api_key": "re_test",
        "from_email": "Brief <brief@example.com>",
        "to_email": "ari@example.com",
        "subject": "Hello",
        "html": "<p>Hi</p>",
        "text": "Hi",
        "edition_date": "2026-09-29",
        "edition_name": "standard",
        "sleep": lambda seconds: None,
        "post": post,
    }
    arguments.update(overrides)
    return send_email(**arguments)


def test_send_retries_transient_failures_with_the_same_key():
    calls = []

    def post(url, headers, json, timeout):
        calls.append(headers["Idempotency-Key"])
        if len(calls) == 1:
            raise httpx.ConnectError("reset", request=REQUEST)
        if len(calls) == 2:
            return _response(503, {"name": "internal_server_error"})
        return _response(200, {"id": "msg_123"})

    result = _send(post)
    assert result.message_id == "msg_123" and not result.duplicate
    assert len(calls) == 3 and len(set(calls)) == 1


def test_send_treats_idempotency_conflict_as_already_delivered():
    def post(url, headers, json, timeout):
        return _response(409, {"name": "invalid_idempotent_request", "message": "..."})

    result = _send(post)
    assert result.duplicate is True


def test_send_waits_out_concurrent_idempotent_requests():
    responses = [
        _response(409, {"name": "concurrent_idempotent_requests"}),
        _response(409, {"name": "invalid_idempotent_request"}),
    ]

    result = _send(lambda url, headers, json, timeout: responses.pop(0))
    assert result.duplicate is True


def test_send_does_not_retry_permanent_rejections():
    calls = []

    def post(url, headers, json, timeout):
        calls.append(1)
        return _response(422, {"name": "validation_error", "message": "bad from"})

    with pytest.raises(httpx.HTTPStatusError, match="422"):
        _send(post)
    assert len(calls) == 1


def test_send_gives_up_after_repeated_outages():
    with pytest.raises(RuntimeError, match="after 4 attempts"):
        _send(lambda url, headers, json, timeout: _response(500, {"name": "boom"}))


def test_send_schedules_delivery_in_utc():
    captured = {}

    def post(url, headers, json, timeout):
        captured.update(json)
        return _response(200, {"id": "msg_1"})

    six_am = datetime(2026, 9, 30, 6, 0, tzinfo=ZoneInfo("America/New_York"))
    result = _send(post, scheduled_at=six_am)
    assert captured["scheduled_at"] == "2026-09-30T10:00:00.000Z"
    assert result.scheduled_at == "2026-09-30T10:00:00.000Z"


def test_send_without_schedule_omits_scheduled_at():
    captured = {}

    def post(url, headers, json, timeout):
        captured.update(json)
        return _response(200, {"id": "msg_1"})

    _send(post)
    assert "scheduled_at" not in captured
