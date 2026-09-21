#!/usr/bin/env python3
"""Tests for policy.py's coarse `POLICY_KEY_TYPES` sweep."""

from pathlib import Path

import pytest

from worktrail.router.policy import (
    _SWEEP_SKIP,
    _TYPE_SWEEP_EXEMPT,
    DEFAULTS,
    POLICY_KEY_TYPES,
    automerge_eligible,
    load_policy,
)


@pytest.fixture(autouse=True)
def _no_machine_wide_routing_file(monkeypatch):
    """Never read the developer's real machine-wide routing file."""
    monkeypatch.setenv(
        "GO_ROUTING_FILE", "/nonexistent/worktrail-key-types-test/routing.yaml"
    )


def _repo_with(tmp_path: Path, policy_text: str) -> Path:
    d = tmp_path / ".worktrail"
    d.mkdir(parents=True, exist_ok=True)
    (d / "policy.yaml").write_text(policy_text, encoding="utf-8")
    return tmp_path


def _warnings(policy) -> list[str]:
    return policy["_meta"]["warnings"]


def test_string_under_protected_paths_falls_back_to_empty_list(tmp_path):
    policy = load_policy(_repo_with(tmp_path, "protected_paths: migrations/\n"))
    assert policy["protected_paths"] == []
    hits = [w for w in _warnings(policy) if w.startswith("protected_paths must be")]
    assert len(hits) == 1
    assert "list" in hits[0]
    assert "'migrations/'" in hits[0]


def test_boolean_under_pre_pr_cmd_falls_back_to_none(tmp_path):
    policy = load_policy(_repo_with(tmp_path, "pre_pr_cmd: true\n"))
    assert policy["pre_pr_cmd"] is None
    hits = [w for w in _warnings(policy) if w.startswith("pre_pr_cmd must be")]
    assert len(hits) == 1
    assert "str or null" in hits[0]
    assert "True" in hits[0]


def test_mistyped_automerge_target_branches_falls_back_to_empty_list(tmp_path):
    policy = load_policy(
        _repo_with(tmp_path, "automerge:\n  enabled: true\n  target_branches: 7\n")
    )
    assert policy["automerge"]["target_branches"] == []
    hits = [
        w
        for w in _warnings(policy)
        if w.startswith("automerge.target_branches must be")
    ]
    assert len(hits) == 1
    assert "7" in hits[0]


def test_scalar_automerge_target_branches_is_left_to_its_per_key_layer(tmp_path):
    """`automerge_eligible()` normalizes a bare string to a single branch."""
    policy = load_policy(
        _repo_with(tmp_path, "automerge:\n  enabled: true\n  target_branches: dev\n")
    )
    assert policy["automerge"]["target_branches"] == "dev"
    assert [w for w in _warnings(policy) if "using default" in w] == []


def test_bare_string_require_human_routes_keeps_gating(tmp_path):
    """`automerge_eligible()` tests `route in require_human_routes`, so a bare
    single-route string is a working gate and must not be swept to `[]`."""
    policy = load_policy(
        _repo_with(
            tmp_path,
            "require_human_routes: B\nautomerge:\n  enabled: true\n  max_risk: medium\n",
        )
    )
    assert policy["require_human_routes"] == "B"
    assert [w for w in _warnings(policy) if "using default" in w] == []
    eligible, reason = automerge_eligible(policy, "low", [], "main", route="B")
    assert eligible is False
    assert "require_human_routes" in reason


def test_correctly_typed_policy_emits_no_type_warning(tmp_path):
    policy = load_policy(
        _repo_with(
            tmp_path,
            "base_branch: main\n"
            "pre_pr_cmd: pytest -q\n"
            "max_workers: 4\n"
            "protected_paths: [migrations/]\n"
            "agent_learning: true\n"
            "automerge:\n  enabled: true\n  max_risk: low\n  target_branches: [dev]\n",
        )
    )
    assert [w for w in _warnings(policy) if "using default" in w] == []
    assert policy["max_workers"] == 4
    assert policy["protected_paths"] == ["migrations/"]
    assert policy["automerge"]["target_branches"] == ["dev"]


def test_existing_per_key_checks_are_unchanged(tmp_path):
    policy = load_policy(
        _repo_with(
            tmp_path,
            "max_workers: 0\nautomerge:\n  enabled: true\n  max_risk: critical\n",
        )
    )
    warnings = _warnings(policy)
    assert any("automerge.max_risk 'critical' invalid" in w for w in warnings)
    assert policy["automerge"]["max_risk"] == "low"
    assert any("max_workers must be an integer >= 1" in w for w in warnings)
    assert policy["max_workers"] is None
    # The coarse sweep must not double-report a correctly-typed value.
    assert [w for w in warnings if "using default" in w] == []


def test_every_defaults_key_has_a_declared_type_or_is_exempt():
    assert set(DEFAULTS) == set(POLICY_KEY_TYPES) | _TYPE_SWEEP_EXEMPT
    assert set(POLICY_KEY_TYPES) & _TYPE_SWEEP_EXEMPT == set()


def test_per_key_validated_keys_are_declared_but_not_swept():
    assert _SWEEP_SKIP <= set(POLICY_KEY_TYPES)


def test_integrate_smoke_retries_keeps_its_own_warning(tmp_path):
    policy = load_policy(_repo_with(tmp_path, "integrate_smoke_retries: true\n"))
    assert policy["integrate_smoke_retries"] == 0
    warnings = _warnings(policy)
    assert any(
        "integrate_smoke_retries must be an integer >= 0; dropped" in w
        for w in warnings
    )
    assert [w for w in warnings if "using default" in w] == []
