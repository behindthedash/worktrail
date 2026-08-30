"""Pure, deterministic provider/model target selection.

The selector deliberately knows nothing about subprocesses or configuration
files.  Callers pass a catalog and a capacity reader, which keeps selection
usable by the CLI, drain, and tests without importing provider adapters.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any


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
    """Every compatible configured candidate/cell is capacity gated.

    ``attempted`` is either the legacy ``(provider, model, evidence)`` triples
    used by :func:`select_execution_target`, or the ``(target, harness,
    model, evidence)`` quadruples used by :func:`select_cell` -- the message
    is shaped from whichever arity was passed so ``select_cell``'s callers
    get each cell's gate class and retry time without breaking the older,
    shorter message existing callers already match on.
    """

    def __init__(self, attempted: Sequence[tuple]):
        self.attempted = tuple(attempted)
        if not self.attempted:
            super().__init__(
                "no supported execution target has capacity (attempted: none)"
            )
            return
        if len(self.attempted[0]) == 4:
            parts = []
            for target, harness, model, evidence in self.attempted:
                detail = f"{target} ({harness}:{model})"
                gate_class = (
                    _value(evidence, "failure_class")
                    if isinstance(evidence, Mapping)
                    else None
                )
                retry_at = (
                    _value(evidence, "retry_after")
                    if isinstance(evidence, Mapping)
                    else None
                )
                if gate_class:
                    detail += f" [{gate_class}"
                    if retry_at:
                        detail += f", retry at {retry_at}"
                    detail += "]"
                parts.append(detail)
            super().__init__(
                f"no execution cell has capacity (attempted: {'; '.join(parts)})"
            )
        else:
            labels = ", ".join(f"{p}:{m}" for p, m, _ in self.attempted) or "none"
            super().__init__(
                f"no supported execution target has capacity (attempted: {labels})"
            )


def _value(item: Any, name: str, default: Any = None) -> Any:
    return (
        item.get(name, default)
        if isinstance(item, Mapping)
        else getattr(item, name, default)
    )


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
        result.append(
            Candidate(
                *key,
                enabled=bool(_value(raw, "enabled", _value(raw, "configured", True))),
                purposes=_strings(_value(raw, "purposes")),
                tiers=_strings(_value(raw, "tiers")),
                observed_available=_value(
                    raw,
                    "observed_available",
                    _value(_value(raw, "observed", {}), "available"),
                ),
            )
        )
    return tuple(result)


def _target(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, str):
        return tuple(value.split(":", 1)) if ":" in value else (value, None)
    return (
        _value(value, "provider", _value(value, "agent_cli")),
        _value(value, "model"),
    )


def _policy_values(
    policy: Mapping[str, Any], purpose: str | None, tier: str | None
) -> list[tuple[Any, str]]:
    effective_tier = tier
    if effective_tier is None and purpose:
        effective_tier = (
            policy.get("purpose_tiers") or policy.get("purposes") or {}
        ).get(purpose)
    values: list[Any] = []
    if purpose:
        direct = (policy.get("purpose_targets") or {}).get(purpose)
        if direct:
            values.extend(
                (item, "purpose/tier policy")
                for item in (direct if isinstance(direct, list) else [direct])
            )
    if effective_tier:
        tier_value = (policy.get("tiers") or {}).get(effective_tier)
        if isinstance(tier_value, Mapping):
            tier_value = (
                tier_value.get("candidates") or tier_value.get("targets") or tier_value
            )
        if tier_value:
            values.extend(
                (item, "purpose/tier policy")
                for item in (
                    tier_value if isinstance(tier_value, list) else [tier_value]
                )
            )
    for key in ("defaults", "fallbacks", "fallback_chain"):
        value = policy.get(key)
        if value:
            values.extend(
                (item, "configured default/fallback")
                for item in (value if isinstance(value, list) else [value])
            )
    return values


def _capacity(
    capacity: Any, provider: str, model: str, now: datetime | None
) -> tuple[bool, Any]:
    if capacity is None:
        return True, None
    check = capacity if callable(capacity) else capacity.check
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
    explicit_provider: str | None = None,
    explicit_model: str | None = None,
    invocation_context: Any = None,
    subcall: bool = False,
    purpose: str | None = None,
    tier: str | None = None,
    policy: Mapping[str, Any] | None = None,
    capacity: Any = None,
    now: datetime | None = None,
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
    if (
        explicit_provider
        and explicit_model
        and (explicit_provider, explicit_model) not in by_key
    ):
        raise InvalidCandidate(
            f"unsupported explicit target: {explicit_provider}:{explicit_model}"
        )

    seeds: list[tuple[str | None, str | None, str]] = []
    if explicit_provider or explicit_model:
        seeds.append((explicit_provider, explicit_model, "explicit override"))
    if subcall and invocation_context is not None:
        inherited_provider = _value(
            invocation_context, "provider", _value(invocation_context, "agent_cli")
        )
        inherited_model = _value(invocation_context, "model")
        if inherited_provider:
            seeds.append(
                (inherited_provider, inherited_model, "inherited invocation context")
            )
    for value, reason in _policy_values(policy or {}, purpose, tier):
        provider, model = _target(value)
        seeds.append((provider, model, reason))
    seeds.extend(
        (item.provider, item.model, "configured default/fallback")
        for item in candidates
    )

    ordered: list[tuple[Candidate, str]] = []
    seen: set[tuple[str, str]] = set()
    for provider, model, reason in seeds:
        matching = [
            item
            for item in candidates
            if (provider is None or item.provider == provider)
            and (model is None or item.model == model)
        ]
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


@dataclass(frozen=True)
class Cell:
    """One resolved routing cell: a target, the harness that runs it, and
    the model/effort/pool/auth that harness spawns with (design D3)."""

    target: str
    harness: str
    model: str
    effort: str | None
    pool: str
    auth: Any = None


def select_cell(
    routing: Mapping[str, Any],
    tier: str,
    *,
    prefer: str | None = None,
    exclude_harness: str | None = None,
    capacity: Any = None,
    now: datetime | None = None,
) -> Cell:
    """Walk a `routing.tiers` row across its declared `routing.targets` in
    preference order, returning the first cell with capacity (design D3).

    ``routing`` is `resolve_routing()`'s return value: `{targets, tiers,
    roles, purposes, default_tier, drain}`. Pure and deterministic beyond
    ``capacity``/``now``, which callers inject (D3: "Pure, clock/capacity
    injected, deterministic").

    Steps (D3):
      1. order = targets in file order; ``prefer`` (if it names a target with
         a cell in this row) moves to the front.
      2. drop `api`-pool targets lacking `api_opt_in`; drop targets with no
         cell in this row.
      3. if `exclude_harness` is set, partition: other harnesses first, the
         excluded harness last (soft exclusion).
      4. the first cell whose `(target, model)` is not capacity-gated wins.
      5. none available -> `NoExecutionTarget` listing every cell attempted
         with its gate class and retry time.
    """
    targets: Mapping[str, Any] = routing.get("targets") or {}
    row: Mapping[str, Any] = (routing.get("tiers") or {}).get(tier) or {}

    order = list(targets.keys())
    if prefer and prefer in order and prefer in row:
        order.remove(prefer)
        order.insert(0, prefer)

    candidates: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    for name in order:
        target = targets.get(name)
        if not isinstance(target, Mapping):
            continue
        if target.get("pool") == "api" and not target.get("api_opt_in"):
            continue
        cell = row.get(name)
        if not cell:
            continue
        candidates.append((name, target, cell))

    if exclude_harness:
        other = [
            item for item in candidates if item[1].get("harness") != exclude_harness
        ]
        excluded = [
            item for item in candidates if item[1].get("harness") == exclude_harness
        ]
        candidates = other + excluded

    attempted: list[tuple[str, str, str, Any]] = []
    for name, target, cell in candidates:
        harness = target.get("harness")
        model = cell.get("model")
        available, evidence = _capacity(capacity, name, model, now)
        attempted.append((name, harness, model, evidence))
        if available:
            return Cell(
                target=name,
                harness=harness,
                model=model,
                effort=cell.get("effort"),
                pool=target.get("pool"),
                auth=target.get("auth"),
            )
    raise NoExecutionTarget(attempted)
