"""Tests for the strict `datalena.worktrail-handoff.v1` envelope adapter."""

from __future__ import annotations

import copy

import pytest

from worktrail.workqueue import pullhook_envelope as pe


def _envelope() -> dict:
    return {
        "schema": pe.SUPPORTED_SCHEMA,
        "event_id": "evt-0001",
        "dedupe_key": "evt-0001",
        "captured_by": "datalena:qa-pipeline",
        "target": {
            "remote": "behindthedash/datalena",
            "base_branch": "dev",
        },
        "handoff": {
            "focus": "Fix the flaky verify smoke test",
            "context": "Observed on three consecutive merges.",
            "suggested_approach": "Reproduce locally, then bound the wait.",
            "artifacts": ["https://example.test/brief.md"],
            "implementation_intent": "requested",
        },
        "source": {
            "repository": "behindthedash/datalena",
            "pr_number": 42,
            "merge_sha": "abc123",
            "run_id": "run-9",
            "brief_path": "docs/specs/usability-briefs/brief-1.md",
        },
        "finding": {
            "identity_key": "qa-pipeline/flaky-verify",
            "persona": "analyst",
            "route": "/reports",
            "journey": "review report",
            "step": "verify output",
            "severity": "medium",
            "heuristic": "feedback",
            "evidence_refs": ["https://example.test/run/9"],
            "judge_model": "model-x",
        },
    }


def _without(path: tuple[str, ...]) -> dict:
    env = _envelope()
    cursor = env
    for key in path[:-1]:
        cursor = cursor[key]
    del cursor[path[-1]]
    return env


def test_valid_v1_envelope_maps_to_create_handoff_arguments():
    args = pe.map_envelope(_envelope())

    assert args["focus"] == "Fix the flaky verify smoke test"
    assert args["repo"] == "behindthedash/datalena"
    assert args["remote"] == "behindthedash/datalena"
    assert args["base_branch"] == "dev"
    assert args["context"] == "Observed on three consecutive merges."
    assert args["approach"] == "Reproduce locally, then bound the wait."
    assert args["implementation_intent"] == "requested"
    assert args["captured_by"] == "datalena:qa-pipeline"


def test_mapped_arguments_are_accepted_by_create_handoff(tmp_path, monkeypatch):
    from worktrail.workqueue.create_handoff import create_handoff

    from .test_create_handoff import _stub_gh_pr_list

    # Keep the overlap scan off the real machine and off the network: a tmp
    # repo instead of a resolvable slug, and a stubbed `gh pr list`.
    gh_calls = _stub_gh_pr_list(monkeypatch, stdout="[]")
    repo = tmp_path / "repo"
    repo.mkdir()
    args = pe.map_envelope(_envelope())
    args["repo"] = str(repo)
    args["remote"] = "acme/widgets"

    result = create_handoff(queue_base=tmp_path, **args)

    assert gh_calls, "the overlap scan must go through the stubbed gh"
    body = (tmp_path / "queue" / f"{result['id']}.md").read_text()
    assert "datalena:qa-pipeline" in body
    assert "behindthedash/datalena" in body


def test_unknown_schema_is_rejected_and_names_the_schema():
    env = _envelope()
    env["schema"] = "datalena.worktrail-handoff.v2"

    with pytest.raises(pe.UnsupportedSchemaError) as excinfo:
        pe.map_envelope(env)

    assert excinfo.value.schema == "datalena.worktrail-handoff.v2"
    assert "datalena.worktrail-handoff.v2" in str(excinfo.value)


def test_missing_schema_is_rejected():
    with pytest.raises(pe.UnsupportedSchemaError):
        pe.map_envelope(_without(("schema",)))


@pytest.mark.parametrize(
    "path",
    [
        ("event_id",),
        ("captured_by",),
        ("target", "remote"),
        ("handoff", "focus"),
        ("source", "repository"),
        ("source", "pr_number"),
        ("source", "merge_sha"),
        ("finding", "identity_key"),
    ],
)
def test_each_missing_required_field_is_rejected(path):
    with pytest.raises(pe.EnvelopeError, match="missing required field"):
        pe.map_envelope(_without(path))


@pytest.mark.parametrize(
    "path",
    [
        ("event_id",),
        ("target", "remote"),
        ("handoff", "focus"),
        ("finding", "identity_key"),
    ],
)
def test_blank_required_field_is_rejected(path):
    env = _envelope()
    cursor = env
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = "   "

    with pytest.raises(pe.EnvelopeError, match="missing required field"):
        pe.map_envelope(env)


