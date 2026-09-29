"""Check every configured feed and show what a morning run would have to work with.

    python -m briefing.feedcheck config/sources.yml config/indonesia_sources.yml

Runs in CI whenever the feed configuration changes. A single dead feed is a warning;
a country whose candidate pool would be too small to publish fails the check.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml

from .collect import FeedResult, deduplicate, fetch_feeds, pool_from_results
from .config import DEFAULT_LOOKBACK_HOURS
from .editor import MIN_CANDIDATES_PER_COUNTRY


def _country_counts(results: list[FeedResult], tier: str) -> dict[str, int]:
    unique = deduplicate([c for r in results if r.tier == tier for c in r.candidates])
    counts: dict[str, int] = {}
    for candidate in unique:
        counts[candidate.country] = counts.get(candidate.country, 0) + 1
    return counts


def build_report(
    path: Path, results: list[FeedResult], lookback_hours: int, max_candidates: int
) -> tuple[list[str], list[str], list[str]]:
    """Return (report lines, dead-feed warnings, blocking errors)."""
    lines = [f"{path} ({lookback_hours}h lookback)"]
    for result in results:
        status = "ok" if result.ok else f"FAILED {result.error}"
        newest = f", newest {result.newest:%Y-%m-%d %H:%M}Z" if result.newest else ""
        lines.append(
            f"  [{result.tier:7}] {result.name}: {result.entries} entries, "
            f"{len(result.candidates)} usable{newest} - {status}"
        )

    primary = _country_counts(results, "primary")
    backup = _country_counts(results, "backup")
    pool = pool_from_results(results, max_candidates)
    countries = sorted({r.country for r in results})
    errors: list[str] = []
    for country in countries:
        in_pool = sum(item.country == country for item in pool)
        lines.append(
            f"  {country}: {primary.get(country, 0)} fresh from primary, "
            f"{backup.get(country, 0)} from backup, {in_pool} in the editor's pool"
        )
        if in_pool < MIN_CANDIDATES_PER_COUNTRY:
            errors.append(
                f"{path}: only {in_pool} {country} candidates; the editor needs "
                f"{MIN_CANDIDATES_PER_COUNTRY} to publish."
            )

    warnings = [f"{path}: {r.name} - {r.error}" for r in results if not r.ok]
    return lines, warnings, errors


def _escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--max-candidates", type=int, default=40)
    args = parser.parse_args(argv)

    in_actions = os.getenv("GITHUB_ACTIONS") == "true"
    failed = False
    for path in args.paths:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        results = asyncio.run(fetch_feeds(config.get("feeds", []), args.lookback_hours))
        lines, warnings, errors = build_report(
            path, results, args.lookback_hours, args.max_candidates
        )
        print("\n".join(lines), flush=True)
        if in_actions:
            print(f"::notice title=Feed report {path}::{_escape(chr(10).join(lines))}")
            for warning in warnings[:10]:
                print(f"::warning title=Dead feed::{_escape(warning)}")
            for error in errors:
                print(f"::error title=Candidate pool too small::{_escape(error)}")
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
