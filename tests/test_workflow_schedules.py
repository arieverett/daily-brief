"""Lock in the delivery design: exact external trigger, hourly backstop, one guard."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    # YAML 1.1 treats the key "on" as boolean True.
    return workflow.get("on", workflow.get(True))


def test_only_the_expected_delivery_workflows_exist():
    assert {path.name for path in WORKFLOWS.glob("*.yml")} == {
        "briefs.yml",
        "briefs-backstop.yml",
        "feed-check.yml",
        "send-newsletter.yml",
        "test.yml",
    }


def test_primary_workflow_is_dispatched_externally_and_never_scheduled():
    on = triggers(load("briefs.yml"))
    # A schedule would let GitHub auto-disable the primary path after 60 quiet days.
    assert "schedule" not in on
    assert "workflow_dispatch" in on
    assert on["workflow_dispatch"]["inputs"]["force"]["default"] is False
    assert on["push"]["paths"] == [".github/run-briefs-now"]


def test_backstop_polls_from_the_evening_before_through_late_morning():
    workflow = load("briefs-backstop.yml")
    assert triggers(workflow)["schedule"] == [
        {"cron": "17 20-23 * * 0-5", "timezone": "America/New_York"},
        {"cron": "17 0-11 * * 1-6", "timezone": "America/New_York"},
    ]
    assert workflow["concurrency"] == {"group": "briefs-backstop", "cancel-in-progress": False}
    gate = workflow["jobs"]["gate"]
    assert "--backstop" in gate["steps"][-1]["run"]
    assert gate["timeout-minutes"] >= 60  # it may wait up to 55 min for 05:55


def test_every_edition_job_uses_the_shared_delivery_workflow():
    for name in ("briefs.yml", "briefs-backstop.yml"):
        jobs = load(name)["jobs"]
        for edition in ("standard", "indonesia", "us"):
            job = jobs[edition]
            assert job["uses"] == "./.github/workflows/send-newsletter.yml"
            assert job["with"]["edition"] == edition
            assert job["permissions"]["actions"] == "read"
        assert "INDONESIA_BRIEF_TO_EMAIL" in jobs["indonesia"]["secrets"]
        # Two-Mile High goes to Ari only.
        assert "INDONESIA_BRIEF_TO_EMAIL" not in jobs["us"]["secrets"]


def test_untargeted_runs_send_every_edition():
    # The external trigger and the marker push carry no `editions` input.
    jobs = load("briefs.yml")["jobs"]
    for edition in ("standard", "indonesia", "us"):
        assert jobs[edition]["if"].startswith("${{ !inputs.editions || inputs.editions == 'all'")
    on = triggers(load("briefs.yml"))
    assert on["workflow_dispatch"]["inputs"]["editions"]["default"] == "all"
    backstop = load("briefs-backstop.yml")["jobs"]
    assert "--editions all" in backstop["gate"]["steps"][-1]["run"]
    assert backstop["gate"]["outputs"]["us"] == "${{ steps.gate.outputs.us }}"


def test_us_edition_uses_its_own_sources_and_only_the_primary_recipient():
    send = next(
        step for step in load("send-newsletter.yml")["jobs"]["send"]["steps"]
        if step.get("name") == "Generate and send newsletter"
    )
    us_branch = send["run"].split('== "us" ]]; then', 1)[1].split("else", 1)[0]
    assert 'BRIEF_TO_EMAIL="${PRIMARY_TO_EMAIL}"' in us_branch
    assert 'sources="config/us_sources.yml"' in us_branch


def test_delivery_job_serializes_per_edition_and_rechecks_before_sending():
    job = load("send-newsletter.yml")["jobs"]["send"]
    assert job["concurrency"] == {
        "group": "deliver-${{ inputs.edition }}",
        "cancel-in-progress": False,
    }
    steps = {step.get("name") or step.get("uses"): step for step in job["steps"]}
    guard = steps["Check whether today's edition still needs sending"]
    assert "briefing.delivery" in guard["run"] and "--force" in guard["run"]

    send = steps["Generate and send newsletter"]
    assert send["if"] == "steps.guard.outputs.any == 'true'"
    assert send["env"]["BRIEF_EDITION_DATE"] == "${{ steps.guard.outputs.date }}"
    assert send["env"]["BRIEF_SEND_AT"] == "${{ !inputs.force && '06:00' || '' }}"
    assert "inputs.force" in send["env"]["BRIEF_DELIVERY_NONCE"]

    # The weather's climate history is cached between runs and can never block a send.
    cache = steps["Restore weather history"]
    assert cache["uses"].startswith("actions/cache@")
    assert cache["continue-on-error"] is True
    assert cache["with"]["path"] == send["env"]["BRIEF_WEATHER_CACHE"]
    assert "${{ inputs.edition }}" in cache["with"]["key"]

    record = steps["Record delivery"]
    assert record["with"]["name"] == (
        "delivered-${{ inputs.edition }}-${{ steps.guard.outputs.date }}"
    )
    assert "!inputs.force" in record["if"]
    assert record["continue-on-error"] is True

    archive = steps["Archive edition"]
    assert archive["with"]["retention-days"] == 90
    assert archive["continue-on-error"] is True
    assert "cancelled()" in archive["if"]

    alert = steps["Email a failure alert"]
    assert alert["if"].startswith("failure()")
    assert alert["env"]["ALERT_TO_EMAIL"] == "${{ secrets.BRIEF_TO_EMAIL }}"


def test_ci_skips_marker_only_delivery_commits():
    workflow = load("test.yml")
    ignored = set(triggers(workflow)["push"]["paths-ignore"])
    assert ignored == {".github/run-briefs-now"}
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_production_and_ci_install_exactly_the_locked_versions():
    install = next(
        step for step in load("send-newsletter.yml")["jobs"]["send"]["steps"]
        if step.get("name") == "Install application"
    )
    assert "uv sync --locked" in install["run"]
    send = next(
        step for step in load("send-newsletter.yml")["jobs"]["send"]["steps"]
        if step.get("name") == "Generate and send newsletter"
    )
    assert "uv run --no-sync python -m briefing" in send["run"]

    test_runs = " ".join(step.get("run", "") for step in load("test.yml")["jobs"]["test"]["steps"])
    assert "uv sync --locked --extra dev" in test_runs


def test_delivery_runners_are_pinned():
    assert load("send-newsletter.yml")["jobs"]["send"]["runs-on"] == "ubuntu-24.04"
    assert load("briefs-backstop.yml")["jobs"]["gate"]["runs-on"] == "ubuntu-24.04"


def test_lockfile_covers_every_runtime_dependency():
    import tomllib

    root = WORKFLOWS.parents[1]
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    locked = {package["name"]: package["version"] for package in lock["package"]}
    assert {"openai", "httpx", "feedparser", "jinja2", "pyyaml", "python-dateutil"} <= set(locked)
    assert all(locked.values())


def test_dependabot_groups_weekly_updates():
    config = yaml.safe_load(
        (WORKFLOWS.parent / "dependabot.yml").read_text(encoding="utf-8")
    )
    ecosystems = {update["package-ecosystem"]: update for update in config["updates"]}
    assert set(ecosystems) == {"uv", "github-actions"}
    for update in config["updates"]:
        assert update["schedule"]["interval"] == "weekly"
        assert update.get("groups")
        # Tests can't exercise the real OpenAI API or delivery workflows, so majors are
        # deliberate upgrades rather than weekly bumps.
        assert {"dependency-name": "*", "update-types": ["version-update:semver-major"]} in (
            update["ignore"]
        )
