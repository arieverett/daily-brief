from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import contextmanager
from pathlib import Path

from .collect import add_article_images, add_indonesia_article_images, collect_candidates
from .config import DEFAULT_OUT_DIR, DEFAULT_SOURCES_PATH, Settings
from .editor import create_edition, create_indonesia_edition
from .models import edition_from_dict
from .render import render_html, render_indonesia_html, render_indonesia_text, render_text
from .send import send_email


@contextmanager
def stage(label: str):
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
        "--sample", action="store_true", help="Render bundled sample without network or API keys"
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def load_sample():
    sample_path = Path(__file__).with_name("data") / "sample_edition.json"
    return edition_from_dict(json.loads(sample_path.read_text(encoding="utf-8")))


def write_outputs(edition, out_dir: Path, edition_name: str = "standard") -> tuple[Path, str, str]:
    if edition_name == "indonesia":
        html = render_indonesia_html(edition)
        text = render_indonesia_text(edition)
    else:
        html = render_html(edition)
        text = render_text(edition)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = "indonesia-" if edition_name == "indonesia" else ""
    html_path = out_dir / f"{prefix}{edition.edition_date}.html"
    html_path.write_text(html, encoding="utf-8")
    (out_dir / f"{prefix}{edition.edition_date}.txt").write_text(text, encoding="utf-8")
    return html_path, html, text


def main() -> None:
    args = parse_args()
    if args.sample:
        if args.edition == "indonesia":
            raise SystemExit("The Indonesia sample is available as the approved HTML mockup")
        edition = load_sample()
        settings = None
    else:
        settings = Settings.from_env(require_delivery=args.send)
        with stage("Fetching news feeds"):
            candidates = asyncio.run(
                collect_candidates(args.sources, settings.lookback_hours, settings.max_candidates)
            )
        print(f"  Found {len(candidates)} usable stories", flush=True)
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
        with stage("Fetching publisher images"):
            if args.edition == "indonesia":
                edition = asyncio.run(add_indonesia_article_images(edition, candidates))
            else:
                edition = asyncio.run(add_article_images(edition, candidates))
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
