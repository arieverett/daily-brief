import pytest

from briefing.send import parse_recipients


def test_parse_recipients_accepts_one_or_multiple_addresses():
    assert parse_recipients("ari@example.com") == ["ari@example.com"]
    assert parse_recipients("mom@example.com, ari@example.com") == [
        "mom@example.com",
        "ari@example.com",
    ]


def test_parse_recipients_rejects_an_empty_value():
    with pytest.raises(ValueError, match="At least one recipient"):
        parse_recipients(" , ")
