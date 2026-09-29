"""Delivery timing and bookkeeping shared by the GitHub workflows.

This module is deliberately stdlib-only: the workflow gate runs it with the runner's
system Python before any package dependencies are installed.

How a morning is supposed to go (all times America/New_York, Monday-Saturday):

* 05:45  An external exact-time scheduler dispatches ``briefs.yml``. Each edition is
         generated and handed to Resend with ``scheduled_at`` = 06:00, so it lands in
         the inbox at 06:00 on the dot.
* 05:55  If a backstop run (GitHub cron, which is often hours late) happens to be
         waiting, it checks whether each edition was delivered and sends any that
         were not.
* Later  Any backstop run that lands after 05:55 and before 15:00 sends whatever is
         still missing, immediately. After 15:00 a morning brief is too stale to send
         automatically.

A successful automatic send uploads a tiny workflow artifact named
``delivered-<edition>-<YYYY-MM-DD>``. That artifact is the delivery record every later
run checks, whichever workflow or trigger produced it. Resend's idempotency key is the
second, independent guard against duplicates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

TIMEZONE = "America/New_York"
EDITIONS = ("standard", "indonesia")

SEND_AT = clock_time(6, 0)
BACKSTOP_OPENS = clock_time(5, 0)
BACKSTOP_CHECKS_AT = clock_time(5, 55)
LATEST_AUTOMATIC_SEND = clock_time(15, 0)
SUNDAY = 6

GITHUB_API_URL = "https://api.github.com"
RESEND_EMAILS_URL = "https://api.resend.com/emails"
API_ATTEMPTS = 3


def local_now(timezone_name: str = TIMEZONE) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))


def is_delivery_day(day: date) -> bool:
    return day.weekday() != SUNDAY


def record_name(edition: str, day: date) -> str:
    return f"delivered-{edition}-{day.isoformat()}"


def alert_record_name(edition: str, day: date) -> str:
    return f"alerted-{edition}-{day.isoformat()}"


def _at(day: date, when: clock_time, tzinfo) -> datetime:
    return datetime.combine(day, when, tzinfo=tzinfo)


@dataclass(frozen=True)
class BackstopPlan:
    proceed: bool
    wait_seconds: float
    reason: str


def plan_backstop(now: datetime) -> BackstopPlan:
    """Decide what a (possibly very late) GitHub cron backstop run should do."""
    if not is_delivery_day(now.date()):
        return BackstopPlan(False, 0.0, "No edition on Sundays.")

    local_clock = now.time()
    if local_clock < BACKSTOP_OPENS:
        return BackstopPlan(
            False,
            0.0,
            f"{now:%H:%M} is before the {BACKSTOP_OPENS:%H:%M} backstop window; "
            "a later run will check.",
        )
    if local_clock >= LATEST_AUTOMATIC_SEND:
        return BackstopPlan(
            False,
            0.0,
            f"{now:%H:%M} is past the {LATEST_AUTOMATIC_SEND:%H:%M} cutoff for an automatic "
            "morning send.",
        )

    check_at = _at(now.date(), BACKSTOP_CHECKS_AT, now.tzinfo)
    wait_seconds = max(0.0, check_at.timestamp() - now.timestamp())
    return BackstopPlan(True, wait_seconds, "Inside the backstop window.")


def scheduled_send_time(
    now: datetime,
    send_at: clock_time | None = SEND_AT,
    *,
    min_lead: timedelta = timedelta(minutes=1),
) -> datetime | None:
    """Return today's inbox time if it is still ahead of ``now``; otherwise send now."""
    if send_at is None:
        return None
    target = _at(now.date(), send_at, now.tzinfo)
    if target.timestamp() - now.timestamp() >= min_lead.total_seconds():
        return target
    return None


def parse_clock(value: str) -> clock_time | None:
    value = value.strip()
    if not value:
        return None
    hours, _, minutes = value.partition(":")
    return clock_time(int(hours), int(minutes or 0))


def requested_editions(value: str) -> list[str]:
    value = (value or "both").strip().lower()
    if value in ("both", "all", ""):
        return list(EDITIONS)
    if value not in EDITIONS:
        raise ValueError(f"Unknown edition {value!r}; expected both, standard, or indonesia")
    return [value]


