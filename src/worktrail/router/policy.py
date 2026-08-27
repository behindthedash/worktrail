#!/usr/bin/env python3
"""GO v2 repository policy loader — deterministic, stdlib-only.

Merges a repo-local `.worktrail/policy.yaml` (optional) over safe defaults so
the front door stays repository-agnostic while repos declare their own gates.

Most keys use a small, flat YAML subset (`parse_policy_yaml`): top-level
`key: value`, one nesting level, and `- item` lists; values parse as
bool/int/null/string. Unknown keys are kept (forward-compatible) but reported
under `unknown_keys` so typos are visible. The `routing:` block is the one
exception — it needs arbitrary nesting, so it's parsed with `yaml.safe_load`
(PyYAML) instead; see `_resolve_routing()`.

Safe defaults: auto-merge OFF, no protected paths, run records under
`worktrail_home()/runs` (default `~/.worktrail/runs`).

Usage: policy.py --repo /path/to/repo [--json]
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..shared.homedir import env_setting, worktrail_home
from .invocation_context import SUPPORTED_AGENTS


class OperatorConfigError(ValueError):
    """A machine-wide operator config (routing.yaml) exists but cannot be honored."""


POLICY_RELPATH = ".worktrail/policy.yaml"
# Prior conventions, checked in this order when POLICY_RELPATH is absent, so no
# repo is forced through a synchronized flag-day rename -- each migrates on its
# own schedule via a plain `git mv`:
#   1. docs/specs/worktrail-go-policy.yaml -- briefly canonical (2026-08-22),
#      live only in the first repo onboarded before this second relocation.
#   2. docs/specs/go-policy.yaml -- the original convention, still used by the
#      ~15 already-onboarded repos.
LEGACY_POLICY_RELPATHS = (
    "docs/specs/worktrail-go-policy.yaml",
    "docs/specs/go-policy.yaml",
)
ROUTING_FILE_ENV = "WORKTRAIL_ROUTING_FILE"


def default_routing_file() -> Path:
    """Machine-wide routing file: `worktrail_home()/routing.yaml`."""
    return worktrail_home() / "routing.yaml"


def default_run_record_dir() -> str:
    """Machine-default run-record root (`worktrail_home()/runs`), as a string
    so it slots into the policy dict exactly like a repo-declared value."""
    return str(worktrail_home() / "runs")

DEFAULTS: Dict[str, Any] = {
    # None -> auto-detect from `git remote show origin` (HEAD branch).
    "base_branch": None,
    # ENFORCEMENT SCOPE (2026-07 key-vs-consumer audit): only `automerge_eligible()`
    # below reads this — and it is itself only invoked by an agent following
    # sdd-workflow's Phase 8 merge-gate instructions (no script calls it).
    # `parallel-orchestrator/scripts/verify.py`'s own `auto_merge()` is a SEPARATE
    # code path that unconditionally calls `gh pr merge` once CI passes; it does
    # not import this module or consult this key at all. A repo that sets
    # `automerge.enabled: false` is NOT protected from orchestrator-driven merges.
    # See docs/specs/research/go-policy-integrity-audit.md; the behavioral fix is
    # tracked separately (handoff 20260714-120011-go-automerge-coordination).
    "automerge": {
        "enabled": False,
        "max_risk": "low",            # low|medium — high/critical never eligible
        "target_branches": [],         # empty = base branch only
    },
    # Paths whose changes always require a human merge decision. Enforced by
    # `automerge_eligible()`: any changed path matching one of these glob
    # patterns makes the PR automerge-ineligible (`go:no-automerge`),
    # independent of risk/gates. Callers pass the PR's changed paths in.
    "protected_paths": [],
    # Route letters that always pause for explicit human approval. Enforced by
    # `automerge_eligible()`: a PR whose classified route is in this list is
    # automerge-ineligible (`go:no-automerge`), independent of risk/gates.
    # Callers pass the classified route in.
    "require_human_routes": [],
    # Free-form note: how to authenticate for local protected-route testing
    # (e.g. "Playwright storage state via npm run e2e:auth; creds in .env.local").
    "auth_testing": None,
    # Where run records are written (outside the repo by default). None is a
    # lazy sentinel: load_policy() resolves it via default_run_record_dir()
    # so the worktrail-home lookup happens at load time, not import time.
    "run_record_dir": None,
    # Optional shell command the orchestrator runs on each group's integration branch
    # (where the group's task branches first coexist) BEFORE opening its PR, to catch
    # cross-task API drift ~a CI round-trip earlier. The command author owns any dep
    # install (e.g. "cd app && npm ci && npm test", "pytest -q"). None = skip entirely,
    # so repos without a wired command are never blocked.
    "integrate_smoke_cmd": None,
    # Optional shell command re-run against the ACTUAL updated `base` HEAD
    # immediately after each group's PR CONFIRMS merged, before the next
    # independent group in the same run is allowed to merge (verify.py's
    # cumulative post-merge gate). integrate_smoke_cmd only ever runs against
    # base + that one group's own tasks; independent FEATURE groups with no
    # declared deps/shared-file edge never see each other's changes before
    # merging, so a missing cross-group dependency is structurally invisible
    # to it. None = fall back to integrate_smoke_cmd; if that is also unset
    # the gate is skipped entirely (no config, no behavior change). Set to a
    # narrower command than integrate_smoke_cmd (e.g. skip slow e2e/browser
    # suites) if the full command is too slow to re-run after every merge.
    "post_merge_smoke_cmd": None,
    # Universal pre-PR test gate command (run by pre_pr_gate.py from the worktree
    # root). Enforced on EVERY PR-producing /go route — one-off claude/codex
    # subprocess workers included, not just orchestrator delivery groups. Preferred
    # over integrate_smoke_cmd (which pre_pr_gate.py falls back to). Set it to the
    # fastest command that mirrors the repo's CI merge gate; the literal string
    # "skip" opts the repo out explicitly (the gate treats unset as a failure).
    "pre_pr_cmd": None,
    # Optional glob patterns (mirroring the repo's own docs-only-bypass CI job, e.g.
    # a dorny/paths-filter config) that pre_pr_gate.py uses to skip pre_pr_cmd when
    # every changed path in the diff matches one of these patterns. Empty by default:
    # the docs-only fast path is opt-in per repo, since bypass rules differ per repo.
    "docs_only_paths": [],
    # Optional map of base branch -> the one head branch that canonically
    # promotes into it (e.g. {"stg": "dev", "prd": "stg"}), mirroring each
    # repo's own promotion-pairing CI guard (e.g. datalena's
    # check_promotion_target.py's `_CANONICAL_PAIRINGS`). pre_pr_gate.py's
    # is_promotion_pr() uses it to skip the heavy pre_pr_cmd for a genuinely
    # zero-local-diff PR whose head branch matches the target branch's
    # canonical promotion source -- content already went through CI on the
    # way into the head branch, so there is nothing local left to test; CI's
    # own required checks on the target branch (e.g. stg-predeploy-gate)
    # still fully cover it. Empty by default: promotion pairing is
    # repo-specific, not a worktrail convention.
    "promotion_pairs": {},
    # Optional shell command the parallel-orchestrator runs in each freshly-created
    # per-task worktree, right after `git worktree add` and before a worker is spawned
    # into it, to install local dependencies (e.g. "npm ci", "cd app && npm ci"). Task
    # worktrees branch off the base commit and start WITHOUT the base checkout's
    # gitignored node_modules; without this every implement/fix/review worker
    # rediscovers and reinstalls them mid-task. The sdd-workflow conductor threads it to
    # `live.py full-real` as `--bootstrap-cmd`. A configured command must succeed before
    # a worker is spawned; None = skip, so repos with no install step are unaffected.
    # For Node repos fanning out many task worktrees from one spec, a plain "npm ci"
    # here pays the full install cost once per task -- prefer
    # "worktrail-bootstrap-node-modules --app-dir app" (or --app-dir . when
    # package.json is at the worktree root), which hardlink-clones the sibling spec
    # worktree's already-installed node_modules when its lockfile matches byte-for-byte,
    # falling back to `npm ci` otherwise (orchestrator/bootstrap_node_modules.py).
    "worktree_bootstrap_cmd": None,
    # Optional glob patterns (fnmatch, matched against a task's declared `files`)
    # identifying schema-migration files for this repo (e.g. Alembic revisions,
    # Drizzle/Rails migrations). A task touching a matching path is always folded
    # into the parallel-orchestrator's BASE integration group (coordinator.py
    # `plan_groups()`), even if its own dependency graph would otherwise place it
    # in an independent feature group -- migrations and the code that depends on
    # the tables they create rarely share a `files` entry, so the shared-file
    # union-find can't catch that coupling, and a migration quarantined on its own
    # can silently leave dev with model/router code for tables that don't exist.
    # Threaded to `live.py full-real` as `--migration-pattern` (repeatable).
    # Empty by default: migration tooling/paths are repo-specific, not a
    # worktrail convention, so repos without this key see no behavior change.
    "migration_path_patterns": [],
    # Optional repo-owned worker defaults. Explicit invocation values still win;
    # these override machine-wide GO_/ORCH_ environment defaults.
    "agent_cli": None,
    "agent_model": None,
    "fallback_agent_cli": None,
    # Optional fan-out width for the parallel orchestrator: how many task
    # worktrees with live agent workers run concurrently (threaded to
    # `live.py full-real` as `--max-workers` by the sdd-workflow conductor).
    # None = use the orchestrator's own default (3). Size to the host: workers
    # are LLM-bound until their tests run, but they share cores with the
    # repo's CI runners — going wider than ~5 on a shared runner host queues
    # on the same cores CI needs.
    "max_workers": None,
    # Optional map of base branch -> merge method ("merge"|"squash"|"rebase") for
    # orchestrator-driven PRs targeting that branch. Overrides
    # parallel-orchestrator/verify.py's own repo-wide `_detect_merge_method()`
    # query, which cannot express "this repo allows merge commits for stg/prd
    # promotions but dev-target feature PRs should still squash." A base branch
    # not listed here falls back to repo-wide detection.
    # ENFORCEMENT SCOPE: like `automerge` above, verify.py does not read this
    # file directly -- the sdd-workflow conductor resolves the method for
    # `$BASE` (via `policy.py --merge-method-for-branch <branch>`) and passes it
    # through as `--merge-method` (see references/subagent-prompts.md#orchestrator).
    "merge_method_by_base": {},
    # Optional bounded wait (seconds) between the orchestrator's group PR
    # creations: before opening group N+1's PR, the orchestrator waits up to this
    # long for group N's PR checks to resolve (or the PR to merge/close).
    # Prevents N sibling group PRs from hitting a shared self-hosted CI runner
    # pool simultaneously (the "open orchestrator PRs sequentially" rule,
    # enforced in code). Best-effort: a red, stuck, or check-less PR never
    # blocks integration beyond this bound. 0 = off. Threaded to
    # `live.py full-real` as `--pr-pacing-wait` by the sdd-workflow conductor;
    # paces the sequential integrate path and --pipeline alike (in pipeline
    # mode only the integrate+PR-open step is serialized; verify overlaps).
    "pr_pacing_wait_s": 0,
    # Optional release focus for this repo (e.g. "v1.0"). When set, the repo is
    # in a release freeze: /go auto's queue pick skips this repo's briefs unless
    # their `triage:` frontmatter is `blocker` (dashboard.auto_pick_brief), so
    # newly-mined ideas default to captured-but-not-scheduled instead of
    # crowding out the release burn-down. None = no freeze, no behavior change.
    # Interactive selection is never blocked -- an operator can still pick a
    # deferred brief by hand; the gate only governs unattended auto-pick.
    "release_gate": None,
    # Optional subscription-aware routing table (defaults/roles/tiers/fallback).
    # None = no repo-local routing configured; see load_policy()'s machine-wide
    # file fallback and resolve_routing() below. Purely additive (DEC-001): the
    # flat agent_cli/agent_model/fallback_agent_cli keys above remain the
    # last-resort default when routing is unset everywhere.
    "routing": None,
    # Opt-in gate for Route D "ready-to-implement" seeding (seed_backlog.py's
    # find_ready_specs()): a repo must set this true for its specs to be
    # auto-seeded as Route D implementation briefs. False by default — a repo
    # that hasn't reviewed the feature never has its backlog silently drained
    # into implementation runs.
    "allow_seeded_implementation": False,
    # Opt-in map of add-on name -> config (e.g. {"aspens": {...}}) run by
    # addons/runner.py after each task/group's own work, staged and committed
    # separately. Empty by default: a repo with no add_ons: key gets zero
    # entries, so the runner iterates nothing and behavior is unchanged.
    "add_ons": {},
}

KNOWN_KEYS = set(DEFAULTS) | {"automerge"}
VALID_MAX_RISK = ("low", "medium")
VALID_MERGE_METHODS = ("merge", "squash", "rebase")
VALID_AGENT_CLIS = ("claude", "codex", "opencode")
# Subscription-CLI-external pools (API/OpenRouter): excluded from a resolved
# routing.fallback chain unless an entry explicitly opts in (AC-005) — these
# bypass the per-subscription capacity system entirely, so silently falling
# back to one would mask capacity/quota assumptions the rest of GO makes.
API_AGENT_LITERALS = ("openrouter", "api")
# routing.targets.<name>.pool literals (design D3): `subscription`/`free` are
# harness-native capacity-gated pools; `api` bypasses the harness's own
# subscription capacity system entirely, so it requires an explicit
# `api_opt_in: true` on the same target (see _validate_routing_targets()).
VALID_POOLS = ("subscription", "free", "api")


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s.startswith("#"):
        return None
    if "#" in s and not (s.startswith('"') or s.startswith("'")):
        s = s.split("#", 1)[0].strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        return s


def parse_policy_yaml(text: str) -> Dict[str, Any]:
    """Parse the supported YAML subset into a dict."""
    result: Dict[str, Any] = {}
    current_key: Optional[str] = None   # nesting context (one level)
    current_list: Optional[List[Any]] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indented = line[0] in (" ", "\t")
        stripped = line.strip()
        if stripped.startswith("- "):
            item = _parse_scalar(stripped[2:])
            if current_list is None:
                # list under the most recent key
                if current_key is None:
                    continue
                current_list = []
                result[current_key] = current_list
            current_list.append(item)
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if not indented:
            current_list = None
            if value == "":
                current_key = key
                result.setdefault(key, {})
            else:
                current_key = key
                result[key] = _parse_scalar(value)
        else:
            # one nesting level under current_key
            current_list = None
            if current_key is None:
                continue
            parent = result.get(current_key)
            if not isinstance(parent, dict):
                parent = {}
                result[current_key] = parent
            if value == "":
                # nested list expected next
                parent[key] = []
                current_list = parent[key]
            else:
                parent[key] = _parse_scalar(value)
    return result


def _validate_agent_entry(value: Any, meta: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
    """Normalize one routing agent entry (`"claude"` or `{agent_cli, agent_model}`,
    also accepting the `{agent, model}` shorthand used by `routing.tiers`).

    Returns `{"agent_cli": str, "agent_model": Optional[str], "effort": Optional[str]}`,
    or `None` (with a `_meta.warnings` entry naming `label`) if `value` is malformed or
    names an agent literal outside `VALID_AGENT_CLIS`. Mirrors the existing
    `agent_cli`/`fallback_agent_cli` validation (policy.py `load_policy()`).
    """
    if isinstance(value, str):
        agent_cli, agent_model, effort = value, None, None
    elif isinstance(value, dict):
        agent_cli = value.get("agent_cli", value.get("agent"))
        agent_model = value.get("agent_model", value.get("model"))
        effort = value.get("effort")
    else:
        meta["warnings"].append(f"{label}: malformed entry {value!r}; dropped")
        return None
    if agent_cli not in VALID_AGENT_CLIS:
        meta["warnings"].append(
            f"{label}: invalid agent literal {agent_cli!r} (allowed: {VALID_AGENT_CLIS}); dropped")
        return None
    if agent_model is not None and not isinstance(agent_model, str):
        meta["warnings"].append(f"{label}: agent_model must be a string; dropped")
        agent_model = None
    if effort is not None and not isinstance(effort, str):
        meta["warnings"].append(f"{label}: effort must be a string; dropped")
        effort = None
    return {"agent_cli": agent_cli, "agent_model": agent_model, "effort": effort}


def _validate_routing_targets(raw: Any, meta: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """`routing.targets`: the ordered `{name: {harness, pool, api_opt_in?, auth?}}`
    mapping (design D3) that separates *harness* (which CLI runs the task) from
    *pool* (which capacity/auth surface it draws from) -- the schema `tiers`
    rows and `roles` entries key against by target name.

    File order is preserved: `raw` comes from `yaml.safe_load` (real YAML, via
    `_resolve_routing()`), whose mapping keys already iterate in declaration
    order, and this function only ever assigns into `resolved` while walking
    `raw.items()` -- never re-sorts or re-inserts.

    A target is dropped (with a `meta["warnings"]` entry) when `harness` is
    outside `SUPPORTED_AGENTS` or `pool` is outside `VALID_POOLS` -- both are
    load-bearing identity fields the selector cannot fall back on. A `pool:
    api` target without `api_opt_in: true` is *kept*, not dropped: its
    `api_opt_in` stays `False` so downstream selection (`select_cell`, task
    2.1) can skip it as ineligible while still surfacing it (e.g. in
    `worktrail-routing --check` diagnostics); a warning is appended either way.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        meta["warnings"].append(f"routing.targets must be a mapping; got {raw!r} — ignored")
        return {}
    resolved: Dict[str, Dict[str, Any]] = {}
    for name, entry in raw.items():
        if not isinstance(name, str):
            meta["warnings"].append(f"routing.targets: key must be a string; got {name!r} — ignored")
            continue
        if not isinstance(entry, dict):
            meta["warnings"].append(
                f"routing.targets.{name} must be a mapping ({{harness, pool, api_opt_in?, auth?}}); "
                f"got {entry!r} — dropped")
            continue
        harness = entry.get("harness")
        if harness not in SUPPORTED_AGENTS:
            meta["warnings"].append(
                f"routing.targets.{name}.harness: invalid harness {harness!r} "
                f"(allowed: {SUPPORTED_AGENTS}); dropped")
            continue
        pool = entry.get("pool")
        if pool not in VALID_POOLS:
            meta["warnings"].append(
                f"routing.targets.{name}.pool: invalid pool {pool!r} "
                f"(allowed: {VALID_POOLS}); dropped")
            continue
        api_opt_in = bool(entry.get("api_opt_in", False))
        if pool == "api" and not api_opt_in:
            meta["warnings"].append(
                f"routing.targets.{name}: pool 'api' requires explicit api_opt_in: true; "
                "target kept but ineligible until opted in")
        auth = entry.get("auth")
        resolved[name] = {
            "harness": harness,
            "pool": pool,
            "api_opt_in": api_opt_in,
            "auth": auth,
        }
    return resolved


