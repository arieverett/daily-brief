"""Command-line entry point and newsletter pipeline orchestration."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .collect import add_article_images, add_indonesia_article_images, collect_candidates
from .config import DEFAULT_OUT_DIR, DEFAULT_SOURCES_PATH, Settings
from .editor import create_edition, create_indonesia_edition
from .models import Candidate, NewsletterEdition, edition_from_dict
from .render import render_html, render_indonesia_text, render_text
from .send import send_email

COUNTRIES = ("Sweden", "Indonesia")
FRESH_STORY_TARGET = 3
EDITORIAL_POOL_TARGET = 12
FALLBACK_LOOKBACK_HOURS = (168, 720)  # 7 days, then 30 days.


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


def _generate_live_edition(
    args: argparse.Namespace,
    settings: Settings,
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
                settings.timezone,
            )
        else:
            edition = create_edition(
                candidates,
                settings.openai_api_key,
                settings.openai_model,
                settings.timezone,
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
        edition, candidates = _generate_live_edition(args, settings)
        edition = _add_images(edition, candidates, args.edition)

    with stage("Rendering the email"):
        html_path, html, text = write_outputs(edition, args.out, args.edition)
    print(f"  Saved {html_path}", flush=True)

    if args.send:
        assert settings is not None
        with stage("Sending through Resend"):
            message_id = send_email(
                api_key=settings.resend_api_key,
                from_email=settings.from_email,
                to_email=settings.to_email,
                subject=edition.subject,
                html=html,
                text=text,
                edition_date=edition.edition_date,
            )
        print(f"  Sent message {message_id}", flush=True)


if __name__ == "__main__":
    main()
