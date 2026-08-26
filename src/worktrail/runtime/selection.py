"""Pure, deterministic provider/model target selection.

The selector deliberately knows nothing about subprocesses or configuration
files.  Callers pass a catalog and a capacity reader, which keeps selection
usable by the CLI, drain, and tests without importing provider adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class ExecutionTarget:
    """A fully resolved launch target and the evidence used to choose it."""

    provider: str
    model: str
    reason: str
    capacity_evidence: Any = None

    @property
    def agent_cli(self) -> str:
        """Compatibility spelling used by the existing orchestrator."""
        return self.provider


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    enabled: bool = True
    purposes: tuple[str, ...] = ()
    tiers: tuple[str, ...] = ()
    observed_available: bool | None = None


class SelectionError(ValueError):
    """Base class for deterministic selection failures."""


class InvalidCandidate(SelectionError):
    """An explicit provider/model override is not in the supported catalog."""


class NoExecutionTarget(SelectionError):
    """Every compatible configured candidate is capacity gated."""

    def __init__(self, attempted: Sequence[tuple[str, str, Any]]):
        self.attempted = tuple(attempted)
        labels = ", ".join(f"{p}:{m}" for p, m, _ in attempted) or "none"
        super().__init__(f"no supported execution target has capacity (attempted: {labels})")


def _value(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _catalog_items(catalog: Any) -> Iterable[Any]:
    if isinstance(catalog, Mapping):
        # Accept either {provider: [models]} or {"candidates": [...]}.
        if "candidates" in catalog:
            return catalog["candidates"]
        return (
            {"provider": provider, "model": model}
            for provider, models in catalog.items()
            for model in models
        )
    for method in ("ordered_candidates", "candidates", "iter_candidates"):
        member = getattr(catalog, method, None)
        if member is not None:
            return member() if callable(member) else member
    return catalog


def catalog_candidates(catalog: Any) -> tuple[Candidate, ...]:
    """Normalize a catalog while retaining its declared order."""
    result: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for raw in _catalog_items(catalog):
        provider = _value(raw, "provider", _value(raw, "agent_cli"))
        model = _value(raw, "model", _value(raw, "name"))
        if not provider or not model:
            continue
        key = (str(provider), str(model))
        if key in seen:
            continue
        seen.add(key)
        result.append(Candidate(
            *key,
            enabled=bool(_value(raw, "enabled", _value(raw, "configured", True))),
            purposes=_strings(_value(raw, "purposes")),
            tiers=_strings(_value(raw, "tiers")),
            observed_available=_value(
                raw,
                "observed_available",
                _value(_value(raw, "observed", {}), "available"),
            ),
        ))
    return tuple(result)


def _target(value: Any) -> tuple[Optional[str], Optional[str]]:
    if isinstance(value, str):
        return tuple(value.split(":", 1)) if ":" in value else (value, None)
    return (_value(value, "provider", _value(value, "agent_cli")), _value(value, "model"))


def _policy_values(
    policy: Mapping[str, Any], purpose: Optional[str], tier: Optional[str]
) -> list[tuple[Any, str]]:
    effective_tier = tier
    if effective_tier is None and purpose:
        effective_tier = (policy.get("purpose_tiers") or policy.get("purposes") or {}).get(purpose)
    values: list[Any] = []
    if purpose:
        direct = (policy.get("purpose_targets") or {}).get(purpose)
        if direct:
            values.extend((item, "purpose/tier policy") for item in
                          (direct if isinstance(direct, list) else [direct]))
    if effective_tier:
        tier_value = (policy.get("tiers") or {}).get(effective_tier)
        if isinstance(tier_value, Mapping):
            tier_value = tier_value.get("candidates") or tier_value.get("targets") or tier_value
        if tier_value:
            values.extend((item, "purpose/tier policy") for item in
                          (tier_value if isinstance(tier_value, list) else [tier_value]))
    for key in ("defaults", "fallbacks", "fallback_chain"):
        value = policy.get(key)
        if value:
            values.extend((item, "configured default/fallback") for item in
                          (value if isinstance(value, list) else [value]))
    return values


def _capacity(capacity: Any, provider: str, model: str, now: Optional[datetime]) -> tuple[bool, Any]:
    if capacity is None:
        return True, None
    check = capacity if callable(capacity) else getattr(capacity, "check")
    try:
        result = check(provider, model, now=now)
    except TypeError:
        result = check(provider, model)
    except Exception as exc:
        # agent_capacity.check communicates an active gate by raising. Do not
        # couple this pure module to that exception class.
        return False, getattr(exc, "state", str(exc))
    if isinstance(result, Mapping):
        available = result.get("available")
        if available is None:
            available = not result.get("gated", False)
        return bool(available), result
    if isinstance(result, tuple) and len(result) == 2:
        return bool(result[0]), result[1]
    return (True if result is None else bool(result)), result


def select_execution_target(
    catalog: Any,
    *,
    explicit_provider: Optional[str] = None,
    explicit_model: Optional[str] = None,
    invocation_context: Any = None,
    subcall: bool = False,
    purpose: Optional[str] = None,
    tier: Optional[str] = None,
    policy: Optional[Mapping[str, Any]] = None,
    capacity: Any = None,
    now: Optional[datetime] = None,
) -> ExecutionTarget:
    """Resolve a target by precedence, compatibility, catalog order and capacity.

    Precedence is explicit override, inherited subcall context, purpose/tier
    policy, then configured catalog order.  Once a provider is preferred, its
    compatible alternate models are exhausted before advancing to a later
    provider.
    """
    candidates = tuple(item for item in catalog_candidates(catalog) if item.enabled)
    by_key = {(item.provider, item.model): item for item in candidates}
    providers = {item.provider for item in candidates}
    models = {item.model for item in candidates}
    if explicit_provider and explicit_provider not in providers:
        raise InvalidCandidate(f"unsupported explicit provider: {explicit_provider}")
    if explicit_model and explicit_model not in models:
        raise InvalidCandidate(f"unsupported explicit model: {explicit_model}")
    if explicit_provider and explicit_model and (explicit_provider, explicit_model) not in by_key:
        raise InvalidCandidate(f"unsupported explicit target: {explicit_provider}:{explicit_model}")

    seeds: list[tuple[Optional[str], Optional[str], str]] = []
    if explicit_provider or explicit_model:
        seeds.append((explicit_provider, explicit_model, "explicit override"))
    if subcall and invocation_context is not None:
        inherited_provider = _value(invocation_context, "provider", _value(invocation_context, "agent_cli"))
        inherited_model = _value(invocation_context, "model")
        if inherited_provider:
            seeds.append((inherited_provider, inherited_model, "inherited invocation context"))
    for value, reason in _policy_values(policy or {}, purpose, tier):
        provider, model = _target(value)
        seeds.append((provider, model, reason))
    seeds.extend((item.provider, item.model, "configured default/fallback") for item in candidates)

    ordered: list[tuple[Candidate, str]] = []
    seen: set[tuple[str, str]] = set()
    for provider, model, reason in seeds:
        matching = [item for item in candidates
                    if (provider is None or item.provider == provider)
                    and (model is None or item.model == model)]
        # A model-only seed intentionally chooses whichever provider declares
        # that model first. A provider seed expands to alternate models before
        # the next provider seed.
        for item in matching:
            key = (item.provider, item.model)
            if key not in seen:
                seen.add(key)
                ordered.append((item, reason))

    attempted: list[tuple[str, str, Any]] = []
    for item, reason in ordered:
        if item.observed_available is False:
            attempted.append((item.provider, item.model, {"catalog_available": False}))
            continue
        if purpose and item.purposes and purpose not in item.purposes:
            continue
        if tier and item.tiers and tier not in item.tiers:
            continue
        available, evidence = _capacity(capacity, item.provider, item.model, now)
        attempted.append((item.provider, item.model, evidence))
        if available:
            return ExecutionTarget(item.provider, item.model, reason, evidence)
    raise NoExecutionTarget(attempted)


# Concise public spelling for callers that already use ``resolve_*`` APIs.
resolve_execution_target = select_execution_target
