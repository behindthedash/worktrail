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
_TOP_LEVEL_KEYS = {
    "schema",
    "event_id",
    "dedupe_key",
    "captured_by",
    "target",
    "handoff",
    "source",
    "finding",
}
_TARGET_KEYS = {"remote", "base_branch"}
_HANDOFF_KEYS = {
    "focus",
    "context",
    "suggested_approach",
    "artifacts",
    "implementation_intent",
}
_SOURCE_KEYS = {"repository", "pr_number", "merge_sha", "run_id", "brief_path"}
_FINDING_KEYS = {
    "identity_key",
    "persona",
    "route",
    "journey",
    "step",
    "severity",
    "heuristic",
    "evidence_refs",
    "judge_model",
}

_REQUIRED = (
    ("event_id", ("event_id",)),
    ("captured_by", ("captured_by",)),
    ("target.remote", ("target", "remote")),
    ("handoff.focus", ("handoff", "focus")),
    ("source.repository", ("source", "repository")),
    ("source.pr_number", ("source", "pr_number")),
    ("source.merge_sha", ("source", "merge_sha")),
    ("finding.identity_key", ("finding", "identity_key")),
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
    finding = _section(envelope, "finding", _FINDING_KEYS)

    for field, path in _REQUIRED:
        value = _lookup(envelope, path)
        if path == ("source", "pr_number"):
            valid = value is not None
        else:
            valid = value is not None and bool(_as_str(value, field))
        if not valid:
            raise EnvelopeError(f"missing required field: {field}")

    event_id = _as_str(envelope["event_id"], "event_id")
    dedupe_key = envelope.get("dedupe_key")
    if dedupe_key is not None and _as_str(dedupe_key, "dedupe_key") != event_id:
        raise EnvelopeError("dedupe_key must match event_id")

    captured_by = _as_str(envelope["captured_by"], "captured_by")
    if not _CAPTURED_BY_PATTERN.match(captured_by):
        raise EnvelopeError(
            "captured_by must be a kebab-case source name, optionally "
            "followed by ':<qualifier>'"
        )

    # An absent intent stays absent: silence from the producer is not consent
    # to auto-implement, so the downstream route asks instead.
    intent = handoff.get("implementation_intent")
    if intent is not None:
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

    pr_number = source["pr_number"]
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1:
        raise EnvelopeError("source.pr_number must be a positive integer")

    def string_list(value: Any, label: str) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise EnvelopeError(f"{label} must be a list of strings")
        return [item.strip() for item in value if item.strip()]

    artifacts = handoff.get("artifacts", [])
    if artifacts is None:
        artifacts = []
    artifacts = string_list(artifacts, "handoff.artifacts")
    evidence_refs = finding.get("evidence_refs", [])
    if evidence_refs is None:
        evidence_refs = []
    evidence_refs = string_list(evidence_refs, "finding.evidence_refs")

    normalized_finding: dict[str, Any] = {
        "identity_key": _as_str(finding["identity_key"], "finding.identity_key"),
        "evidence_refs": evidence_refs,
    }
    for key in _FINDING_KEYS - {"identity_key", "evidence_refs"}:
        normalized_finding[key] = optional(finding, key, f"finding.{key}")

    return {
        "schema": SUPPORTED_SCHEMA,
        "event_id": _as_str(envelope["event_id"], "event_id"),
        "dedupe_key": event_id,
        "captured_by": captured_by,
        "target": {
            "remote": optional(target, "remote", "target.remote"),
            "base_branch": optional(target, "base_branch", "target.base_branch"),
        },
        "handoff": {
            "focus": _as_str(handoff["focus"], "handoff.focus"),
            "context": optional(handoff, "context", "handoff.context"),
            "approach": optional(
                handoff, "suggested_approach", "handoff.suggested_approach"
            ),
            "artifacts": artifacts,
            "implementation_intent": intent,
        },
        "source": {
            "repository": _as_str(source["repository"], "source.repository"),
            "pr_number": pr_number,
            "merge_sha": _as_str(source["merge_sha"], "source.merge_sha"),
            "run_id": optional(source, "run_id", "source.run_id"),
            "brief_path": optional(source, "brief_path", "source.brief_path"),
        },
        "finding": normalized_finding,
    }


def _artifacts(valid: dict[str, Any]) -> str:
    source = valid["source"]
    finding = valid["finding"]
    lines = [
        f"- External event: {valid['schema']} {valid['event_id']}",
        f"- Source repository: {source['repository']}",
        f"- Merged PR: https://github.com/{source['repository']}/pull/{source['pr_number']}",
        f"- Merge commit: {source['merge_sha']}",
        f"- Source finding: {finding['identity_key']}",
    ]
    if source["run_id"]:
        lines.append(f"- Source run: {source['run_id']}")
    if source["brief_path"]:
        lines.append(f"- Source brief: {source['brief_path']}")
    lines.extend(f"- Evidence: {ref}" for ref in finding["evidence_refs"])
    producer = valid["handoff"]["artifacts"]
    if producer:
        lines.extend(f"- Producer artifact: {artifact}" for artifact in producer)
    return "\n".join(lines)


def map_envelope(envelope: Any) -> dict[str, Any]:
    """Validate `envelope` and return `create_handoff()` keyword arguments."""
    valid = validate_envelope(envelope)
    handoff = valid["handoff"]
    return {
        "focus": handoff["focus"],
        "repo": valid["source"]["repository"],
        "remote": valid["target"]["remote"],
        "base_branch": valid["target"]["base_branch"],
        "context": handoff["context"],
        "approach": handoff["approach"],
        "artifacts": _artifacts(valid),
        "implementation_intent": handoff["implementation_intent"],
        "captured_by": valid["captured_by"],
    }
