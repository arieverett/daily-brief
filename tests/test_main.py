from datetime import date, datetime
from zoneinfo import ZoneInfo

from briefing.config import Settings
from briefing.main import delivery_time, edition_day, write_delivery_record
from briefing.send import SendResult

NY = ZoneInfo("America/New_York")


def settings(**overrides) -> Settings:
    values = {"openai_api_key": "k", "resend_api_key": "r", "to_email": "a@example.com",
              "from_email": "b@example.com"}
    values.update(overrides)
    return Settings(**values)


def test_edition_day_prefers_the_gate_date():
    assert edition_day(settings(edition_date="2026-09-29")) == date(2026, 9, 29)
    late_night = datetime(2026, 9, 29, 23, 30, tzinfo=NY)
    assert edition_day(settings(), late_night) == date(2026, 9, 29)


def test_automatic_runs_are_scheduled_for_six_am():
    early = datetime(2026, 9, 29, 5, 48, tzinfo=NY)
    target = delivery_time(settings(send_at="06:00"), date(2026, 9, 29), early)
    assert target == datetime(2026, 9, 29, 6, 0, tzinfo=NY)


def test_late_runs_and_manual_revisions_send_immediately():
    late = datetime(2026, 9, 29, 6, 20, tzinfo=NY)
    assert delivery_time(settings(send_at="06:00"), date(2026, 9, 29), late) is None

    early = datetime(2026, 9, 29, 5, 48, tzinfo=NY)
    manual = settings(send_at="06:00", delivery_nonce="run-42")
    assert delivery_time(manual, date(2026, 9, 29), early) is None
    assert delivery_time(settings(), date(2026, 9, 29), early) is None


def test_delivery_record_is_written(tmp_path):
    path = write_delivery_record(tmp_path, "indonesia", date(2026, 9, 29),
                                 SendResult(message_id="msg_1"))
    assert path.name == "indonesia.json"
    assert '"message_id": "msg_1"' in path.read_text()
