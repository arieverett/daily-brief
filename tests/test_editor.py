"""The AI editor's output is repaired into a usable, correctly dated edition."""

import json
from datetime import date
from importlib.resources import files

import pytest

from briefing import editor
from briefing.models import Candidate


def _sample_payload() -> dict:
    sample_path = files("briefing").joinpath("data", "sample_edition.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def _candidates_for(payload: dict) -> list[Candidate]:
    candidates: list[Candidate] = []
    for country_key, country in (("sweden", "Sweden"), ("indonesia", "Indonesia")):
        section = payload[country_key]
        stories = [section["lead"], *section["stories"], *section["quick_hits"]]
        for story in stories:
            candidates.append(
                Candidate(country=country, title=story["headline"], url=story["url"],
                          source=story["source"])
            )
        # Pad each country to the minimum pool size with distinct filler topics.
        for index in range(editor.MIN_CANDIDATES_PER_COUNTRY):
            candidates.append(
                Candidate(
                    country=country,
                    title=f"{country} filler topic number {index} about harbour cranes",
                    url=f"https://example.com/{country_key}/{index}",
                    source="Example",
                )
            )
    return candidates


def test_edition_date_comes_from_the_gate_not_the_model(monkeypatch):
    payload = _sample_payload()
    payload["edition_date"] = "September 29th"  # the model can't be trusted with this
    payload["date_label"] = "Someday"
    monkeypatch.setattr(editor, "_generate_structured_output", lambda **kwargs: payload)

    edition = editor.create_edition(_candidates_for(payload), "key", "gpt-5-mini",
                                    date(2026, 9, 29))
    assert edition.edition_date == "2026-09-29"
    assert edition.date_label == "Tuesday, September 29"


def test_indonesia_date_label_is_localized():
    assert editor.indonesia_date_label(date(2026, 9, 29)) == "Selasa, 29 September"
    assert editor.indonesia_date_label(date(2026, 10, 3)) == "Sabtu, 3 Oktober"


def test_unusable_editor_output_is_regenerated_once(monkeypatch):
    payload = _sample_payload()
    calls = []

    def flaky(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise json.JSONDecodeError("truncated", "", 0)
        return payload

    monkeypatch.setattr(editor, "_generate_structured_output", flaky)
    edition = editor.create_edition(_candidates_for(payload), "key", "gpt-5-mini",
                                    date(2026, 9, 29))
    assert len(calls) == 2
    assert edition.subject == payload["subject"]


def test_regeneration_gives_up_after_the_retry(monkeypatch):
    calls = []

    def broken(**kwargs):
        calls.append(1)
        raise json.JSONDecodeError("truncated", "", 0)

    monkeypatch.setattr(editor, "_generate_structured_output", broken)
    with pytest.raises(ValueError):
        editor.create_edition(_candidates_for(_sample_payload()), "key", "gpt-5-mini",
                              date(2026, 9, 29))
    assert len(calls) == editor.GENERATION_ATTEMPTS


def test_openai_sdk_contract_request_and_response_shape():
    """Exercise the real OpenAI SDK against a fake server.

    Catches SDK updates that change how our Responses API request is sent or how
    `output_text` is read, which unit tests that mock our own helper can't see.
    """
    import httpx

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "created_at": 1790000000,
                "model": "gpt-5-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": '{"ok": true}', "annotations": []}
                        ],
                    }
                ],
            },
        )

    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    result = editor._generate_structured_output(
        api_key="sk-test",
        model="gpt-5-mini",
        instructions="Be brief.",
        prompt="Say ok.",
        schema=schema,
        schema_name="probe",
        retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result == {"ok": True}
    assert seen["path"].endswith("/responses")
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["model"] == "gpt-5-mini"
    assert body["instructions"] == "Be brief."
    assert body["input"] == "Say ok."
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "probe",
        "schema": schema,
        "strict": True,
    }
    assert body["reasoning"] == {"effort": "low"}
