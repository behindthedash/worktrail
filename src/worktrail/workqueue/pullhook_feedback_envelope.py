"""Strict adapter for GGB's ``worktrail.feedback.v1`` PullHook envelope."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

SUPPORTED_SCHEMA = "worktrail.feedback.v1"
TRUSTED_SOURCE_ID = "gracefully-giving-back"
TRUSTED_SOURCE_KIND = "website-feedback"
TRUSTED_REPO = "gracefully-giving-back"
MAX_ENVELOPE_BYTES = 262_144

_TOP_KEYS = {"schema", "event_id", "source", "occurred_at", "feedback"}
_SOURCE_KEYS = {"id", "kind"}
_FEEDBACK_KEYS = {"id", "title", "body", "url", "metadata"}
_METADATA_KEYS = {
    "pageUrl",
    "elementSelector",
    "elementText",
    "sourceFileHint",
    "componentHint",
    "changeKindGuess",
    "viewport",
}
_TEXT_METADATA_KEYS = _METADATA_KEYS - {"viewport"}


class FeedbackEnvelopeError(ValueError):
    """A feedback event that must not be materialized."""


class UnsupportedFeedbackSchemaError(FeedbackEnvelopeError):
    """The event schema has no supported adapter."""


def _object(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FeedbackEnvelopeError(f"{field} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise FeedbackEnvelopeError(f"unknown {field} field(s): {', '.join(unknown)}")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeedbackEnvelopeError(f"{field} must be a non-empty string")
    return value.strip()


def _absolute_http_url(value: Any, field: str) -> str:
    url = _text(value, field)
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
    ):
        raise FeedbackEnvelopeError(f"{field} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise FeedbackEnvelopeError(f"{field} must not contain credentials")
    return url


def validate_envelope(envelope: Any) -> dict[str, Any]:
    """Validate and normalize one supported GGB feedback event."""
    if not isinstance(envelope, dict):
        raise FeedbackEnvelopeError("envelope must be an object")
    if envelope.get("schema") != SUPPORTED_SCHEMA:
        raise UnsupportedFeedbackSchemaError(
            f"unsupported envelope schema: {envelope.get('schema')!r}"
        )
    _object(envelope, "envelope", _TOP_KEYS)

    event_id = _text(envelope.get("event_id"), "event_id")
    try:
        uuid.UUID(event_id)
    except ValueError, AttributeError:
        raise FeedbackEnvelopeError("event_id must be a UUID") from None

    source = _object(envelope.get("source"), "source", _SOURCE_KEYS)
    if (
        source.get("id") != TRUSTED_SOURCE_ID
        or source.get("kind") != TRUSTED_SOURCE_KIND
    ):
        raise FeedbackEnvelopeError("untrusted feedback source identity")

    occurred_at = _text(envelope.get("occurred_at"), "occurred_at")
    try:
        parsed_time = datetime.fromisoformat(occurred_at)
    except ValueError:
        raise FeedbackEnvelopeError(
            "occurred_at must be an ISO 8601 timestamp"
        ) from None
    if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
        raise FeedbackEnvelopeError("occurred_at must include a timezone")

    feedback = _object(envelope.get("feedback"), "feedback", _FEEDBACK_KEYS)
    feedback_id = _text(feedback.get("id"), "feedback.id")
    try:
        uuid.UUID(feedback_id)
    except ValueError, AttributeError:
        raise FeedbackEnvelopeError("feedback.id must be a UUID") from None
    if feedback_id != event_id:
        raise FeedbackEnvelopeError("event_id and feedback.id must match")
    title = _text(feedback.get("title"), "feedback.title")
    body = _text(feedback.get("body"), "feedback.body")
    source_url = feedback.get("url")
    if source_url is not None:
        source_url = _absolute_http_url(source_url, "feedback.url")

    metadata = _object(feedback.get("metadata"), "feedback.metadata", _METADATA_KEYS)
    normalized_metadata: dict[str, Any] = {}
    for key in _TEXT_METADATA_KEYS:
        value = metadata.get(key)
        if value is None and key in {"elementText", "sourceFileHint", "componentHint"}:
            continue
        normalized_metadata[key] = _text(value, f"feedback.metadata.{key}")
    page_url = normalized_metadata["pageUrl"]
    if page_url.lower().startswith("data:"):
        raise FeedbackEnvelopeError("feedback.metadata.pageUrl must not be a data URL")

    viewport = metadata.get("viewport")
    if not isinstance(viewport, dict) or set(viewport) != {"width", "height"}:
        raise FeedbackEnvelopeError("feedback.metadata.viewport needs width and height")
    for key, value in viewport.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise FeedbackEnvelopeError(
                f"feedback.metadata.viewport.{key} must be a positive integer"
            )
    normalized_metadata["viewport"] = {
        "width": viewport["width"],
        "height": viewport["height"],
    }

    normalized_feedback: dict[str, Any] = {
        "id": feedback_id,
        "title": title,
        "body": body,
        "url": source_url,
        "metadata": normalized_metadata,
    }
    return {
        "schema": SUPPORTED_SCHEMA,
        "event_id": event_id,
        "source": {"id": TRUSTED_SOURCE_ID, "kind": TRUSTED_SOURCE_KIND},
        "occurred_at": occurred_at,
        "feedback": normalized_feedback,
    }


def map_envelope(envelope: Any) -> dict[str, Any]:
    """Map a validated event to canonical ``create_handoff`` arguments."""
    try:
        encoded = json.dumps(
            envelope, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except TypeError, ValueError:
        raise FeedbackEnvelopeError("envelope must contain JSON values") from None
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise FeedbackEnvelopeError(
            f"feedback envelope exceeds {MAX_ENVELOPE_BYTES} bytes"
        )
    valid = validate_envelope(envelope)
    feedback = valid["feedback"]
    metadata_json = json.dumps(
        feedback["metadata"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    artifacts = [
        f"- External event: {valid['schema']} {valid['event_id']}",
        f"- Feedback source: {valid['source']['id']} ({valid['source']['kind']})",
        f"- Occurred at: {valid['occurred_at']}",
        f"- Captured metadata: {metadata_json}",
    ]
    if feedback["url"]:
        artifacts.insert(2, f"- Feedback URL: {feedback['url']}")
    return {
        "focus": feedback["title"],
        "repo": TRUSTED_REPO,
        "context": feedback["body"],
        "artifacts": "\n".join(artifacts),
        "captured_by": "gracefully-giving-back:feedback",
    }