@pytest.mark.parametrize(
    "section,key",
    [
        (None, "priority"),
        ("target", "branch"),
        ("handoff", "questions"),
        ("source", "author"),
        ("finding", "unknown"),
    ],
)
def test_unknown_fields_are_rejected(section, key):
    env = _envelope()
    if section is None:
        env[key] = "x"
    else:
        env[section][key] = "x"

    with pytest.raises(pe.EnvelopeError, match="unknown"):
        pe.map_envelope(env)


def test_provenance_is_retained_in_mapped_artifacts():
    args = pe.map_envelope(_envelope())

    artifacts = args["artifacts"]
    assert pe.SUPPORTED_SCHEMA in artifacts
    assert "evt-0001" in artifacts
    assert "behindthedash/datalena" in artifacts
    assert "https://github.com/behindthedash/datalena/pull/42" in artifacts
    assert "abc123" in artifacts
    assert "qa-pipeline/flaky-verify" in artifacts
    assert "https://example.test/run/9" in artifacts
    assert "https://example.test/brief.md" in artifacts


def test_optional_fields_default_cleanly():
    env = _envelope()
    del env["target"]["base_branch"]
    for key in (
        "context",
        "suggested_approach",
        "artifacts",
        "implementation_intent",
    ):
        del env["handoff"][key]
    del env["source"]["run_id"]
    del env["source"]["brief_path"]
    del env["finding"]["evidence_refs"]

    args = pe.map_envelope(env)

    assert args["remote"] == "behindthedash/datalena"
    assert args["base_branch"] is None
    assert args["context"] is None
    assert args["implementation_intent"] is None
    assert "Evidence:" not in args["artifacts"]


def test_actual_datalena_publisher_v1_shape_is_accepted():
    event = {
        "schema": "datalena.worktrail-handoff.v1",
        "event_id": "v1:stable-key",
        "dedupe_key": "v1:stable-key",
        "source": {
            "repository": "behindthedash/datalena",
            "pr_number": 2962,
            "merge_sha": "deadbeef",
            "run_id": "actions-123",
            "brief_path": "docs/specs/usability-briefs/example.md",
        },
        "finding": {
            "identity_key": "persona:journey:step",
            "persona": "operator",
            "route": "/workspace",
            "journey": "review data",
            "step": "save view",
            "severity": "medium",
            "heuristic": "feedback",
            "evidence_refs": ["docs/evidence/example.md"],
            "judge_model": "reviewer-v1",
        },
        "handoff": {
            "focus": "Datalena usability: improve save view",
            "context": "Accepted finding persona:journey:step.",
            "suggested_approach": "Add a clearer save action.",
            "artifacts": ["https://github.com/behindthedash/datalena/pull/2962"],
        },
        "target": {"remote": "behindthedash/datalena", "base_branch": "dev"},
        "captured_by": "datalena-persona-usability",
    }

    args = pe.map_envelope(event)

    assert args["repo"] == "behindthedash/datalena"
    assert args["remote"] == "behindthedash/datalena"
    assert "persona:journey:step" in args["artifacts"]
    assert "deadbeef" in args["artifacts"]


def test_invalid_captured_by_is_rejected():
    env = _envelope()
    env["captured_by"] = "Datalena QA"

    with pytest.raises(pe.EnvelopeError, match="captured_by"):
        pe.map_envelope(env)


def test_invalid_implementation_intent_is_rejected():
    env = _envelope()
    env["handoff"]["implementation_intent"] = "maybe"

    with pytest.raises(pe.EnvelopeError, match="implementation_intent"):
        pe.map_envelope(env)


@pytest.mark.parametrize("payload", ["not-an-object", None, ["a"]])
def test_non_object_envelope_is_rejected(payload):
    with pytest.raises(pe.EnvelopeError):
        pe.map_envelope(payload)


def test_non_object_section_is_rejected():
    env = _envelope()
    env["target"] = "behindthedash/worktrail"

    with pytest.raises(pe.EnvelopeError, match="target must be an object"):
        pe.map_envelope(env)


def test_validation_does_not_mutate_the_input_envelope():
    env = _envelope()
    before = copy.deepcopy(env)

    pe.map_envelope(env)

    assert env == before
