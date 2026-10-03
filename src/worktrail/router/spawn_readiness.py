"""Spawn-readiness probe for a resolved routing table.

`readiness_problems()` answers the one question `worktrail-routing --check` and
the unattended drain both need before anything is launched: *for this resolved
routing table, in this environment, would every declared cell really spawn?*

The input is `resolve_routing(load_policy(...))`'s output and nothing else: this
module takes no path parameter and opens no file of its own, so it can only ever
judge the table a spawn will actually be served from. Re-deriving readiness from
the raw routing file re-opens the hole this closes -- a check that validated the
file's declared keys reported `ok` for cells the resolver had already dropped
(the `env_profiles` incident, #1380).

Per `(row, target)` cell the probe reports, in this order:

  * a cell whose target `routing.targets` does not declare (nothing launches);
  * a harness outside `spawnlib.SUPPORTED_AGENTS` (no launcher exists);
  * an `api`-pool target with no `api_opt_in` (the selector skips it as
    ineligible, so no run can ever reach it);
  * anything the spawn path's own builders refuse -- `build_cmd` for the argv,
    the codex `api` home validation (`spawnlib.codex_api_home`), and
    `build_child_env` against the resolved `env_profiles` and the environment
    the checking process would spawn with. A cell is ready only when the same
    builders a real spawn calls accept it.

Nothing is launched and nothing is created: the probe deliberately does not call
`_prepare_child_env()`, whose codex home / opencode data dir are spawn-time side
effects that would make the next spawn's result differ from the one predicted.
It records no `agent_capacity` gate either -- a recorded gate is skipped
*silently* by the next select, which would turn a loud configuration error into
an invisible fallback to the next rung.

`spawnlib` is imported inside the function: it imports `router.routing_cli` at
module level and `routing_cli` is this module's caller, so a module-level edge
back to it would be an import cycle.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from ..runtime.selection import Cell

__all__ = ["readiness_problems"]


def readiness_problems(
    routing: Mapping[str, Any],
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[tuple[str, str], str]:
    """Every declared `routing.tiers` cell that cannot spawn, keyed by
    `(row, target)` and valued with the operator-facing reason.

    `routing` is `resolve_routing()`'s return value -- the resolved table, never
    the raw file -- and `base_env` is the environment a spawn would be launched
    with (defaults to this process's, since every worker env is built from
    `{**os.environ, ...}` of the spawning process). Tests inject `base_env` to
    prove the environment-sensitivity deliberately.

    A ready table (and a table that declares no cells at all) returns `{}`.
    Never raises for a table-shaped problem: each unready cell is one entry, so
    a caller can report all of them at once instead of failing on the first.
    """
    from ..orchestrator import spawnlib

    env = dict(base_env) if base_env is not None else dict(os.environ)
    targets = routing.get("targets") or {}
    tiers = routing.get("tiers") or {}
    env_profiles = routing.get("env_profiles") or {}

    problems: dict[tuple[str, str], str] = {}
    for row in sorted(tiers):
        cells = tiers[row]
        if not isinstance(cells, Mapping):
            continue
        for target in sorted(cells):
            cell_def = cells[target]
            if not isinstance(cell_def, Mapping):
                continue
            declared = targets.get(target)
            if not isinstance(declared, Mapping):
                problems[(row, target)] = (
                    f"routing.targets declares no target {target!r} -- "
                    "nothing can launch this cell"
                )
                continue
            harness = declared.get("harness")
            if harness not in spawnlib.SUPPORTED_AGENTS:
                problems[(row, target)] = (
                    f"target {target!r} names harness {harness!r}, which the spawn "
                    "path cannot launch (supported: "
                    f"{', '.join(sorted(spawnlib.SUPPORTED_AGENTS))})"
                )
                continue
            if declared.get("pool") == "api" and not declared.get("api_opt_in"):
                problems[(row, target)] = (
                    f"target {target!r} draws from the 'api' pool without "
                    "`api_opt_in: true` -- the selector skips it as ineligible, "
                    "so no run can reach this cell"
                )
                continue
            cell = Cell(
                target=target,
                harness=harness,
                model=cell_def.get("model"),
                effort=cell_def.get("effort"),
                pool=declared.get("pool"),
                auth=declared.get("auth"),
            )
            try:
                # The same builders a real spawn runs, in the same order, so a
                # cell is reported ready only if the spawn path would accept
                # it. The prompt is unused (nothing is launched); codex's argv
                # resolves its sandbox roots from the process cwd because the
                # probe takes no path parameter by design.
                spawnlib.build_cmd("", cell)
                spawnlib.codex_api_home(cell)
                spawnlib.build_child_env(cell, env, env_profiles=env_profiles)
            except ValueError as exc:
                # OperatorConfigError is a ValueError; so is build_cmd's
                # unsupported-agent and codex_sandbox's bad-mode ValueError.
                # Every one of them is a condition a spawn would die on.
                problems[(row, target)] = str(exc)
    return problems