def _validate_routing_agents(raw: Any, meta: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """`routing.agents`: `{<agent>: {default_model: str}}` — the per-agent
    default-model table `default_model_for_agent()` resolves against, replacing
    the retired `model-defaults.yaml` (D2/D3). Unlike the other
    `_validate_routing_*` tables, entries here are not `_validate_agent_entry`
    shorthand — malformed entries are dropped via `meta["warnings"]`, never
    raised, consistent with the sibling validators."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        meta["warnings"].append(f"routing.agents must be a mapping; got {raw!r} — ignored")
        return {}
    resolved: Dict[str, Dict[str, Any]] = {}
    for agent, entry in raw.items():
        if not isinstance(agent, str) or agent not in VALID_AGENT_CLIS:
            meta["warnings"].append(
                f"routing.agents: invalid agent literal {agent!r} "
                f"(allowed: {VALID_AGENT_CLIS}); dropped")
            continue
        if not isinstance(entry, dict):
            meta["warnings"].append(
                f"routing.agents.{agent} must be a mapping ({{default_model: str}}); "
                f"got {entry!r} — dropped")
            continue
        default_model = entry.get("default_model")
        if not isinstance(default_model, str):
            meta["warnings"].append(
                f"routing.agents.{agent}.default_model must be a string; "
                f"got {default_model!r} — dropped")
            continue
        resolved[agent] = {"default_model": default_model}
    return resolved


def _validate_routing_drain(raw: Any, meta: Dict[str, Any]) -> Dict[str, Any]:
    """`routing.drain`: `{agent: str, fallback_agents: [str], max_workers: int>=1}` —
    the machine-wide drain defaults formerly read from `config.json` by
    `shared/operator_config.py::drain_config()`, consolidated into
    `routing.yaml`. Ports that function's field-level shape checks, messages,
    and loud-failure semantics verbatim: a malformed `drain` section is stated
    operator intent, so it raises `OperatorConfigError` rather than warning
    and falling back — unlike the sibling `_validate_routing_*` validators,
    which drop-and-warn. Agent literal validity (is it a supported agent?)
    stays the drain CLI's own check, same as `drain_config()`."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise OperatorConfigError(f"routing.drain must be a mapping; got {raw!r}")
    agent = raw.get("agent")
    if agent is not None and not isinstance(agent, str):
        raise OperatorConfigError("routing.drain.agent must be a string")
    fallback_agents = raw.get("fallback_agents", [])
    if not isinstance(fallback_agents, list) or any(
            not isinstance(f, str) for f in fallback_agents):
        raise OperatorConfigError(
            "routing.drain.fallback_agents must be a list of strings")
    max_workers = raw.get("max_workers")
    if max_workers is None:
        max_workers = 2
    elif not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
        raise OperatorConfigError(
            "routing.drain.max_workers must be a positive integer")
    return {
        "agent": agent,
        "fallback_agents": list(fallback_agents),
        "max_workers": max_workers,
    }


def _validate_routing_defaults(raw: Any, meta: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """`routing.defaults`: `{route: {risk: agent-entry}}` — the `(route, risk)` table
    `resolve_routing()` consults for the primary agent/model."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        meta["warnings"].append(f"routing.defaults must be a mapping; got {raw!r} — ignored")
        return {}
    resolved: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for route, risk_map in raw.items():
        if not isinstance(risk_map, dict):
            meta["warnings"].append(
                f"routing.defaults.{route} must be a mapping of risk -> agent entry; "
                f"got {risk_map!r} — ignored")
            continue
        risks: Dict[str, Dict[str, Any]] = {}
        for risk, entry in risk_map.items():
            normalized = _validate_agent_entry(entry, meta, f"routing.defaults.{route}.{risk}")
            if normalized is not None:
                risks[risk] = normalized
        if risks:
            resolved[route] = risks
    return resolved


def _validate_routing_roles(
    raw: Any, tiers: Dict[str, Dict[str, Dict[str, Any]]],
    targets: Dict[str, Dict[str, Any]], meta: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """`routing.roles`: `{role: {tier, prefer?, independent?}}` — a role now
    resolves to a *tier row* (`routing.tiers`, task 1.2), not directly to an
    agent/model: `dispatch.tier_for()` (task 4.1) reads `tier` to pick the row
    `select_cell()` (task 2.1) walks, `prefer` to reorder that row toward one
    declared target first, and `independent` to mark a role (e.g. review)
    that should avoid re-using the implementer's own harness.

    An entry is dropped (with a `meta["warnings"]` entry) when it is not a
    mapping, or when `tier` is missing, not a string, or does not name a
    declared row of the already-resolved `tiers` table (`_validate_routing_tiers()`)
    — a role with no valid tier has nothing for the selector to walk. A
    `prefer` naming a target not declared in `targets` (`_validate_routing_targets()`)
    drops the whole entry the same way, rather than keeping a tier with an
    unusable preference. `independent` defaults to `False`; a non-bool value
    is dropped (with a warning), falling back to `False` rather than the
    whole entry, since it is the least load-bearing field.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        meta["warnings"].append(f"routing.roles must be a mapping; got {raw!r} — ignored")
        return {}
    resolved: Dict[str, Dict[str, Any]] = {}
    for role, entry in raw.items():
        if not isinstance(entry, dict):
            meta["warnings"].append(
                f"routing.roles.{role} must be a mapping ({{tier, prefer?, independent?}}); "
                f"got {entry!r} — dropped")
            continue
        tier = entry.get("tier")
        if not isinstance(tier, str) or tier not in tiers:
            meta["warnings"].append(
                f"routing.roles.{role}.tier {tier!r} does not name a declared "
                "routing.tiers row; dropped")
            continue
        prefer = entry.get("prefer")
        if prefer is not None and (not isinstance(prefer, str) or prefer not in targets):
            meta["warnings"].append(
                f"routing.roles.{role}.prefer {prefer!r} does not name a declared "
                "routing.targets entry; dropped")
            continue
        independent = entry.get("independent", False)
        if not isinstance(independent, bool):
            meta["warnings"].append(
                f"routing.roles.{role}.independent must be a boolean; "
                f"got {independent!r} — dropped")
            independent = False
        resolved[role] = {"tier": tier, "prefer": prefer, "independent": independent}
    return resolved


def _validate_routing_purpose_tiers(raw: Any, meta: Dict[str, Any]) -> Dict[str, str]:
    """`routing.purposes`: `{purpose: tier}` — a plain string-to-string map
    (unlike `routing.roles`/`routing.tiers`, values here are tier names, not
    agent entries) that `dispatch.tier_for()` (task 4.1) consults ahead of
    `complexity` to resolve a task's tier."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        meta["warnings"].append(f"routing.purposes must be a mapping; got {raw!r} — ignored")
        return {}
    resolved: Dict[str, str] = {}
    for purpose, tier in raw.items():
        if not isinstance(purpose, str) or not isinstance(tier, str):
            meta["warnings"].append(
                f"routing.purposes.{purpose!r}: value must be a string; got {tier!r} — dropped")
            continue
        resolved[purpose] = tier
    return resolved


def _validate_routing_tiers(
    raw: Any, targets: Dict[str, Dict[str, Any]], meta: Dict[str, Any]
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """`routing.tiers`: `{<row>: {<target>: {model, effort?}}}` — tier rows
    keyed by declared target name (`routing.targets`, task 1.1), replacing the
    old `(complexity, domain)`-keyed agent-entry table.

    A row is a tier name (e.g. `t1-deep`); each cell names one of that row's
    declared targets and the model (required) / effort (optional) to use when
    the selector (task 2.1) walks that target for that tier. A target with no
    cell in a given row simply cannot serve that tier — the selector skips it;
    this is not itself a warning-worthy condition, since not every target need
    cover every tier.

    A cell naming an undeclared target (not a key of `targets`, the already-
    validated `_validate_routing_targets()` output) is dropped with a warning
    — `routing.targets` is the single source of truth for what a target *is*,
    so a typo'd or removed target name here is a local, per-cell mistake, not
    a `routing.targets` problem.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        meta["warnings"].append(f"routing.tiers must be a mapping; got {raw!r} — ignored")
        return {}
    resolved: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row, cells in raw.items():
        if not isinstance(row, str):
            meta["warnings"].append(f"routing.tiers: key must be a string; got {row!r} — ignored")
            continue
        if not isinstance(cells, dict):
            meta["warnings"].append(
                f"routing.tiers.{row} must be a mapping of target -> {{model, effort?}}; "
                f"got {cells!r} — ignored")
            continue
        row_resolved: Dict[str, Dict[str, Any]] = {}
        for target, cell in cells.items():
            if not isinstance(target, str) or target not in targets:
                meta["warnings"].append(
                    f"routing.tiers.{row}.{target!r}: undeclared target "
                    "(not in routing.targets); dropped")
                continue
            if not isinstance(cell, dict):
                meta["warnings"].append(
                    f"routing.tiers.{row}.{target} must be a mapping ({{model, effort?}}); "
                    f"got {cell!r} — dropped")
                continue
            model = cell.get("model")
            if not isinstance(model, str):
                meta["warnings"].append(
                    f"routing.tiers.{row}.{target}.model must be a string; "
                    f"got {model!r} — dropped")
                continue
            effort = cell.get("effort")
            if effort is not None and not isinstance(effort, str):
                meta["warnings"].append(
                    f"routing.tiers.{row}.{target}.effort must be a string; dropped")
                effort = None
            row_resolved[target] = {"model": model, "effort": effort}
        if row_resolved:
            resolved[row] = row_resolved
    return resolved


def _validate_routing_default_tier(
    raw: Any, tiers: Dict[str, Dict[str, Dict[str, Any]]], meta: Dict[str, Any]
) -> Optional[str]:
    """`routing.default_tier`: the tier row used absent any more specific
    role/purpose/task tier (`dispatch.tier_for()`, task 4.1). Must name a
    declared row of the already-resolved `routing.tiers` table; anything else
    resolves to `None` with a warning rather than silently pointing dispatch
    at a row with no cells."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        meta["warnings"].append(f"routing.default_tier must be a string; got {raw!r} — ignored")
        return None
    if raw not in tiers:
        meta["warnings"].append(
            f"routing.default_tier {raw!r} does not name a declared routing.tiers row; ignored")
        return None
    return raw


def _validate_routing_fallback(raw: Any, meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`routing.fallback`: an ordered list of agent entries (`"codex"` or
    `{agent_cli, agent_model, effort, api_opt_in}`). An `API_AGENT_LITERALS` entry is
    dropped unless `api_opt_in: true` is set explicitly (AC-005)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        meta["warnings"].append(f"routing.fallback must be a list; got {raw!r} — ignored")
        return []
    resolved: List[Dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, str):
            agent_cli, agent_model, effort, api_opt_in = entry, None, None, False
        elif isinstance(entry, dict):
            agent_cli = entry.get("agent_cli")
            agent_model = entry.get("agent_model")
            effort = entry.get("effort")
            api_opt_in = bool(entry.get("api_opt_in", False))
        else:
            meta["warnings"].append(f"routing.fallback: malformed entry {entry!r}; dropped")
            continue
        if agent_model is not None and not isinstance(agent_model, str):
            meta["warnings"].append("routing.fallback: agent_model must be a string; dropped")
            agent_model = None
        if effort is not None and not isinstance(effort, str):
            meta["warnings"].append("routing.fallback: effort must be a string; dropped")
            effort = None
        if agent_cli in VALID_AGENT_CLIS:
            resolved.append({"agent_cli": agent_cli, "agent_model": agent_model, "effort": effort})
        elif agent_cli in API_AGENT_LITERALS:
            if api_opt_in:
                resolved.append({"agent_cli": agent_cli, "agent_model": agent_model, "effort": effort, "api": True})
            else:
                meta["warnings"].append(
                    f"routing.fallback: API/OpenRouter agent {agent_cli!r} excluded "
                    "(requires explicit api_opt_in: true); ignored")
        else:
            meta["warnings"].append(
                f"routing.fallback: invalid agent literal {agent_cli!r} "
                f"(allowed: {VALID_AGENT_CLIS + API_AGENT_LITERALS}); dropped")
    return resolved


def _validate_routing(raw: Any, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate a raw `routing:` mapping (from either `go-policy.yaml` or the
    machine-wide routing file) into
    `{targets, defaults, roles, tiers, default_tier, fallback, purposes,
    agents, drain}`.

    Returns `None` when `raw` is absent, an empty mapping, or not a mapping at
    all (the last case appends a `_meta.warnings` entry) — callers treat `None`
    as "fall through to the next source" (AC-003/AC-004).

    `targets` is resolved first: `tiers`' cells are only valid when they name
    a target already declared there, and `default_tier` is only valid when it
    names a row `tiers` actually resolved.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        meta["warnings"].append(
            f"routing must be a mapping (defaults/roles/tiers/fallback); got {raw!r} — ignored")
        return None
    if not raw:
        return None
    targets = _validate_routing_targets(raw.get("targets"), meta)
    tiers = _validate_routing_tiers(raw.get("tiers"), targets, meta)
    return {
        "targets": targets,
        "defaults": _validate_routing_defaults(raw.get("defaults"), meta),
        "roles": _validate_routing_roles(raw.get("roles"), tiers, targets, meta),
        "tiers": tiers,
        "default_tier": _validate_routing_default_tier(raw.get("default_tier"), tiers, meta),
        "fallback": _validate_routing_fallback(raw.get("fallback"), meta),
        "purposes": _validate_routing_purpose_tiers(raw.get("purposes"), meta),
        "agents": _validate_routing_agents(raw.get("agents"), meta),
        "drain": _validate_routing_drain(raw.get("drain"), meta),
    }


def _load_yaml_mapping(text: str) -> Optional[Dict[str, Any]]:
    """`yaml.safe_load` a full document, returning it only if it parsed to a
    mapping; `None` on malformed YAML or a non-mapping top level (never raises)."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _resolve_add_ons(parsed_local: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve `policy["add_ons"]` from the repo-local block, re-parsed with
    real YAML (design D3: `Dict[str, Dict[str, Any]]`, arbitrary per-add-on
    nesting `add_ons.<name>.<key>`) rather than `parse_policy_yaml`'s
    one-level-nesting subset, which flattens a nested add-on config's own
    keys up into `add_ons` as siblings of the add-on name instead of nesting
    them under it -- the same limitation `routing` already has its own real-
    YAML resolution for.

    A malformed top-level shape (not a mapping) falls back to the safe `{}`
    default with a warning, matching `automerge`'s own fail-safe posture --
    never let a malformed policy file widen behavior beyond the documented
    opt-in-per-repo default of zero add-ons.
    """
    raw = parsed_local.get("add_ons") if isinstance(parsed_local, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        meta["warnings"].append(
            f"add_ons must be a mapping of add-on name -> config; got {raw!r} — ignored")
        return {}
    return raw


def resolved_routing_file_path() -> Path:
    """The machine-wide routing file `_resolve_routing()`/`routing_cli` actually
    read from and write to: `WORKTRAIL_ROUTING_FILE` (or its legacy `GO_`
    synonym) if set, else `default_routing_file()`. Any caller that names this
    path in a message to the operator (e.g. an error pointing at
    `worktrail-routing --init`) must resolve it through here, not through
    `default_routing_file()` alone -- naming the unconditional default while an
    override env var is actually in effect names the wrong file."""
    override = env_setting(ROUTING_FILE_ENV)
    return Path(override).expanduser() if override else default_routing_file()


def _resolve_routing(repo: Path, parsed_local: Dict[str, Any], meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve `policy["routing"]`: repo-local `routing:` block, else the
    machine-wide routing file (`WORKTRAIL_ROUTING_FILE`, default `worktrail_home()/routing.yaml`),
    else `None` (flat keys remain the last-resort default — AC-003/AC-004).

    The repo-local block is re-parsed with `yaml.safe_load` (architecture §3.8)
    rather than `parse_policy_yaml`'s one-level-nesting subset, since `routing`
    needs arbitrary nesting (`defaults.<route>.<risk>.agent_cli`).
    """
    local_raw = parsed_local.get("routing") if isinstance(parsed_local, dict) else None
    validated = _validate_routing(local_raw, meta)
    if validated is not None:
        return validated
    routing_path = resolved_routing_file_path()
    if not routing_path.is_file():
        return None
    try:
        text = routing_path.read_text(encoding="utf-8")
    except OSError:
        return None
    mw_raw = _load_yaml_mapping(text)
    if mw_raw is None:
        meta["warnings"].append(
            f"machine-wide routing file {routing_path} is malformed YAML; ignored")
        return None
    return _validate_routing(mw_raw, meta)


def resolve_routing(policy: Dict[str, Any], route: str = "", risk: str = "") -> Dict[str, Any]:
    """Deterministically resolve the effective targets/tiers/roles/purposes/
    drain configuration — the single source of truth for the selector
    (`select_cell()`, task 2.1) and dispatch (`tier_for()`, task 4.1).

    Args:
        policy: the dict returned by `load_policy()`.
        route, risk: unused (kept for call-site compatibility with the
            retired route/risk-keyed `routing.defaults` resolution; every
            caller of `resolve_routing()` is migrated off passing them by
            the tasks depending on this one).

    Returns:
        {
          "targets": {name: {"harness", "pool", "api_opt_in", "auth"}},
                                          # routing.targets (task 1.1)
          "tiers": {row: {target: {"model", "effort"}}},
                                          # routing.tiers (task 1.2)
          "roles": {role: {"tier", "prefer", "independent"}},
                                          # routing.roles
          "purposes": {purpose: tier},   # routing.purposes, consulted by
                                          # dispatch.tier_for() ahead of
                                          # complexity to resolve a task's tier
          "default_tier": Optional[str], # routing.default_tier
          "drain": {"agent": Optional[str], "fallback_agents": [str],
                    "max_workers": int}, # routing.drain, the machine-wide
                                          # drain defaults (D1); {} when absent
          "agents": {agent: {"default_model": str}},
                                          # routing.agents -- STILL exposed
                                          # here, not just internal to
                                          # policy["routing"]: spawnlib.py's
                                          # default_model_for_agent() (retired
                                          # only in task 4.2) reads
                                          # resolve_routing(...)["agents"]
                                          # directly, so dropping this key
                                          # before that retirement lands
                                          # raises KeyError on every agent
                                          # spawn. {} when absent.
          "fallback": [{"agent_cli", "agent_model", "effort", "api"?}, ...],
                                          # routing.fallback -- STILL exposed
                                          # for the same reason: compile.py's
                                          # invocation-context resolution
                                          # reads resolve_routing(...)["fallback"]
                                          # directly. [] when absent.
        }

    Same inputs always produce the same output (REQ-NR002): no randomness, no
    I/O, no clock/env reads beyond what `load_policy()` already resolved.
    """
    routing = policy.get("routing")
    if not routing:
        return {
            "targets": {},
            "tiers": {},
            "roles": {},
            "purposes": {},
            "default_tier": None,
            "drain": {},
            "agents": {},
            "fallback": [],
        }
    return {
        "targets": routing.get("targets") or {},
        "tiers": routing.get("tiers") or {},
        "roles": routing.get("roles") or {},
        "purposes": routing.get("purposes") or {},
        "default_tier": routing.get("default_tier"),
        "drain": routing.get("drain") or {},
        "agents": routing.get("agents") or {},
        "fallback": routing.get("fallback") or [],
    }


def resolve_tier_map(policy: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Resolve `policy["routing"]["tiers"]` into the selector-ready
    `{row: {target: {"model", "effort"}}}` mapping (task 2.1's `select_cell()`
    walks one row across its declared targets in preference order).

    A pure passthrough of the already-normalized table `_validate_routing_tiers()`
    built eagerly at `load_policy()` time.

    Args:
        policy: the dict returned by `load_policy()`.

    Returns:
        `{}` when `policy["routing"]` is absent/`None` or has no `tiers`
        entries; never raises (mirrors `resolve_routing()`'s defensive
        posture, REQ-NR004).
    """
    routing = policy.get("routing")
    if not routing:
        return {}
    return routing.get("tiers") or {}


def _json_safe(obj: Any) -> Any:
    """Recursively convert any tuple dict key to its `"a/b"` (or bare `"a"`)
    string form so `load_policy()`'s return value can be `json.dumps`-ed.
    `routing.tiers` is string-keyed by row/target since `_validate_routing_tiers()`'s
    1.2 rewrite, so this is now a defensive no-op for it specifically; kept
    generic in case a future validator stores another non-JSON-safe key."""
    if isinstance(obj, dict):
        safe: Dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, tuple):
                complexity, domain = key
                key = f"{complexity}/{domain}" if domain else complexity
            safe[key] = _json_safe(value)
        return safe
    if isinstance(obj, list):
        return [_json_safe(item) for item in obj]
    return obj


def detect_external_automerge(repo: Path) -> Dict[str, Any]:
    """Scan `.github/workflows/*.yml`/`*.yaml` for a repo's own auto-merge automation.

    Independent of `go-policy.yaml` — reads only the workflows directory, sorted by
    name so "first match wins" is deterministic.
    """
    workflows_dir = repo / ".github" / "workflows"
    result: Dict[str, Any] = {"detected": False, "workflow_file": None}
    if not workflows_dir.is_dir():
        return result
    candidates = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    candidates.sort(key=lambda p: p.name)
    for wf in candidates:
        if not wf.is_file():
            continue
        text = wf.read_text(encoding="utf-8")
        if (("gh pr merge" in text and "--auto" in text)
                or "enable-auto-merge" in text
                or "enable-pull-request-automerge" in text):
            result["detected"] = True
            result["workflow_file"] = str(wf.relative_to(repo))
            break
    return result


def policy_file_path(repo: Path) -> Path:
    """POLICY_RELPATH if present, else the first of LEGACY_POLICY_RELPATHS that
    exists, else POLICY_RELPATH (possibly nonexistent) -- the single source of
    truth for "where is this repo's policy file", used by every repo-detection
    gate (policy_selfcheck, policy_drift_selfcheck, reconcile_pr_labels) so
    they don't go blind to a repo still on a prior convention."""
    current = repo / POLICY_RELPATH
    if current.is_file():
        return current
    for relpath in LEGACY_POLICY_RELPATHS:
        legacy = repo / relpath
        if legacy.is_file():
            return legacy
    return current


def has_policy_file(repo: Path) -> bool:
    return policy_file_path(repo).is_file()


def _resolve_policy_src(repo: Path, meta: Dict[str, Any]) -> Path:
    """policy_file_path(), plus a one-time deprecation warning when it
    resolved to a legacy relpath."""
    src = policy_file_path(repo)
    if src.is_file() and str(src) != str(repo / POLICY_RELPATH):
        meta["warnings"].append(
            f"{src.relative_to(repo)} is deprecated -- rename to {POLICY_RELPATH} "
            "(git mv, no content changes needed)")
    return src


def load_policy(repo: Path) -> Dict[str, Any]:
    policy = copy.deepcopy(DEFAULTS)
    # Resolved here (not in DEFAULTS) so the worktrail-home lookup stays lazy;
    # a repo-declared `run_record_dir` below still overrides it.
    policy["run_record_dir"] = default_run_record_dir()
    meta: Dict[str, Any] = {"source": None, "unknown_keys": [], "warnings": []}
    src = _resolve_policy_src(repo, meta)
    parsed: Dict[str, Any] = {}
    if src.is_file():
        meta["source"] = str(src)
        parsed = parse_policy_yaml(src.read_text(encoding="utf-8"))
        for key, value in parsed.items():
            if key not in KNOWN_KEYS:
                meta["unknown_keys"].append(key)
                policy[key] = value
                continue
            if key == "automerge":
                if isinstance(value, dict):
                    policy["automerge"].update(value)
                else:
                    # A scalar `automerge: true` hand-edit must not crash Phase 4;
                    # keep the safe defaults and say so.
                    meta["warnings"].append(
                        "automerge must be a mapping (enabled/max_risk/...); "
                        f"got {value!r} — ignored, defaults kept")
            elif key == "routing":
                # `parse_policy_yaml`'s one-level-nesting subset can't represent
                # `routing`'s arbitrary nesting — resolved separately below via
                # `_resolve_routing()` (real `yaml.safe_load`).
                continue
            elif key == "add_ons":
                # Same limitation as `routing` above: a per-add-on config is a
                # nested dict (design D3, `Dict[str, Dict[str, Any]]`, e.g.
                # `add_ons: {aspens: {enabled: true, target: ..., required: ...}}`).
                # `parse_policy_yaml`'s one-level-nesting subset flattens the
                # add-on's own keys up into `add_ons` as siblings of the add-on
                # name instead of nesting them under it — resolved separately
                # below via `full_local` (real `yaml.safe_load`).
                continue
            else:
                policy[key] = value
    # Repo `routing:` block (re-parsed with real YAML), else the machine-wide
    # routing file, else None (flat keys stay the last-resort default).
    if src.is_file():
        full_local = _load_yaml_mapping(src.read_text(encoding="utf-8")) or {}
    else:
        full_local = {}
    policy["routing"] = _resolve_routing(repo, full_local, meta)
    policy["add_ons"] = _resolve_add_ons(full_local, meta)
    # Validation / clamping — never let a policy file widen autonomy unsafely.
    mr = policy["automerge"].get("max_risk", "low")
    if mr not in VALID_MAX_RISK:
        meta["warnings"].append(
            f"automerge.max_risk '{mr}' invalid (allowed: {VALID_MAX_RISK}); clamped to 'low'")
        policy["automerge"]["max_risk"] = "low"
    if not isinstance(policy["automerge"].get("enabled"), bool):
        meta["warnings"].append("automerge.enabled not boolean; forced to false")
        policy["automerge"]["enabled"] = False
    if not isinstance(policy.get("allow_seeded_implementation"), bool):
        meta["warnings"].append(
            "allow_seeded_implementation not boolean; forced to false")
        policy["allow_seeded_implementation"] = False
    for key in ("agent_cli", "fallback_agent_cli"):
        value = policy.get(key)
        if value is not None and value not in VALID_AGENT_CLIS:
            meta["warnings"].append(
                f"{key} '{value}' invalid (allowed: {VALID_AGENT_CLIS}); dropped"
            )
            policy[key] = None
    if policy.get("agent_model") is not None and not isinstance(policy["agent_model"], str):
        meta["warnings"].append("agent_model must be a string; dropped")
        policy["agent_model"] = None
    # Integer keys threaded verbatim into the orchestrator invocation — a bad
    # value would break the `live.py full-real` command line, so drop to the
    # default instead of passing it through.
    for key, minimum in (("max_workers", 1), ("pr_pacing_wait_s", 0)):
        value = policy.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            meta["warnings"].append(
                f"{key} must be an integer >= {minimum}; dropped ({value!r})"
            )
            policy[key] = DEFAULTS[key]
    mmb = policy.get("merge_method_by_base")
    if not isinstance(mmb, dict):
        if mmb:
            meta["warnings"].append(
                "merge_method_by_base must be a mapping (branch: method); "
                f"got {mmb!r} — ignored, defaults kept")
        policy["merge_method_by_base"] = {}
    else:
        cleaned: Dict[str, Any] = {}
        for branch, method in mmb.items():
            if method in VALID_MERGE_METHODS:
                cleaned[branch] = method
            else:
                meta["warnings"].append(
                    f"merge_method_by_base.{branch} '{method}' invalid "
                    f"(allowed: {VALID_MERGE_METHODS}); dropped")
        policy["merge_method_by_base"] = cleaned
    pp = policy.get("promotion_pairs")
    if not isinstance(pp, dict):
        if pp:
            meta["warnings"].append(
                "promotion_pairs must be a mapping (branch: head_branch); "
                f"got {pp!r} — ignored, defaults kept")
        policy["promotion_pairs"] = {}
    else:
        cleaned_pp: Dict[str, Any] = {}
        for branch, head in pp.items():
            if isinstance(head, str) and head.strip():
                cleaned_pp[branch] = head
            else:
                meta["warnings"].append(
                    f"promotion_pairs.{branch} must be a non-empty string; dropped")
        policy["promotion_pairs"] = cleaned_pp
    # Nudge: a repo with specs but no integrated-smoke command ships group PRs whose
    # cross-task integration is only checked at CI/merge — the per-task review loop is
    # structurally blind to it. Surface once; do not block.
    if not (policy.get("integrate_smoke_cmd") or policy.get("pre_pr_cmd")):
        specs_dir = repo / "docs" / "specs"
        has_specs = specs_dir.is_dir() and any(
            p.is_dir() and p.name[:1].isdigit() for p in specs_dir.iterdir()
        )
        if has_specs:
            meta["warnings"].append(
                "integrate_smoke_cmd unset: group PRs won't be smoke-tested before merge; "
                "cross-task integration bugs will surface only at CI. Consider adding the "
                "repo's fast test command to docs/specs/go-policy.yaml.")
    meta["external_automerge"] = detect_external_automerge(repo)
    policy["_meta"] = meta
    return policy


def _protected_path_match(path: str, patterns: List[str]) -> Optional[str]:
    """First pattern matching `path`, or None. A trailing '/' pattern is a
    directory-prefix match (`protected_paths` examples use "migrations/" to
    mean everything under migrations/); anything else is an fnmatch glob."""
    for pattern in patterns:
        if pattern.endswith("/"):
            if path == pattern.rstrip("/") or path.startswith(pattern):
                return pattern
        elif fnmatch.fnmatch(path, pattern):
            return pattern
    return None


def automerge_eligible(policy: Dict[str, Any], risk: str, gates: List[str],
                       target_branch: str, route: Optional[str] = None,
                       changed_paths: Optional[List[str]] = None) -> Tuple[bool, str]:
    """The deterministic part of the merge gate (CI/review state is checked live).

    `route` and `changed_paths` are optional and each check applies only when
    its input is supplied: a caller that omits `route` skips the
    `require_human_routes` check, and one that omits `changed_paths` skips the
    `protected_paths` check, rather than failing closed on absent input.
    Callers that want those two policy keys enforced must pass them in.
    """
    am = policy["automerge"]
    if "never_automerge" in gates or "require_human_approval" in gates:
        return False, "protected operation or human-approval gate"
    if route is not None and route in (policy.get("require_human_routes") or []):
        return False, f"route {route} is in policy's require_human_routes"
    if changed_paths is not None:
        protected = policy.get("protected_paths") or []
        for path in changed_paths:
            matched = _protected_path_match(path, protected)
            if matched:
                return False, (
                    f"changed path '{path}' matches protected_paths pattern '{matched}'")
    if not am.get("enabled"):
        external = policy.get("_meta", {}).get("external_automerge", {})
        if external.get("detected"):
            return True, (
                f"this repo's own CI automation ({external.get('workflow_file')}) handles "
                "merging; no agent or human action needed")
        return False, "automerge disabled by policy"
    order = ["low", "medium", "high", "critical"]
    if order.index(risk) > order.index(am.get("max_risk", "low")):
        return False, f"risk {risk} exceeds policy max_risk {am.get('max_risk')}"
    allowed = am.get("target_branches") or []
    if allowed and target_branch not in allowed:
        return False, f"target branch {target_branch} not in {allowed}"
    return True, "eligible (pending live CI + review checks)"


def automerge_labels(eligible: bool, risk: str) -> List[str]:
    """GitHub labels encoding `automerge_eligible()`'s verdict on a PR.

    A repo's own CI (e.g. `gh pr merge --auto` in `auto-merge.yml`) cannot call
    this Python function directly, so the verdict has to reach it as PR
    metadata instead. `go:risk-<level>` is always applied; `go:no-automerge`
    is added only when ineligible, so a workflow can gate on label presence
    rather than absence (fail-open by default, block only on the explicit
    signal). Labels must pre-exist in the repo — `gh pr create --label`
    errors on an unknown label name.
    """
    labels = [f"go:risk-{risk}"]
    if not eligible:
        labels.append("go:no-automerge")
    return labels


def merge_method_for_branch(policy: Dict[str, Any], target_branch: str) -> Optional[str]:
    """Return the configured `merge_method_by_base` override for `target_branch`.

    None means "no override" — the caller (verify.py's `_detect_merge_method()`)
    falls back to its own repo-wide GitHub-settings query.
    """
    mapping = policy.get("merge_method_by_base") or {}
    method = mapping.get(target_branch)
    return method if method in VALID_MERGE_METHODS else None


def resolve_post_merge_smoke_cmd(policy: Dict[str, Any]) -> Optional[str]:
    """Resolve verify.py's cumulative post-merge gate command.

    `post_merge_smoke_cmd` wins; `integrate_smoke_cmd` is the fallback (mirrors
    `pre_pr_gate.resolve_cmd()`'s precedence pattern for its own two keys).
    A repo with neither key set resolves to None — the gate is skipped
    entirely, identical to pre-existing behavior.
    """
    for key in ("post_merge_smoke_cmd", "integrate_smoke_cmd"):
        value = policy.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--check-automerge", action="store_true",
        help="print the deterministic automerge_eligible() decision instead of the policy")
    p.add_argument("--risk", default="low", choices=("low", "medium", "high", "critical"))
    p.add_argument("--gates", default="",
                    help="comma-separated classifier gates, e.g. never_automerge,require_human_approval")
    p.add_argument("--target-branch", default="main")
    p.add_argument("--route", default=None,
                    help="classified route letter, for --check-automerge's "
                         "require_human_routes check")
    p.add_argument("--changed-paths", default="",
                    help="comma-separated changed paths, for --check-automerge's "
                         "protected_paths check")
    p.add_argument(
        "--merge-method-for-branch", default=None, metavar="BRANCH",
        help="print the merge_method_by_base override for BRANCH (null if unset) "
             "instead of the policy")
    p.add_argument(
        "--resolve-routing", default=None, metavar="ROUTE:RISK",
        help="print the resolve_routing() result for ROUTE:RISK (e.g. B:medium) "
             "instead of the policy")
    args = p.parse_args(argv)
    policy = load_policy(Path(args.repo))
    if args.resolve_routing is not None:
        route, _, risk = args.resolve_routing.partition(":")
        print(json.dumps(resolve_routing(policy, route, risk), indent=2))
        return 0
    if args.check_automerge:
        gates = [g for g in args.gates.split(",") if g]
        changed_paths = [p for p in args.changed_paths.split(",") if p]
        eligible, reason = automerge_eligible(
            policy, args.risk, gates, args.target_branch,
            route=args.route, changed_paths=changed_paths or None)
        labels = automerge_labels(eligible, args.risk)
        print(json.dumps({"eligible": eligible, "reason": reason, "labels": labels}, indent=2))
        return 0
    if args.merge_method_for_branch is not None:
        method = merge_method_for_branch(policy, args.merge_method_for_branch)
        print(json.dumps({"merge_method": method}, indent=2))
        return 0
    print(json.dumps(_json_safe(policy), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
