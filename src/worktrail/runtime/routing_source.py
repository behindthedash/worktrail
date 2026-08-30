"""Derive a `runtime.selection`-compatible candidate catalog from routing.yaml.

`routing_candidates()` is what task 4.3 uses to replace `drain.py`'s synthetic
`"configured-default"` sentinel catalog: everything it yields comes from
`routing["targets"]`/`routing["tiers"]` -- the same dict shape `resolve_routing()`
already returns (see `router.policy._validate_routing`, `resolve_routing()`) --
so capacity gating keys on the target/model actually spawned instead of a
sentinel that could not distinguish one model from another under the same
harness.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any


def routing_candidates(routing: Mapping[str, Any] | None) -> Iterator[dict[str, Any]]:
    """Yield `{target, harness, model, tiers, purposes}` candidates from
    `routing["targets"]` and `routing["tiers"]`.

    One candidate per distinct `(target, model)` pair actually configured:
    every `routing.tiers.<row>.<target>` cell, tagged with the harness
    declared on that target (`routing.targets.<target>.harness`) and the
    tier row(s) it appears under. A cell naming a target absent from
    `routing["targets"]` is skipped -- `resolve_routing()` never produces
    that (`_validate_routing_tiers()` already drops undeclared targets), but
    this stays defensive rather than raising on a malformed input.

    `purposes` lists every `routing.purposes` entry (`{purpose: tier}`)
    whose mapped tier matches one of a candidate's tiers.

    Pure: no I/O, no clock, same input always produces the same output
    (REQ-NR002), matching `resolve_routing()`. `routing` falsy (`None` or
    `{}`, i.e. no routing.yaml/`routing:` block configured) yields nothing.
    """
    if not routing:
        return
    targets: Mapping[str, Any] = routing.get("targets") or {}
    tiers: Mapping[str, Any] = routing.get("tiers") or {}
    purposes: Mapping[str, str] = routing.get("purposes") or {}

    seen: dict[tuple[str, str], dict[str, Any]] = {}

    def candidate(target: str, harness: str, model: str) -> dict[str, Any]:
        key = (target, model)
        entry = seen.get(key)
        if entry is None:
            entry = {
                "target": target,
                "harness": harness,
                "model": model,
                "tiers": set(),
                "purposes": set(),
            }
            seen[key] = entry
        return entry

    for tier_name, row in tiers.items():
        if not isinstance(row, Mapping):
            continue
        for target_name, cell in row.items():
            target = targets.get(target_name)
            if not isinstance(target, Mapping) or not isinstance(cell, Mapping):
                continue
            harness = target.get("harness")
            model = cell.get("model")
            if not harness or not model:
                continue
            candidate(target_name, harness, model)["tiers"].add(tier_name)

    for purpose, tier_name in purposes.items():
        for entry in seen.values():
            if tier_name in entry["tiers"]:
                entry["purposes"].add(purpose)

    for entry in seen.values():
        yield {
            "target": entry["target"],
            "harness": entry["harness"],
            "model": entry["model"],
            "tiers": tuple(sorted(entry["tiers"])),
            "purposes": tuple(sorted(entry["purposes"])),
        }
