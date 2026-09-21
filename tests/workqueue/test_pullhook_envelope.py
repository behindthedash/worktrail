"""Tests for the strict `datalena.worktrail-handoff.v1` envelope adapter."""

from __future__ import annotations

import copy

import pytest

from worktrail.workqueue import pullhook_envelope as pe


def _envelope() -> dict:
    return {
        "schema": pe.SUPPORTED_SCHEMA,
        "event_id": "evt-0001",
        "captured_by": "datalena:qa-pipeline",
        "target": {
            "repo": "behindthedash/worktrail",
            "remote": "git@github.com:behindthedash/worktrail.git",
            "base_branch": "main",
        },
        "handoff": {
            "focus": "Fix the flaky verify smoke test",
            "context": "Observed on three consecutive merges.",
            "approach": "Reproduce locally, then bound the wait.",
            "artifacts": "- Producer note: see run log",
            "implementation_intent": "requested",
        },
        "source": {
            "repository": "behindthedash/datalena",
            "merged_pr": "https://github.com/behindthedash/datalena/pull/42",
            "finding": "qa-pipeline/flaky-verify",
            "evidence": "https://example.test/run/9",
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
    assert args["repo"] == "behindthedash/worktrail"
    assert args["remote"] == "git@github.com:behindthedash/worktrail.git"
    assert args["base_branch"] == "main"
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
        ("target", "repo"),
        ("handoff", "focus"),
        ("source", "repository"),
        ("source", "merged_pr"),
        ("source", "finding"),
    ],
)
def test_each_missing_required_field_is_rejected(path):
    with pytest.raises(pe.EnvelopeError, match="missing required field"):
        pe.map_envelope(_without(path))


@pytest.mark.parametrize(
    "path",
    [
        ("event_id",),
        ("target", "repo"),
        ("handoff", "focus"),
        ("source", "finding"),
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
    assert "qa-pipeline/flaky-verify" in artifacts
    assert "https://example.test/run/9" in artifacts
    assert "- Producer note: see run log" in artifacts


def test_optional_fields_default_cleanly():
    env = _envelope()
    for key in ("remote", "base_branch"):
        del env["target"][key]
    for key in ("context", "approach", "artifacts", "implementation_intent"):
        del env["handoff"][key]
    del env["source"]["evidence"]

    args = pe.map_envelope(env)

    assert args["remote"] is None
    assert args["base_branch"] is None
    assert args["context"] is None
    assert args["implementation_intent"] is None
    assert "Evidence:" not in args["artifacts"]


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
