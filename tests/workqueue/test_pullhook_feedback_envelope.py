"""Validation and canonical mapping for GGB feedback events."""

from __future__ import annotations

import copy
import uuid

import pytest

from worktrail.workqueue import pullhook_feedback_envelope as adapter

EVENT_ID = "5f8089f5-dc70-4b5a-a482-7e524f53af53"


def envelope() -> dict:
    return {
        "schema": adapter.SUPPORTED_SCHEMA,
        "event_id": EVENT_ID,
        "source": {
            "id": adapter.TRUSTED_SOURCE_ID,
            "kind": adapter.TRUSTED_SOURCE_KIND,
        },
        "occurred_at": "2026-10-06T09:30:00-07:00",
        "feedback": {
            "id": EVENT_ID,
            "title": "Improve the event signup page",
            "body": "The form needs a clearer confirmation state.",
            "url": "https://example.test/events/123",
            "metadata": {
                "pageUrl": "/events/123",
                "elementSelector": "#signup-form button[type=submit]",
                "elementText": "Join event",
                "sourceFileHint": "src/app/events/[id]/page.tsx",
                "componentHint": "SignupForm",
                "changeKindGuess": "usability",
                "viewport": {"width": 1280, "height": 720},
            },
        },
    }


def test_valid_feedback_maps_to_trusted_canonical_handoff():
    mapped = adapter.map_envelope(envelope())

    assert mapped["focus"] == "Improve the event signup page"
    assert mapped["context"] == "The form needs a clearer confirmation state."
    assert mapped["repo"] == "gracefully-giving-back"
    assert "remote" not in mapped
    assert "base_branch" not in mapped
    assert "implementation_intent" not in mapped
    assert mapped["captured_by"] == "gracefully-giving-back:feedback"
    for provenance in (
        f"worktrail.feedback.v1 {EVENT_ID}",
        "https://example.test/events/123",
        "2026-10-06T09:30:00-07:00",
        '"sourceFileHint":"src/app/events/[id]/page.tsx"',
    ):
        assert provenance in mapped["artifacts"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda event: event.update(schema="worktrail.feedback.v2"),
        lambda event: event.update(route={"repo": "attacker/repo"}),
        lambda event: event["source"].update(id="other-repo"),
        lambda event: event["source"].update(kind="other-kind"),
        lambda event: event["feedback"].update(id=str(uuid.uuid4())),
        lambda event: event["feedback"].update(title="  "),
        lambda event: event["feedback"].update(url="data:image/png;base64,AAAA"),
        lambda event: event["feedback"].update(screenshot="data:image/png;base64,AAAA"),
        lambda event: event["feedback"]["metadata"].update(extra="no"),
        lambda event: event["feedback"]["metadata"].update(
            screenshotDataUrl="data:image/png;base64,AAAA"
        ),
        lambda event: event.update(occurred_at="not-a-date"),
    ],
)
def test_malformed_or_untrusted_envelopes_are_rejected(mutate):
    event = copy.deepcopy(envelope())
    mutate(event)
    with pytest.raises(adapter.FeedbackEnvelopeError):
        adapter.map_envelope(event)


def test_unknown_schema_has_specific_error():
    event = envelope()
    event["schema"] = "future.schema.v9"
    with pytest.raises(adapter.UnsupportedFeedbackSchemaError):
        adapter.map_envelope(event)


def test_oversized_envelope_is_rejected():
    event = envelope()
    event["feedback"]["body"] = "x" * adapter.MAX_ENVELOPE_BYTES
    with pytest.raises(adapter.FeedbackEnvelopeError, match="exceeds"):
        adapter.map_envelope(event)


def test_source_file_hint_is_retained_as_text_not_used_for_routing():
    event = envelope()
    event["feedback"]["metadata"]["sourceFileHint"] = "../../outside/.env"
    mapped = adapter.map_envelope(event)
    assert mapped["repo"] == "gracefully-giving-back"
    assert "../../outside/.env" in mapped["artifacts"]
