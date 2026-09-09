import pytest

from briefing.send import _idempotency_key, parse_recipients


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


def test_idempotency_key_is_stable_and_content_sensitive():
    arguments = {
        "edition_date": "2026-09-09",
        "recipients": ["ari@example.com"],
        "subject": "Daily Brief",
        "html": "<p>Hello</p>",
    }
    first = _idempotency_key(**arguments)
    assert first == _idempotency_key(**arguments)
    assert first.startswith("daily-brief-")
    assert first != _idempotency_key(**{**arguments, "html": "<p>Changed</p>"})
