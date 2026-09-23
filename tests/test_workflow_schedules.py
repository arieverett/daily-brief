"""Prevent newsletter schedule and deduplication regressions."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize("filename", ["daily.yml", "indonesia-daily.yml"])
def test_both_newsletters_use_external_primary_and_new_york_recovery(filename: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))
    # YAML 1.1 treats the key "on" as boolean True.
    triggers = workflow.get("on", workflow.get(True))
    schedule = triggers["schedule"]

    # The exact 06:00 New York send is triggered externally by touching the
    # shared marker. GitHub cron remains only as a 06:20 recovery path.
    assert schedule == [
        {"cron": "20 6 * * 1-6", "timezone": "America/New_York"},
    ]
    assert ".github/run-briefs-now" in triggers["push"]["paths"]
    assert workflow["jobs"]["send"]["permissions"]["actions"] == "read"


def test_ci_skips_marker_only_delivery_commits() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "test.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    ignored = set(triggers["push"]["paths-ignore"])

    assert ignored == {
        ".github/run-briefs-now",
        ".github/run-standard-now",
        ".github/run-indonesia-now",
    }
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_delivery_workflow_checks_weekday_and_prevents_duplicate_retries() -> None:
    workflow = yaml.safe_load(
        (WORKFLOWS / "send-newsletter.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["send"]["steps"]
    guard = next(step for step in steps if step.get("id") == "delivery_guard")

    assert guard["if"] == "github.event_name != 'workflow_dispatch'"
    assert "TZ=America/New_York date +%u" in guard["run"]
    assert 'echo "skip=true" >> "$GITHUB_OUTPUT"' in guard["run"]
    assert ".conclusion == \"success\"" in guard["run"]
    generate = next(step for step in steps if step.get("name") == "Generate and send newsletter")
    assert generate["if"] == "steps.delivery_guard.outputs.skip != 'true'"
    assert "BRIEF_DELIVERY_NONCE" in generate["env"]
