from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from worktrail.runtime.selection import (
    InvalidCandidate,
    NoExecutionTarget,
    select_execution_target,
)


CATALOG = [
    {"provider": "claude", "model": "sonnet", "purposes": ["implementation"]},
    {"provider": "claude", "model": "haiku", "purposes": ["classification", "implementation"]},
    {"provider": "opencode", "model": "zen/free", "purposes": ["classification"]},
    {"provider": "codex", "model": "gpt-5", "purposes": ["implementation"]},
]


@dataclass(frozen=True)
class Context:
    agent_cli: str


def test_explicit_override_wins_over_inherited_and_policy():
    target = select_execution_target(
        CATALOG,
        explicit_provider="codex",
        explicit_model="gpt-5",
        invocation_context=Context("claude"),
        subcall=True,
        purpose="implementation",
        policy={"defaults": ["opencode:zen/free"]},
    )
    assert (target.provider, target.model, target.reason) == (
        "codex", "gpt-5", "explicit override")


def test_subcall_inherits_provider_before_routing_policy():
    target = select_execution_target(
        CATALOG,
        invocation_context=Context("claude"),
        subcall=True,
        purpose="implementation",
        policy={"defaults": ["codex:gpt-5"]},
    )
    assert (target.provider, target.model) == ("claude", "sonnet")
    assert target.agent_cli == "claude"
    assert target.reason == "inherited invocation context"


def test_originating_call_uses_purpose_policy_and_model_can_drive_provider():
    target = select_execution_target(
        CATALOG,
        purpose="classification",
        policy={"purpose_targets": {"classification": [
            {"model": "zen/free"}, "claude:haiku"
        ]}},
    )
    assert (target.provider, target.model) == ("opencode", "zen/free")
    assert target.reason == "purpose/tier policy"


def test_same_provider_alternate_model_precedes_next_provider():
    calls = []

    def capacity(provider, model, **_):
        calls.append((provider, model))
        return {"available": model != "sonnet", "source": "test"}

    target = select_execution_target(
        CATALOG,
        explicit_provider="claude",
        purpose="implementation",
        policy={"fallbacks": ["codex:gpt-5"]},
        capacity=capacity,
    )
    assert calls == [("claude", "sonnet"), ("claude", "haiku")]
    assert (target.provider, target.model) == ("claude", "haiku")


def test_capacity_reader_honors_ttl_at_injected_time():
    retry_after = datetime(2026, 8, 26, 17, tzinfo=timezone.utc)

    class Capacity:
        def check(self, provider, model, *, now):
            gated = provider == "claude" and now < retry_after
            return {"available": not gated, "retry_after": retry_after.isoformat()}

    policy = {"defaults": ["claude:sonnet", "codex:gpt-5"]}
    gated = select_execution_target(
        CATALOG, purpose="implementation", policy=policy, capacity=Capacity(),
        now=retry_after - timedelta(seconds=1),
    )
    assert (gated.provider, gated.model) == ("codex", "gpt-5")

    expired = select_execution_target(
        CATALOG, purpose="implementation", policy=policy, capacity=Capacity(),
        now=retry_after,
    )
    assert (expired.provider, expired.model) == ("claude", "sonnet")


def test_unsupported_explicit_values_fail_instead_of_silent_fallback():
    with pytest.raises(InvalidCandidate, match="unsupported explicit target"):
        select_execution_target(CATALOG, explicit_provider="codex", explicit_model="sonnet")


def test_disabled_candidates_and_all_capacity_gates_are_reported():
    catalog = [
        {"provider": "claude", "model": "old", "enabled": False},
        {"provider": "codex", "model": "gpt-5"},
    ]
    with pytest.raises(NoExecutionTarget) as caught:
        select_execution_target(catalog, capacity=lambda *_args, **_kwargs: False)
    assert [(p, m) for p, m, _ in caught.value.attempted] == [("codex", "gpt-5")]


def test_duck_typed_catalog_preserves_declared_order():
    @dataclass(frozen=True)
    class Model:
        provider: str
        model: str
        enabled: bool = True
        purposes: tuple[str, ...] = ()

    class Catalog:
        def candidates(self, enabled_only=True):
            assert enabled_only is True
            return (Model("codex", "small"), Model("claude", "haiku"))

    target = select_execution_target(Catalog())
    assert (target.provider, target.model) == ("codex", "small")


def test_discovery_unavailable_candidate_is_skipped_without_reordering_intent():
    catalog = [
        {"provider": "claude", "model": "sonnet", "observed": {"available": False}},
        {"provider": "claude", "model": "haiku", "observed": {"available": True}},
    ]
    target = select_execution_target(catalog)
    assert (target.provider, target.model) == ("claude", "haiku")
