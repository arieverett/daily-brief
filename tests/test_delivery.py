from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from briefing import delivery
from briefing.delivery import (
    build_alert,
    plan_backstop,
    record_name,
    requested_editions,
    run_gate,
    scheduled_send_time,
)

NY = ZoneInfo("America/New_York")


def ny(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=NY)


# 2026-09-29 is a Tuesday; 2026-10-04 is a Sunday.


@pytest.mark.parametrize(
    ("now", "proceed", "wait_minutes"),
    [
        ("2026-09-29T02:10", False, 0),  # too early: a later backstop run handles it
        ("2026-09-29T04:59", False, 0),
        ("2026-09-29T05:00", True, 55),  # waits for the 05:45 primary to finish
        ("2026-09-29T05:40", True, 15),
        ("2026-09-29T05:55", True, 0),
        ("2026-09-29T11:09", True, 0),  # late GitHub cron still sends immediately
        ("2026-09-29T14:59", True, 0),
        ("2026-09-29T15:00", False, 0),  # a "morning" brief is stale by the afternoon
        ("2026-09-29T23:10", False, 0),  # early-evening cron for tomorrow landed today
        ("2026-10-04T05:30", False, 0),  # Sunday
    ],
)
def test_backstop_window(now, proceed, wait_minutes):
    plan = plan_backstop(ny(now))
    assert plan.proceed is proceed
    assert plan.wait_seconds == pytest.approx(wait_minutes * 60)


def test_backstop_wait_uses_wall_clock_in_both_utc_offsets():
    # Monday after the March DST change (UTC-4) and a winter Monday (UTC-5).
    plan = plan_backstop(ny("2026-03-09T05:25"))
    assert plan.proceed
    assert plan.wait_seconds == pytest.approx(30 * 60)
    plan = plan_backstop(ny("2026-12-07T05:25"))
    assert plan.proceed
    assert plan.wait_seconds == pytest.approx(30 * 60)


def test_scheduled_send_time_targets_six_am_only_when_still_ahead():
    target = scheduled_send_time(ny("2026-09-29T05:48"))
    assert target == ny("2026-09-29T06:00")
    assert target.astimezone(ZoneInfo("UTC")).hour == 10  # EDT
    assert scheduled_send_time(ny("2026-12-01T05:48")).astimezone(ZoneInfo("UTC")).hour == 11
    assert scheduled_send_time(ny("2026-09-29T05:59:30")) is None  # too close: send now
    assert scheduled_send_time(ny("2026-09-29T11:00")) is None
    assert scheduled_send_time(ny("2026-09-29T05:48"), None) is None


def test_requested_editions():
    assert requested_editions("both") == ["standard", "indonesia"]
    assert requested_editions("Indonesia") == ["indonesia"]
    with pytest.raises(ValueError):
        requested_editions("sweden")


def test_record_names_are_per_edition_and_day():
    assert record_name("standard", date(2026, 9, 29)) == "delivered-standard-2026-09-29"


def test_gate_sends_only_missing_editions():
    delivered = {("standard", date(2026, 9, 29)): True}
    result = run_gate(
        editions=["standard", "indonesia"],
        now=ny("2026-09-29T05:45"),
        is_delivered=lambda edition, day: delivered.get((edition, day), False),
    )
    assert result.send == {"standard": False, "indonesia": True}
    assert result.day == date(2026, 9, 29)


def test_gate_fails_open_when_records_are_unavailable():
    result = run_gate(
        editions=["standard"],
        now=ny("2026-09-29T05:45"),
        is_delivered=lambda edition, day: None,
    )
    assert result.send == {"standard": True, "indonesia": False}


def test_gate_skips_sunday_unless_forced():
    sunday = ny("2026-10-04T05:45")
    assert not any(run_gate(editions=["standard"], now=sunday).send.values())
    forced = run_gate(editions=["standard"], now=sunday, force=True)
    assert forced.send == {"standard": True, "indonesia": False}


