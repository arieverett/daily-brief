from email.message import EmailMessage

from briefing import reference
from briefing.main import load_sample
from briefing.models import IndonesiaEdition


def _issue(body: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = "Test issue"
    message["Date"] = "Fri, 16 Oct 2026 05:18:43 -0400"
    message.set_content(body)
    return message.as_bytes()


def test_reference_issue_is_reduced_to_numbers_without_its_text():
    body = (
        "Presented By Some Sponsor\n\n"
        "Wall Street is treating the company like a houseguest who never leaves (and eats "
        "everything). Shares fell 15% on Wednesday, so investors aren't laughing.\n\n"
        "Core concern: The company's $50 billion debt pile keeps growing, and lenders want "
        "answers before they write another check...right?\n\n"
        "Share the Brew and unlock swag\n"
    )
    info, paras = reference.issue_paragraphs(_issue(body))
    assert info == {"date": "2026-10-16", "subject": "Test issue"}
    assert not any("Sponsor" in para or "Share the Brew" in para for para in paras)

    metrics = reference.measure(paras)
    assert set(metrics) == set(reference.METRICS)
    assert metrics["run_in_share_pct"] == 50.0
    assert metrics["asides_per_1000_words"] > 0 and metrics["questions_per_1000_words"] > 0
    assert metrics["contractions_per_100_words"] > 0


def test_stored_reference_is_numbers_only():
    issues = reference.load_reference()
    assert len(issues) >= 2
    for issue in issues:
        assert set(issue) == {"date", "subject", "added", "metrics"}
        assert set(issue["metrics"]) == set(reference.METRICS)
        assert all(isinstance(value, (int, float)) for value in issue["metrics"].values())


def test_editions_are_compared_to_the_reference():
    report = reference.compare_to_reference(load_sample())
    assert report.startswith("Voice vs Morning Brew (")
    assert "Words per sentence:" in report
    # Indonesian copy isn't comparable with English metrics.
    sample = load_sample()
    indonesian = IndonesiaEdition(
        sample.edition_date, sample.date_label, sample.subject, sample.preview_text,
        sample.indonesia, sample.setup,
    )
    assert reference.compare_to_reference(indonesian) == ""
