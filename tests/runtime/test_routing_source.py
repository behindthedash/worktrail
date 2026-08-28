"""Tests for `routing_candidates()`. Run: python3 -m pytest tests/runtime/test_routing_source.py"""

from worktrail.runtime.routing_source import routing_candidates


def _routing(targets, tiers, purposes=None):
    return {"targets": targets, "tiers": tiers, "roles": {}, "purposes": purposes or {},
            "default_tier": None, "drain": {}}


def _target(harness, pool="subscription", api_opt_in=False, auth=None):
    return {"harness": harness, "pool": pool, "api_opt_in": api_opt_in, "auth": auth}


def _cell(model, effort=None):
    return {"model": model, "effort": effort}


def test_none_routing_yields_nothing():
    assert list(routing_candidates(None)) == []


def test_empty_routing_yields_nothing():
    assert list(routing_candidates({})) == []


def test_yields_one_candidate_per_target_model_with_harness_from_targets():
    routing = _routing(
        targets={"claude-sub": _target("claude"), "codex-sub": _target("codex")},
        tiers={"t1-deep": {
            "claude-sub": _cell("opus"),
            "codex-sub": _cell("gpt-5"),
        }},
    )
    candidates = list(routing_candidates(routing))
    assert {"target": "claude-sub", "harness": "claude", "model": "opus",
            "tiers": ("t1-deep",), "purposes": ()} in candidates
    assert {"target": "codex-sub", "harness": "codex", "model": "gpt-5",
            "tiers": ("t1-deep",), "purposes": ()} in candidates
    assert len(candidates) == 2


def test_target_appearing_in_multiple_tier_rows_with_same_model_merges_tiers():
    routing = _routing(
        targets={"claude-sub": _target("claude")},
        tiers={
            "t1-deep": {"claude-sub": _cell("opus")},
            "t2-build": {"claude-sub": _cell("opus")},
        },
    )
    candidates = list(routing_candidates(routing))
    assert len(candidates) == 1
    assert candidates[0]["target"] == "claude-sub"
    assert candidates[0]["tiers"] == ("t1-deep", "t2-build")


def test_target_with_different_models_across_rows_yields_distinct_candidates():
    routing = _routing(
        targets={"claude-sub": _target("claude")},
        tiers={
            "t1-deep": {"claude-sub": _cell("opus")},
            "t2-build": {"claude-sub": _cell("sonnet")},
        },
    )
    candidates = list(routing_candidates(routing))
    assert len(candidates) == 2
    models = {c["model"]: c["tiers"] for c in candidates}
    assert models == {"opus": ("t1-deep",), "sonnet": ("t2-build",)}


def test_purposes_mapped_from_matching_tier():
    routing = _routing(
        targets={"claude-sub": _target("claude")},
        tiers={"t1-deep": {"claude-sub": _cell("opus")}},
        purposes={"review": "t1-deep", "frontend": "t2-build"},
    )
    candidates = list(routing_candidates(routing))
    assert len(candidates) == 1
    assert candidates[0]["purposes"] == ("review",)


def test_cell_naming_undeclared_target_is_skipped():
    routing = _routing(
        targets={"claude-sub": _target("claude")},
        tiers={"t1-deep": {
            "claude-sub": _cell("opus"),
            "ghost-target": _cell("mystery"),
        }},
    )
    candidates = list(routing_candidates(routing))
    assert len(candidates) == 1
    assert candidates[0]["target"] == "claude-sub"


def test_no_agents_key_used_or_required():
    routing = {"targets": {"claude-sub": _target("claude")},
               "tiers": {"t1-deep": {"claude-sub": _cell("opus")}},
               "roles": {}, "purposes": {}, "default_tier": None, "drain": {}}
    assert "agents" not in routing
    candidates = list(routing_candidates(routing))
    assert len(candidates) == 1


def test_same_input_produces_same_output_deterministically():
    routing = _routing(
        targets={"claude-sub": _target("claude"), "codex-sub": _target("codex")},
        tiers={"t1-deep": {
            "claude-sub": _cell("opus"),
            "codex-sub": _cell("gpt-5"),
        }},
    )
    first = list(routing_candidates(routing))
    second = list(routing_candidates(routing))
    assert first == second
