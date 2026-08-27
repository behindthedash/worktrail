"""Derive a `runtime.selection`-compatible candidate catalog from routing.yaml.

`routing_candidates()` is what task 4.3 uses to replace `drain.py`'s synthetic
`"configured-default"` sentinel catalog: everything it yields comes from
`routing["agents"]`/`routing["tiers"]` -- the same dict shape `policy["routing"]`
already holds (see `router.policy._validate_routing`, `resolve_routing()`,
`resolve_tier_map()`) -- so capacity gating keys on the model actually spawned
instead of a sentinel that could not distinguish one model from another under
the same provider.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping, Optional


def routing_candidates(routing: Optional[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield `{provider, model, tiers, purposes}` candidates from
    `routing["agents"]` and `routing["tiers"]`.

    One candidate per distinct `(agent, model)` pair actually configured:
    every `routing.agents.<agent>.default_model` entry, and every
    `routing.tiers` entry's `(agent_cli, agent_model)` (falling back to that
    agent's `routing.agents` default when a tier entry omits `agent_model`,
    same as `dispatch.agent_for`'s own resolution). Each tier-sourced
    candidate is tagged with the tier name recovered from its
    `(f"{tier}-{agent}", domain)` key (`_validate_routing_tiers()`'s
    composite form, shared by both the flat and nested `routing.tiers`
    shapes -- D6).

    `purposes` lists every `routing.purpose_tiers` purpose whose mapped tier
    matches one of a candidate's tiers.

    Pure: no I/O, no clock, same input always produces the same output
    (REQ-NR002), matching `resolve_routing()`/`resolve_tier_map()`. `routing`
    falsy (`None` or `{}`, i.e. no routing.yaml/`routing:` block configured)
    yields nothing, same as `resolve_tier_map()`'s `{}` return.
    """
    if not routing:
        return
    agents: Mapping[str, Any] = routing.get("agents") or {}
    tiers: Mapping[Any, Any] = routing.get("tiers") or {}
    purpose_tiers: Mapping[str, str] = routing.get("purpose_tiers") or {}

    seen: dict[tuple[str, str], dict[str, Any]] = {}

    def candidate(provider: str, model: str) -> dict[str, Any]:
        key = (provider, model)
        entry = seen.get(key)
        if entry is None:
            entry = {"provider": provider, "model": model, "tiers": set(), "purposes": set()}
            seen[key] = entry
        return entry

    for agent, entry in agents.items():
        model = entry.get("default_model") if isinstance(entry, Mapping) else None
        if agent and model:
            candidate(agent, model)

    for tier_key, entry in tiers.items():
        if not isinstance(entry, Mapping):
            continue
        agent_cli = entry.get("agent_cli")
        agent_model = entry.get("agent_model") or (agents.get(agent_cli) or {}).get("default_model")
        if not agent_cli or not agent_model:
            continue
        composite = tier_key[0] if isinstance(tier_key, tuple) else tier_key
        suffix = f"-{agent_cli}"
        tier_name = composite[: -len(suffix)] if composite.endswith(suffix) else composite
        candidate(agent_cli, agent_model)["tiers"].add(tier_name)

    for purpose, tier_name in purpose_tiers.items():
        for entry in seen.values():
            if tier_name in entry["tiers"]:
                entry["purposes"].add(purpose)

    for entry in seen.values():
        yield {
            "provider": entry["provider"],
            "model": entry["model"],
            "tiers": tuple(sorted(entry["tiers"])),
            "purposes": tuple(sorted(entry["purposes"])),
        }
