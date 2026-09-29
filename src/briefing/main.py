"""Command-line entry point and newsletter pipeline orchestration."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .collect import add_article_images, add_indonesia_article_images, collect_candidates
from .config import DEFAULT_OUT_DIR, DEFAULT_SOURCES_PATH, Settings
from .delivery import parse_clock, scheduled_send_time
from .editor import create_edition, create_indonesia_edition
from .models import Candidate, NewsletterEdition, edition_from_dict
from .render import render_html, render_indonesia_text, render_text
from .send import SendResult, send_email

COUNTRIES = ("Sweden", "Indonesia")
FRESH_STORY_TARGET = 3
EDITORIAL_POOL_TARGET = 12
# Last resort when even the direct-publisher backup feeds leave a country thin. Capped at
# 7 days so a slow news day can't put month-old stories into "today's" brief.
FALLBACK_LOOKBACK_HOURS = (168,)


@contextmanager
def stage(label: str) -> Iterator[None]:
    """Log a pipeline stage with elapsed time and preserve the original exception."""
    started = time.monotonic()
    print(f"→ {label}...", flush=True)
    try:
        yield
    except Exception:
        print(f"✗ {label} failed after {time.monotonic() - started:.1f}s", flush=True)
        raise
    print(f"✓ {label} ({time.monotonic() - started:.1f}s)", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Daily Brief")
    parser.add_argument("--send", action="store_true", help="Send through Resend after generation")
    parser.add_argument(
        "--edition",
        choices=("standard", "indonesia"),
        default="standard",
        help="Newsletter edition to generate",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Render the bundled standard sample without network or API keys",
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def load_sample():
    sample_path = Path(__file__).with_name("data") / "sample_edition.json"
    return edition_from_dict(json.loads(sample_path.read_text(encoding="utf-8")))


def write_outputs(
    edition: NewsletterEdition,
    out_dir: Path,
    edition_name: str = "standard",
) -> tuple[Path, str, str]:
    html = render_html(edition)
    text = render_indonesia_text(edition) if edition_name == "indonesia" else render_text(edition)

    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = "indonesia-" if edition_name == "indonesia" else ""
    html_path = out_dir / f"{prefix}{edition.edition_date}.html"
    text_path = out_dir / f"{prefix}{edition.edition_date}.txt"
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    return html_path, html, text


def country_counts(candidates: list[Candidate]) -> dict[str, int]:
    return {
        country: sum(candidate.country == country for candidate in candidates)
        for country in COUNTRIES
    }


def _relevant_counts(candidates: list[Candidate], edition_name: str) -> list[int]:
    counts = country_counts(candidates)
    if edition_name == "indonesia":
        return [counts["Indonesia"]]
    return list(counts.values())


def candidate_pool_is_thin(candidates: list[Candidate], edition_name: str) -> bool:
    """Keep enough choices for topic diversity even when only a few are ultimately published."""
    return min(_relevant_counts(candidates, edition_name)) < EDITORIAL_POOL_TARGET


def backfill_candidates(
    sources: Path,
    candidates: list[Candidate],
    settings: Settings,
    edition_name: str,
) -> list[Candidate]:
    fresh_counts = country_counts(candidates)
    if min(_relevant_counts(candidates, edition_name)) < FRESH_STORY_TARGET:
        print(
            "  Fresh-story target below 3, activating older-story fallback: "
            f"{fresh_counts}",
            flush=True,
        )

    if not candidate_pool_is_thin(candidates, edition_name):
        return candidates

    for hours in FALLBACK_LOOKBACK_HOURS:
        print(f"  Expanding candidate lookback to {hours // 24} days", flush=True)
        candidates = asyncio.run(
            collect_candidates(sources, hours, max(settings.max_candidates, 60))
        )
        if not candidate_pool_is_thin(candidates, edition_name):
            break
    return candidates


def edition_day(settings: Settings, now: datetime | None = None) -> date:
    """The day this edition is for: fixed by the workflow gate, else today locally."""
    if settings.edition_date:
        return date.fromisoformat(settings.edition_date)
    return (now or datetime.now(ZoneInfo(settings.timezone))).date()


def delivery_time(settings: Settings, day: date, now: datetime | None = None) -> datetime | None:
    """Inbox time for automatic runs; None means send immediately.

    Manual revisions (which carry a delivery nonce) always go out immediately.
    """
    if settings.delivery_nonce or not settings.send_at:
        return None
    now = now or datetime.now(ZoneInfo(settings.timezone))
    if now.date() != day:
        return None
    return scheduled_send_time(now, parse_clock(settings.send_at))


def write_delivery_record(
    out_dir: Path,
    edition_name: str,
    day: date,
    result: SendResult,
) -> Path:
    record_dir = out_dir / "delivery"
    record_dir.mkdir(parents=True, exist_ok=True)
    path = record_dir / f"{edition_name}.json"
    path.write_text(
        json.dumps(
            {
                "edition": edition_name,
                "date": day.isoformat(),
                "message_id": result.message_id,
                "duplicate": result.duplicate,
                "scheduled_at": result.scheduled_at,
                "recorded_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _generate_live_edition(
    args: argparse.Namespace,
    settings: Settings,
    day: date,
) -> tuple[NewsletterEdition, list[Candidate]]:
    with stage("Fetching news feeds"):
        candidates = asyncio.run(
            collect_candidates(args.sources, settings.lookback_hours, settings.max_candidates)
        )

    fresh_counts = country_counts(candidates)
    print(f"  Found {len(candidates)} usable fresh stories: {fresh_counts}", flush=True)
    candidates = backfill_candidates(args.sources, candidates, settings, args.edition)
    print(f"  Publishing candidate pool: {country_counts(candidates)}", flush=True)

    with stage("Writing the edition with OpenAI"):
        if args.edition == "indonesia":
            edition = create_indonesia_edition(
                candidates,
                settings.openai_api_key,
                settings.openai_model,
                day,
            )
        else:
            edition = create_edition(
                candidates,
                settings.openai_api_key,
                settings.openai_model,
                day,
            )
    return edition, candidates


def _add_images(
    edition: NewsletterEdition,
    candidates: list[Candidate],
    edition_name: str,
) -> NewsletterEdition:
    with stage("Fetching publisher images"):
        if edition_name == "indonesia":
            return asyncio.run(
                add_indonesia_article_images(
                    edition,
                    candidates,
                    limit=6,
                    image_budget_seconds=45.0,
                )
            )
        return asyncio.run(
            add_article_images(
                edition,
                candidates,
                limit=10,
                image_budget_seconds=45.0,
            )
        )


def main() -> None:
    args = parse_args()
    settings: Settings | None = None

    if args.sample:
        if args.edition == "indonesia":
            raise SystemExit("The bundled sample is the standard two-country edition")
        edition: NewsletterEdition = load_sample()
    else:
        settings = Settings.from_env(require_delivery=args.send)
        day = edition_day(settings)
        print(f"  Edition date: {day.isoformat()}", flush=True)
        edition, candidates = _generate_live_edition(args, settings, day)
        edition = _add_images(edition, candidates, args.edition)

    with stage("Rendering the email"):
        html_path, html, text = write_outputs(edition, args.out, args.edition)
    print(f"  Saved {html_path}", flush=True)

    if args.send:
        assert settings is not None
        send_at = delivery_time(settings, day)
        label = f"Scheduling for {send_at:%H:%M %Z}" if send_at else "Sending"
        with stage(f"{label} through Resend"):
            result = send_email(
                api_key=settings.resend_api_key,
                from_email=settings.from_email,
                to_email=settings.to_email,
                subject=edition.subject,
                html=html,
                text=text,
                edition_date=edition.edition_date,
                edition_name=args.edition,
                delivery_nonce=settings.delivery_nonce,
                scheduled_at=send_at,
            )
        if result.duplicate:
            print(
                "  Resend already has today's edition under this key (an earlier run sent "
                "it); treating as delivered.",
                flush=True,
            )
        elif result.scheduled_at:
            print(f"  Resend message {result.message_id} will arrive at {send_at:%H:%M %Z}")
        else:
            print(f"  Sent message {result.message_id}", flush=True)
        record = write_delivery_record(args.out, args.edition, day, result)
        print(f"  Delivery record: {record}", flush=True)


if __name__ == "__main__":
    main()
