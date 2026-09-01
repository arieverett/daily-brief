from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .collect import add_article_images, collect_candidates
from .config import DEFAULT_OUT_DIR, DEFAULT_SOURCES_PATH, Settings
from .editor import create_edition
from .models import edition_from_dict
from .render import render_html, render_text
from .send import send_email


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Daily Brief")
    parser.add_argument("--send", action="store_true", help="Send through Resend after generation")
    parser.add_argument(
        "--sample", action="store_true", help="Render bundled sample without network or API keys"
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def load_sample():
    sample_path = Path(__file__).with_name("data") / "sample_edition.json"
    return edition_from_dict(json.loads(sample_path.read_text(encoding="utf-8")))


def write_outputs(edition, out_dir: Path) -> tuple[Path, str, str]:
    html = render_html(edition)
    text = render_text(edition)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{edition.edition_date}.html"
    html_path.write_text(html, encoding="utf-8")
    (out_dir / f"{edition.edition_date}.txt").write_text(text, encoding="utf-8")
    return html_path, html, text


def main() -> None:
    args = parse_args()
    if args.sample:
        edition = load_sample()
        settings = None
    else:
        settings = Settings.from_env(require_delivery=args.send)
        candidates = asyncio.run(
            collect_candidates(args.sources, settings.lookback_hours, settings.max_candidates)
        )
        edition = create_edition(
            candidates,
            settings.openai_api_key,
            settings.openai_model,
            settings.timezone,
        )
        edition = asyncio.run(add_article_images(edition, candidates))
    html_path, html, text = write_outputs(edition, args.out)
    print(f"Rendered {html_path}")
    if args.send:
        assert settings is not None
        message_id = send_email(
            api_key=settings.resend_api_key,
            from_email=settings.from_email,
            to_email=settings.to_email,
            subject=edition.subject,
            html=html,
            text=text,
            edition_date=edition.edition_date,
        )
        print(f"Sent message {message_id}")


if __name__ == "__main__":
    main()
