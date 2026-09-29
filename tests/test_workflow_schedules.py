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
        for edition in ("standard", "indonesia"):
            job = jobs[edition]
            assert job["uses"] == "./.github/workflows/send-newsletter.yml"
            assert job["with"]["edition"] == edition
            assert job["permissions"]["actions"] == "read"
        assert "INDONESIA_BRIEF_TO_EMAIL" in jobs["indonesia"]["secrets"]


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

    record = steps["Record delivery"]
    assert record["with"]["name"] == (
        "delivered-${{ inputs.edition }}-${{ steps.guard.outputs.date }}"
    )
    assert "!inputs.force" in record["if"]
    assert record["continue-on-error"] is True

    alert = steps["Email a failure alert"]
    assert alert["if"].startswith("failure()")
    assert alert["env"]["ALERT_TO_EMAIL"] == "${{ secrets.BRIEF_TO_EMAIL }}"


def test_ci_skips_marker_only_delivery_commits():
    workflow = load("test.yml")
    ignored = set(triggers(workflow)["push"]["paths-ignore"])
    assert ignored == {".github/run-briefs-now"}
    assert workflow["concurrency"]["cancel-in-progress"] is True