def test_forced_gate_ignores_delivery_records():
    result = run_gate(
        editions=["standard", "indonesia"],
        now=ny("2026-09-29T10:00"),
        force=True,
        is_delivered=lambda edition, day: True,
    )
    assert result.send == {"standard": True, "indonesia": True}


def test_backstop_waits_then_rechecks_after_primary():
    slept: list[float] = []
    after_wait = ny("2026-09-29T05:55")
    # The primary delivered standard while the backstop was waiting.
    delivered = {"standard": True, "indonesia": False}

    result = run_gate(
        editions=["standard", "indonesia"],
        now=ny("2026-09-29T05:20"),
        backstop=True,
        is_delivered=lambda edition, day: delivered[edition],
        sleep=slept.append,
        clock=lambda: after_wait,
    )
    assert slept == [pytest.approx(35 * 60)]
    assert result.send == {"standard": False, "indonesia": True}


def test_backstop_outside_window_does_not_check_or_send():
    def fail(*_):
        raise AssertionError("should not look up records outside the window")

    result = run_gate(
        editions=["standard", "indonesia"],
        now=ny("2026-09-29T01:17"),
        backstop=True,
        is_delivered=fail,
    )
    assert not any(result.send.values())


def test_gate_command_writes_github_outputs(tmp_path, monkeypatch):
    output = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    delivery.main(["gate", "--editions", "indonesia", "--now", "2026-09-29T09:50:00+00:00"])
    lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert lines == {"date": "2026-09-29", "standard": "false", "indonesia": "true", "any": "true"}


def test_artifact_lookup_parses_github_response(monkeypatch):
    def fake_request(request, timeout=20.0):
        assert "name=delivered-standard-2026-09-29" in request.full_url
        return {"artifacts": [{"name": "delivered-standard-2026-09-29", "expired": False}]}

    monkeypatch.setattr(delivery, "_request_json", fake_request)
    assert delivery.artifact_exists(
        "delivered-standard-2026-09-29", repo="o/r", token="t"
    ) is True


def test_artifact_lookup_returns_unknown_on_error(monkeypatch):
    def broken(request, timeout=20.0):
        raise TimeoutError("slow")

    monkeypatch.setattr(delivery, "_request_json", broken)
    assert delivery.artifact_exists("x", repo="o/r", token="t") is None


def test_alert_names_the_edition_and_links_the_run():
    alert = build_alert(
        edition="indonesia",
        day=date(2026, 9, 29),
        run_url="https://github.com/o/r/actions/runs/1",
        trigger="scheduler",
    )
    assert "Nusantara Daily" in alert["subject"]
    assert "Tue Sep 29" in alert["subject"]
    assert "https://github.com/o/r/actions/runs/1" in alert["text"]


def test_alert_is_sent_once_per_edition_per_day(tmp_path, monkeypatch):
    posted: list[dict] = []
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("ALERT_TO_EMAIL", "ari@example.com")
    monkeypatch.setenv("BRIEF_FROM_EMAIL", "Brief <b@example.com>")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    monkeypatch.setattr(delivery, "post_resend", lambda payload, key: posted.append(payload))

    monkeypatch.setattr(delivery, "artifact_exists", lambda *a, **k: False)
    args = ["alert", "--edition", "standard", "--date", "2026-09-29",
            "--record-dir", str(tmp_path / "rec")]
    delivery.main(args)
    assert len(posted) == 1 and posted[0]["to"] == ["ari@example.com"]
    assert (tmp_path / "rec" / "alert.json").exists()

    monkeypatch.setattr(delivery, "artifact_exists", lambda *a, **k: True)
    delivery.main(args)
    assert len(posted) == 1


def test_scheduled_send_lead_time_is_configurable():
    assert scheduled_send_time(ny("2026-09-29T05:58"), min_lead=timedelta(minutes=5)) is None