# ---------------------------------------------------------------------------
# GitHub artifact records
# ---------------------------------------------------------------------------


def _request_json(request: urllib.request.Request, *, timeout: float = 20.0) -> dict:
    last_error: Exception | None = None
    for attempt in range(API_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
        if attempt + 1 < API_ATTEMPTS:
            time.sleep(2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def artifact_exists(
    name: str,
    *,
    repo: str,
    token: str,
    api_url: str = GITHUB_API_URL,
) -> bool | None:
    """Return whether an unexpired artifact exists, or None if GitHub can't be asked."""
    query = urllib.parse.urlencode({"name": name, "per_page": 10})
    request = urllib.request.Request(
        f"{api_url}/repos/{repo}/actions/artifacts?{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DailyBrief/1.0",
        },
    )
    try:
        payload = _request_json(request)
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
        print(f"  WARNING: could not look up artifact {name}: {exc}", flush=True)
        return None
    return any(
        item.get("name") == name and not item.get("expired")
        for item in payload.get("artifacts", [])
    )


# ---------------------------------------------------------------------------
# Gate command
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    day: date
    send: dict[str, bool]
    notes: list[str]


def run_gate(
    *,
    editions: list[str],
    now: datetime,
    backstop: bool = False,
    force: bool = False,
    is_delivered=None,
    sleep=time.sleep,
    clock=None,
) -> GateResult:
    """Decide which editions this run should send.

    ``is_delivered(edition, day)`` returns True/False, or None when unknown. Unknown is
    treated as "not delivered": a duplicate attempt is stopped by Resend's idempotency
    key, whereas a skipped send would be a missed newsletter.
    """
    notes: list[str] = []
    none = {edition: False for edition in EDITIONS}

    if force:
        notes.append("Forced send: skipping the day and duplicate checks.")
        return GateResult(now.date(), {e: e in editions for e in EDITIONS}, notes)

    if backstop:
        plan = plan_backstop(now)
        notes.append(plan.reason)
        if not plan.proceed:
            return GateResult(now.date(), none, notes)
        if plan.wait_seconds > 0:
            notes.append(
                f"Waiting {plan.wait_seconds / 60:.0f} min until {BACKSTOP_CHECKS_AT:%H:%M} "
                "so the primary 05:45 trigger can finish first."
            )
            print(f"  {notes[-1]}", flush=True)
            sleep(plan.wait_seconds)
            now = clock() if clock else local_now(str(now.tzinfo))
    elif not is_delivery_day(now.date()):
        notes.append("No automatic edition on Sundays. Use force to send anyway.")
        return GateResult(now.date(), none, notes)

    day = now.date()
    send = dict(none)
    for edition in editions:
        delivered = is_delivered(edition, day) if is_delivered else None
        if delivered:
            notes.append(f"{edition}: already delivered for {day}.")
        else:
            send[edition] = True
            status = "not delivered yet" if delivered is False else "delivery status unknown"
            notes.append(f"{edition}: {status} for {day}; sending.")
    return GateResult(day, send, notes)


def _write_github_output(values: dict[str, str]) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.writelines(f"{key}={value}\n" for key, value in values.items())


def _write_summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _github_lookup():
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("GITHUB_TOKEN", "") or os.getenv("GH_TOKEN", "")
    if not repo or not token:
        print("  WARNING: no GitHub token; delivery records can't be checked.", flush=True)
        return None

    def is_delivered(edition: str, day: date) -> bool | None:
        return artifact_exists(record_name(edition, day), repo=repo, token=token)

    return is_delivered


def gate_command(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.timezone)
    now = datetime.fromisoformat(args.now).astimezone(tz) if args.now else local_now(args.timezone)
    result = run_gate(
        editions=requested_editions(args.editions),
        now=now,
        backstop=args.backstop,
        force=args.force,
        is_delivered=_github_lookup(),
        clock=lambda: local_now(args.timezone),
    )
    outputs = {"date": result.day.isoformat()}
    outputs.update({edition: str(flag).lower() for edition, flag in result.send.items()})
    outputs["any"] = str(any(result.send.values())).lower()
    _write_github_output(outputs)

    for note in result.notes:
        print(f"  {note}", flush=True)
    _write_summary([f"### Delivery gate ({result.day})", *[f"- {note}" for note in result.notes]])
    return 0


# ---------------------------------------------------------------------------
# Failure alert command
# ---------------------------------------------------------------------------


def build_alert(*, edition: str, day: date, run_url: str, trigger: str) -> dict[str, str]:
    label = "Nusantara Daily (Indonesia)" if edition == "indonesia" else "Daily Brief (standard)"
    subject = f"⚠️ {label} did not send for {day:%a %b} {day.day}"
    text = (
        f"The {label} run for {day.isoformat()} failed (trigger: {trigger}).\n\n"
        f"Run log: {run_url}\n\n"
        "The backstop keeps retrying hourly until 3 PM ET. If no brief arrives, open the "
        "run log above, fix the cause, then run 'Morning briefs' from the Actions tab.\n\n"
        "You get at most one of these alerts per edition per day."
    )
    return {"subject": subject, "text": text}


def post_resend(payload: dict, api_key: str) -> None:
    request = urllib.request.Request(
        RESEND_EMAILS_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "DailyBrief/1.0",
        },
    )
    _request_json(request, timeout=30.0)


