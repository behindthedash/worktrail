#!/usr/bin/env python3
"""worktrail-routing: bootstrap and inspect the machine-wide `routing.yaml`.

`--init` writes a starter file covering every top-level section
`_validate_routing()` understands (`agents`, `fallback`, `roles`,
`purpose_tiers`, `tiers`, `drain`) -- the fail-closed mitigation for D3
(consolidate-operator-config-into-routing): a machine with no routing file
cannot resolve a default model, and the error `default_model_for_agent()`
raises names this exact command.

`--show` prints the routing block `load_policy(repo)` resolved for a repo
(repo-local `routing:` block, else the machine-wide file, else null).

Usage: worktrail-routing --init [--force]
       worktrail-routing --show [--repo /path/to/repo]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from ..orchestrator import agent_capacity
from .policy import (
    EFFORT_VOCABULARY,
    VALID_AGENT_CLIS,
    OperatorConfigError,
    _json_safe,
    _load_yaml_mapping,
    _validate_routing,
    load_policy,
    resolved_routing_file_path,
)

STARTER_ROUTING_YAML = """\
# worktrail machine-wide routing config -- written by `worktrail-routing --init`.
# Governs which provider/model runs which work (targets/tiers/roles) and how
# unattended drain picks agents (drain.max_workers). Edit freely; re-run
# `worktrail-routing --init --force` only if you want to discard local edits
# and start over.
#
# Only subscription-pool targets are declared here, since a fresh machine has
# no way to know which opencode models this operator's plan actually serves --
# naming one risks a `model_unavailable` gate on the very first `--check`.
# Uncomment the opencode-free example below once you know a real model id
# (e.g. from `opencode models`).

# routing.targets: the harnesses this machine can spawn, and which capacity
# pool each draws from. `pool: subscription` targets are always eligible;
# `pool: free`/`api` targets are opencode/OpenRouter pools this starter
# leaves out until you opt in explicitly.
targets:
  claude-sub:
    harness: claude
    pool: subscription
  codex-sub:
    harness: codex
    pool: subscription
  # opencode-free:
  #   harness: opencode
  #   pool: free

# routing.tiers: a row per work tier, one cell per declared target above.
# Both subscription targets are filled for every row so neither is ever
# short a model/effort when the selector walks it.
tiers:
  t1-deep:
    claude-sub: {model: opus, effort: high}
    codex-sub: {model: gpt-5.6-sol, effort: high}
  t2-build:
    claude-sub: {model: sonnet, effort: medium}
    codex-sub: {model: gpt-5.6-terra, effort: medium}
  t3-bulk:
    claude-sub: {model: haiku, effort: medium}
    codex-sub: {model: gpt-5.6-terra, effort: low}
  t4-trivia:
    claude-sub: {model: haiku, effort: low}
    codex-sub: {model: gpt-5.6-luna, effort: minimal}

# routing.default_tier: the row used absent a more specific role/purpose/task
# tier (dispatch.tier_for()).
default_tier: t2-build

# routing.roles: review resolves to its own tier row, preferring the target
# named by `prefer` (independent: true means the selector avoids re-using
# the implementer's own harness when it can).
roles:
  review:
    tier: t1-deep
    prefer: codex-sub
    independent: true

# Optional purpose -> tier map, consulted ahead of task complexity to
# resolve a task's tier (dispatch.tier_for()). {} means purpose never
# overrides the complexity-derived tier.
purposes: {}

# Machine-wide drain (worktrail-drain) defaults: how many task worktrees
# with live agent workers run concurrently. Agent selection for drain now
# comes from targets/tiers/roles above, not a drain-specific agent list.
drain:
  max_workers: 2

