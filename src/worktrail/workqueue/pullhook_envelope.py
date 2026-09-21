"""Strict allowlisted adapter for `datalena.worktrail-handoff.v1` envelopes.

Transport schema adaptation lives here so core queue semantics never see an
unvalidated external payload: an envelope is fully validated *before* any queue
mutation, and only then mapped to `create_handoff()` keyword arguments.

Future producers add an adapter rather than weakening this validation.
"""

from __future__ import annotations

import re
from typing import Any

SUPPORTED_SCHEMA = "datalena.worktrail-handoff.v1"

_CAPTURED_BY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*(:[A-Za-z0-9._/-]+)?$")
_VALID_INTENTS = {"requested", "planning-only", "unknown"}

# Allowlists: any key not named here is a rejection, not a silently ignored extra.
_TOP_LEVEL_KEYS = {"schema", "event_id", "captured_by", "target", "handoff", "source"}
_TARGET_KEYS = {"repo", "remote", "base_branch"}
_HANDOFF_KEYS = {"focus", "context", "approach", "artifacts", "implementation_intent"}
_SOURCE_KEYS = {"repository", "merged_pr", "finding", "evidence"}

_REQUIRED = (
    ("event_id", ("event_id",)),
    ("captured_by", ("captured_by",)),
    ("target.repo", ("target", "repo")),
    ("handoff.focus", ("handoff", "focus")),
    ("source.repository", ("source", "repository")),
    ("source.merged_pr", ("source", "merged_pr")),
    ("source.finding", ("source", "finding")),
)


class EnvelopeError(ValueError):
    """A payload that must not reach the work queue."""


class UnsupportedSchemaError(EnvelopeError):
    """The envelope declares a schema this build has no adapter for."""

    def __init__(self, schema: Any) -> None:
        self.schema = schema
        super().__init__(f"unsupported envelope schema: {schema!r}")


def _as_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise EnvelopeError(f"{field} must be a string")
    return value.strip()


def _section(envelope: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    value = envelope.get(name, {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise EnvelopeError(f"{name} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise EnvelopeError(f"unknown {name} field(s): {', '.join(unknown)}")
    return value


def _lookup(envelope: dict[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = envelope
    for key in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


def validate_envelope(envelope: Any) -> dict[str, Any]:
    """Return the normalized envelope, or raise `EnvelopeError`."""
    if not isinstance(envelope, dict):
        raise EnvelopeError("envelope must be an object")
    schema = envelope.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise UnsupportedSchemaError(schema)

    unknown = sorted(set(envelope) - _TOP_LEVEL_KEYS)
    if unknown:
        raise EnvelopeError(f"unknown envelope field(s): {', '.join(unknown)}")

    target = _section(envelope, "target", _TARGET_KEYS)
    handoff = _section(envelope, "handoff", _HANDOFF_KEYS)
    source = _section(envelope, "source", _SOURCE_KEYS)

    for field, path in _REQUIRED:
        value = _lookup(envelope, path)
        if value is None or not _as_str(value, field):
            raise EnvelopeError(f"missing required field: {field}")

    captured_by = _as_str(envelope["captured_by"], "captured_by")
    if not _CAPTURED_BY_PATTERN.match(captured_by):
        raise EnvelopeError(
            "captured_by must be a kebab-case source name, optionally "
            "followed by ':<qualifier>'"
        )

    intent = handoff.get("implementation_intent")
    if intent is None:
        intent = "requested"
    else:
        intent = _as_str(intent, "handoff.implementation_intent")
        if intent not in _VALID_INTENTS:
            raise EnvelopeError(
                "handoff.implementation_intent must be requested, "
                "planning-only, or unknown"
            )

    def optional(section: dict[str, Any], name: str, label: str) -> str | None:
        value = section.get(name)
        if value is None:
            return None
        return _as_str(value, label) or None

    return {
        "schema": SUPPORTED_SCHEMA,
        "event_id": _as_str(envelope["event_id"], "event_id"),
        "captured_by": captured_by,
        "target": {
            "repo": _as_str(target["repo"], "target.repo"),
            "remote": optional(target, "remote", "target.remote"),
            "base_branch": optional(target, "base_branch", "target.base_branch"),
        },
        "handoff": {
            "focus": _as_str(handoff["focus"], "handoff.focus"),
            "context": optional(handoff, "context", "handoff.context"),
            "approach": optional(handoff, "approach", "handoff.approach"),
            "artifacts": optional(handoff, "artifacts", "handoff.artifacts"),
            "implementation_intent": intent,
        },
        "source": {
            "repository": _as_str(source["repository"], "source.repository"),
            "merged_pr": _as_str(source["merged_pr"], "source.merged_pr"),
            "finding": _as_str(source["finding"], "source.finding"),
            "evidence": optional(source, "evidence", "source.evidence"),
        },
    }


def _artifacts(valid: dict[str, Any]) -> str:
    source = valid["source"]
    lines = [
        f"- External event: {valid['schema']} {valid['event_id']}",
        f"- Source repository: {source['repository']}",
        f"- Merged PR: {source['merged_pr']}",
        f"- Source finding: {source['finding']}",
    ]
    if source["evidence"]:
        lines.append(f"- Evidence: {source['evidence']}")
    producer = valid["handoff"]["artifacts"]
    if producer:
        lines.append(producer)
    return "\n".join(lines)


def map_envelope(envelope: Any) -> dict[str, Any]:
    """Validate `envelope` and return `create_handoff()` keyword arguments."""
    valid = validate_envelope(envelope)
    handoff = valid["handoff"]
    return {
        "focus": handoff["focus"],
        "repo": valid["target"]["repo"],
        "remote": valid["target"]["remote"],
        "base_branch": valid["target"]["base_branch"],
        "context": handoff["context"],
        "approach": handoff["approach"],
        "artifacts": _artifacts(valid),
        "implementation_intent": handoff["implementation_intent"],
        "captured_by": valid["captured_by"],
    }