def alert_command(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.date) if args.date else local_now(args.timezone).date()
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("GITHUB_TOKEN", "") or os.getenv("GH_TOKEN", "")
    if repo and token:
        already = artifact_exists(alert_record_name(args.edition, day), repo=repo, token=token)
        if already:
            print("  Already alerted for this edition today; not sending another.", flush=True)
            _write_github_output({"sent": "false"})
            return 0

    api_key = os.getenv("RESEND_API_KEY", "")
    to_email = os.getenv("ALERT_TO_EMAIL", "")
    from_email = os.getenv("BRIEF_FROM_EMAIL", "")
    if not (api_key and to_email and from_email):
        print("  Alert not sent: RESEND_API_KEY, ALERT_TO_EMAIL, or BRIEF_FROM_EMAIL missing.")
        _write_github_output({"sent": "false"})
        return 0

    run_url = args.run_url or (
        f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/"
        f"{os.getenv('GITHUB_RUN_ID', '')}"
    )
    message = build_alert(edition=args.edition, day=day, run_url=run_url, trigger=args.trigger)
    recipients = [address.strip() for address in to_email.split(",") if address.strip()]
    try:
        post_resend({"from": from_email, "to": recipients, **message}, api_key)
    except Exception as exc:  # noqa: BLE001 - alerting must never mask the real failure
        print(f"  Alert email failed: {exc}", flush=True)
        _write_github_output({"sent": "false"})
        return 0

    Path(args.record_dir).mkdir(parents=True, exist_ok=True)
    (Path(args.record_dir) / "alert.json").write_text(
        json.dumps({"edition": args.edition, "date": day.isoformat(), "run": run_url}),
        encoding="utf-8",
    )
    print(f"  Alert sent to {', '.join(recipients)}", flush=True)
    _write_github_output({"sent": "true", "record": alert_record_name(args.edition, day)})
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily Brief delivery gate and alerts")
    parser.add_argument("--timezone", default=os.getenv("BRIEF_TIMEZONE", TIMEZONE))
    sub = parser.add_subparsers(dest="command", required=True)

    gate = sub.add_parser("gate", help="Decide which editions this run should send")
    gate.add_argument("--editions", default="both")
    gate.add_argument("--backstop", action="store_true")
    gate.add_argument("--force", action="store_true")
    gate.add_argument("--now", default="", help="ISO timestamp override (testing)")
    gate.set_defaults(handler=gate_command)

    alert = sub.add_parser("alert", help="Email a one-per-day failure alert")
    alert.add_argument("--edition", required=True, choices=EDITIONS)
    alert.add_argument("--date", default="")
    alert.add_argument("--trigger", default="unknown")
    alert.add_argument("--run-url", default="")
    alert.add_argument("--record-dir", default="out/alert")
    alert.set_defaults(handler=alert_command)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