# Run `worktrail-routing --check` after editing this file -- it validates
# every tiers cell against reality (gates a retired/unavailable opencode
# model, warns on a free-pool id missing -free/:free or an out-of-vocabulary
# effort) and prints a per-cell table.
"""


def list_opencode_models(runner=subprocess.run) -> set:
    """`opencode/*`, `openrouter/*`, `google/*` ids `opencode models` currently
    serves, one per stdout line -- the source of truth D7's `model_unavailable`
    gate (task 3.5) and `--check` (task 6.2) compare cells against. Fails open:
    a missing binary, timeout, or non-zero exit warns on stderr and returns an
    empty set rather than raising, since an unreachable listing must never be
    mistaken for "no models exist"."""
    try:
        result = runner(
            ["opencode", "models"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(
            f"worktrail-routing: could not list opencode models: {exc}", file=sys.stderr
        )
        return set()
    if result.returncode != 0:
        print(
            f"worktrail-routing: `opencode models` exited {result.returncode}",
            file=sys.stderr,
        )
        return set()
    return {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}


def _routing_file_path() -> Path:
    """Where `--init` writes and `load_policy()` looks: `policy.resolved_routing_file_path()`,
    the same override-aware resolution `_resolve_routing()` uses internally."""
    return resolved_routing_file_path()


def _init(force: bool) -> int:
    path = _routing_file_path()
    if path.exists() and not force:
        print(
            f"worktrail-routing: {path} already exists -- pass --force to overwrite",
            file=sys.stderr,
        )
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_ROUTING_YAML, encoding="utf-8")
    print(f"worktrail-routing: wrote starter routing config to {path}")
    print("worktrail-routing: run `worktrail-routing --check` to validate it")
    return 0


def _show(repo: Path) -> int:
    policy = load_policy(repo)
    print(json.dumps(_json_safe(policy.get("routing")), indent=2))
    return 0


def _check(
    path: Path | None = None,
    runner=subprocess.run,
    capacity_path: Path | None = None,
    now=None,
) -> int:
    """`worktrail-routing --check`: walk every declared `routing.tiers` cell
    and report whether it can actually serve.

    An opencode cell whose model id is absent from `list_opencode_models()`
    (task 6.1) is a *gate*: it is recorded as `model_unavailable` via
    `agent_capacity.record()` (task 3.1's `model_unavailable` cooldown) and
    fails the check (D7 -- a retired model gates its own cell with a
    distinct failure class). A `free`-pool opencode id lacking a `-free`/
    `:free` suffix, and an effort literal outside its target's harness's
    `EFFORT_VOCABULARY` (task 1.5), are warnings only -- they surface in the
    per-cell table and on stderr but never flip the exit code.
    """
    routing_path = path or _routing_file_path()
    if not routing_path.is_file():
        print(
            f"worktrail-routing: no routing file at {routing_path} -- run --init first",
            file=sys.stderr,
        )
        return 1
    raw = _load_yaml_mapping(routing_path.read_text(encoding="utf-8"))
    if raw is None:
        print(f"worktrail-routing: {routing_path} is malformed YAML", file=sys.stderr)
        return 1
    meta: dict = {"source": str(routing_path), "unknown_keys": [], "warnings": []}
    try:
        routing = _validate_routing(raw, meta)
    except OperatorConfigError as exc:
        print(f"worktrail-routing: {exc}", file=sys.stderr)
        return 1
    for warning in meta["warnings"]:
        print(f"worktrail-routing: warning: {warning}", file=sys.stderr)
    if not routing:
        print(
            f"worktrail-routing: {routing_path} declares no routing.targets/tiers",
            file=sys.stderr,
        )
        return 1

    targets = routing["targets"]
    tiers = routing["tiers"]
    opencode_models: set | None = None
    rows = []
    gated = 0

    for row in sorted(tiers):
        for target in sorted(tiers[row]):
            cell = tiers[row][target]
            target_info = targets.get(target, {})
            harness = target_info.get("harness")
            pool = target_info.get("pool")
            model = cell["model"]
            effort = cell.get("effort")
            notes = []
            status = "ok"

            if harness == "opencode":
                if opencode_models is None:
                    opencode_models = list_opencode_models(runner=runner)
                if model not in opencode_models:
                    agent_capacity.record(
                        target,
                        model,
                        outcome="unavailable",
                        failure_class="model_unavailable",
                        retry_after=agent_capacity.retry_time("model_unavailable", now),
                        source="worktrail-routing --check",
                        path=capacity_path,
                        now=now,
                    )
                    status = "GATED"
                    notes.append("model_unavailable")
                    gated += 1
                if pool == "free" and not (model.endswith(("-free", ":free"))):
                    notes.append("warn: free-pool id missing -free/:free suffix")

            if effort:
                vocabulary = EFFORT_VOCABULARY.get(harness)
                if vocabulary is None or effort not in vocabulary:
                    notes.append(
                        f"warn: effort {effort!r} outside {harness!r} vocabulary"
                    )

            rows.append(
                (
                    row,
                    target,
                    model,
                    effort or "-",
                    pool or "-",
                    harness or "-",
                    status,
                    "; ".join(notes) or "-",
                )
            )

    header = ("TIER", "TARGET", "MODEL", "EFFORT", "POOL", "HARNESS", "STATUS", "NOTES")
    widths = [
        max(len(header[i]), *(len(r[i]) for r in rows)) if rows else len(header[i])
        for i in range(len(header))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    for r in rows:
        print(fmt.format(*r))

    if gated:
        print(f"worktrail-routing: {gated} cell(s) gated -- see above", file=sys.stderr)
        return 1
    return 0


def _target_for_harness(harness: str) -> str:
    """`<harness>-sub` for a subscription-pool harness, `opencode-free` for
    opencode (design D9: opencode's only migrated pool is `free`)."""
    return "opencode-free" if harness == "opencode" else f"{harness}-sub"


def _migrate_targets(fallback_raw: Any) -> dict[str, dict]:
    """`routing.targets` from `routing.fallback`'s harness order (design D9):
    each harness becomes one `subscription` target (opencode: `free`), first
    occurrence wins, declaration order preserved."""
    targets: dict[str, dict] = {}
    if not isinstance(fallback_raw, list):
        return targets
    for entry in fallback_raw:
        if isinstance(entry, str):
            harness = entry
        elif isinstance(entry, dict):
            harness = entry.get("agent_cli") or entry.get("agent")
        else:
            continue
        if harness not in VALID_AGENT_CLIS:
            continue
        name = _target_for_harness(harness)
        if name in targets:
            continue
        targets[name] = {
            "harness": harness,
            "pool": "free" if harness == "opencode" else "subscription",
        }
    return targets


def _normalize_effort(harness: str, effort: str) -> str | None:
    """Clamp a legacy effort literal (e.g. `xhigh`) to `EFFORT_VOCABULARY[harness]`'s
    highest recognized value, so a migrated cell never reintroduces the exact
    out-of-vocabulary warning `--migrate` exists to clear. `opencode` has no
    effort vocabulary at all (`None`) -- there is nothing valid to clamp to,
    so its effort is dropped rather than kept out-of-vocabulary."""
    vocabulary = EFFORT_VOCABULARY.get(harness)
    if not vocabulary:
        return None
    if effort in vocabulary:
        return effort
    return vocabulary[-1]


def _migrate_tiers(
    tiers_raw: Any, harness_to_target: dict[str, str]
) -> dict[str, dict]:
    """Re-key every `routing.tiers.<row>` cell from a harness literal to its
    migrated target name; a harness with no migrated target (not present in
    `routing.fallback`) drops its cell. An effort outside that harness's
    `EFFORT_VOCABULARY` is clamped via `_normalize_effort` rather than copied
    verbatim."""
    tiers: dict[str, dict] = {}
    if not isinstance(tiers_raw, dict):
        return tiers
    for row, cells in tiers_raw.items():
        if not isinstance(cells, dict):
            continue
        row_out: dict[str, dict] = {}
        for harness, cell in cells.items():
            target = harness_to_target.get(harness)
            if target is None or not isinstance(cell, dict):
                continue
            cell_out = {"model": cell.get("model")}
            effort = cell.get("effort")
            if effort is not None:
                normalized = _normalize_effort(harness, effort)
                if normalized is not None:
                    cell_out["effort"] = normalized
            row_out[target] = cell_out
        if row_out:
            tiers[row] = row_out
    return tiers


def _migrate_default_tier(agents_raw: Any, tiers_raw: Any) -> str:
    """The row whose per-harness cell models match every `routing.agents.<x>.default_model`
    (design D9), else `t2-build`. Matched against the pre-migration, harness-keyed
    `routing.tiers` (the migrated one is already re-keyed by target)."""
    if (
        not isinstance(agents_raw, dict)
        or not agents_raw
        or not isinstance(tiers_raw, dict)
    ):
        return "t2-build"
    for row, cells in tiers_raw.items():
        if not isinstance(cells, dict):
            continue
        match = True
        for harness, entry in agents_raw.items():
            if not isinstance(entry, dict):
                continue
            default_model = entry.get("default_model")
            cell = cells.get(harness)
            if not isinstance(cell, dict) or cell.get("model") != default_model:
                match = False
                break
        if match:
            return row
    return "t2-build"


def _migrate_roles(
    roles_raw: Any, harness_to_target: dict[str, str], default_tier: str
) -> dict[str, dict]:
    """`roles.review` -> `{tier: t1-deep, prefer: <target of its agent_cli>,
    independent: true}` (design D9); any other legacy `{agent_cli, ...}` role
    migrates generically (its own tier defaults to `default_tier`, not
    independent) rather than being dropped silently. A role already in the
    current `{tier, prefer?, independent?}` shape passes through unchanged."""
    roles: dict[str, dict] = {}
    if not isinstance(roles_raw, dict):
        return roles
    for role, entry in roles_raw.items():
        if not isinstance(entry, dict):
            continue
        if "agent_cli" not in entry and "agent_model" not in entry:
            roles[role] = entry
            continue
        agent_cli = entry.get("agent_cli")
        prefer = harness_to_target.get(
            agent_cli, _target_for_harness(agent_cli) if agent_cli else None
        )
        if role == "review":
            roles[role] = {"tier": "t1-deep", "prefer": prefer, "independent": True}
        else:
            roles[role] = {"tier": default_tier, "prefer": prefer, "independent": False}
    return roles


def _migrate_routing_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Build the migrated `routing:` mapping per design D9 from a raw legacy
    mapping already confirmed (by the caller) to trip `_reject_legacy_routing_keys()`."""
    fallback_raw = raw.get("fallback")
    targets = _migrate_targets(fallback_raw)
    harness_to_target = {info["harness"]: name for name, info in targets.items()}
    tiers = _migrate_tiers(raw.get("tiers"), harness_to_target)
    default_tier = _migrate_default_tier(raw.get("agents"), raw.get("tiers"))
    roles = _migrate_roles(raw.get("roles"), harness_to_target, default_tier)
    migrated: dict[str, Any] = {"targets": targets}
    if "defaults" in raw:
        migrated["defaults"] = raw["defaults"]
    migrated["roles"] = roles
    purposes = raw.get("purposes")
    if purposes is None:
        purposes = raw.get("purpose_tiers")
    migrated["purposes"] = purposes or {}
    migrated["tiers"] = tiers
    migrated["default_tier"] = default_tier
    drain_raw = raw.get("drain")
    if isinstance(drain_raw, dict):
        migrated["drain"] = {"max_workers": drain_raw.get("max_workers", 2)}
    return migrated


def _migrate(path: Path | None = None) -> int:
    """`worktrail-routing --migrate`: rewrite a legacy `routing.yaml` into the
    current targets/tiers/roles schema (design D9), backing up the original
    to `<file>.bak` first. Refuses (exit 1, no write) when the file already
    loads cleanly under the current schema -- `_reject_legacy_routing_keys()`
    (via `_validate_routing()`) is the only signal that a rewrite is needed,
    since a clean file has nothing left to migrate.
    """
    routing_path = path or _routing_file_path()
    if not routing_path.is_file():
        print(f"worktrail-routing: no routing file at {routing_path}", file=sys.stderr)
        return 1
    text = routing_path.read_text(encoding="utf-8")
    raw = _load_yaml_mapping(text)
    if raw is None:
        print(f"worktrail-routing: {routing_path} is malformed YAML", file=sys.stderr)
        return 1
    meta: dict = {"source": str(routing_path), "unknown_keys": [], "warnings": []}
    try:
        _validate_routing(raw, meta)
    except OperatorConfigError:
        pass
    else:
        print(
            f"worktrail-routing: {routing_path} already loads cleanly under the current "
            "schema -- nothing to migrate",
            file=sys.stderr,
        )
        return 1

    migrated = _migrate_routing_dict(raw)
    backup_path = Path(str(routing_path) + ".bak")
    backup_path.write_text(text, encoding="utf-8")
    new_text = yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False)
    routing_path.write_text(new_text, encoding="utf-8")
    print(f"worktrail-routing: migrated {routing_path} (backup at {backup_path})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--init",
        action="store_true",
        help="write a starter routing.yaml (WORKTRAIL_ROUTING_FILE, else "
        "worktrail_home()/routing.yaml)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="with --init, overwrite an existing routing.yaml",
    )
    p.add_argument(
        "--show",
        action="store_true",
        help="print the routing resolved for --repo (repo-local routing: "
        "block, else the machine-wide file, else null)",
    )
    p.add_argument("--repo", default=".", help="repo path for --show (default: cwd)")
    p.add_argument(
        "--check",
        action="store_true",
        help="validate every routing.tiers cell against reality: gate an "
        "opencode model absent from `opencode models` as "
        "model_unavailable, warn on a free-pool id missing -free/:free, "
        "warn on an out-of-vocabulary effort, print a per-cell table, "
        "exit non-zero on any gate",
    )
    p.add_argument(
        "--migrate",
        action="store_true",
        help="rewrite a legacy routing.yaml (agents/fallback/purpose_tiers/"
        "drain.agent/harness-keyed tiers/agent_cli roles) into the "
        "current targets/tiers/roles schema, backing up the original "
        "to <file>.bak; refuses if the file already loads cleanly",
    )
    args = p.parse_args(argv)
    if args.init:
        return _init(args.force)
    if args.show:
        return _show(Path(args.repo))
    if args.check:
        return _check()
    if args.migrate:
        return _migrate()
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
