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
import sys
from pathlib import Path

from .policy import _json_safe, load_policy, resolved_routing_file_path

STARTER_ROUTING_YAML = """\
# worktrail machine-wide routing config -- written by `worktrail-routing --init`.
# Governs which provider/model runs which work (agents/tiers/roles/fallback)
# and how unattended drain picks agents (drain). Edit freely; re-run
# `worktrail-routing --init --force` only if you want to discard local edits
# and start over.

# Per-agent default model. default_model_for_agent() resolves against this
# table whenever a spawn doesn't name a model explicitly -- every agent you
# plan to spawn needs an entry here, or that spawn fails loud naming this file.
agents:
  claude:
    default_model: sonnet
  codex:
    default_model: gpt-5.4-mini
  opencode:
    default_model: opencode/deepseek-v4-flash-free

# Ordered fallback chain: if the primary agent for a route/risk is at
# capacity, GO/orchestrator tries these in order. Each entry may be a bare
# agent name or a mapping ({agent_cli, agent_model, effort}).
fallback:
  - claude
  - codex
  - opencode

# Optional role -> agent overrides (e.g. a dedicated reviewer agent/model).
# {} means every role uses the resolved primary agent.
roles: {}

# Optional purpose -> tier map, consulted ahead of task complexity to
# resolve a task's tier (dispatch.agent_for). {} means purpose never
# overrides the complexity-derived tier.
purpose_tiers: {}

# Optional complexity[/domain] -> per-agent {model, effort} tier table, e.g.:
#   tiers:
#     simple:
#       claude: {model: haiku}
# {} means every tier falls back to the resolved primary agent/model.
tiers: {}

# Machine-wide drain (worktrail-drain) defaults -- claude-first per the
# 2026-08-26 operator decision.
drain:
  agent: claude
  fallback_agents:
    - codex
    - opencode
  max_workers: 2
"""


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
    return 0


def _show(repo: Path) -> int:
    policy = load_policy(repo)
    print(json.dumps(_json_safe(policy.get("routing")), indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--init", action="store_true",
        help="write a starter routing.yaml (WORKTRAIL_ROUTING_FILE, else "
             "worktrail_home()/routing.yaml)")
    p.add_argument(
        "--force", action="store_true",
        help="with --init, overwrite an existing routing.yaml")
    p.add_argument(
        "--show", action="store_true",
        help="print the routing resolved for --repo (repo-local routing: "
             "block, else the machine-wide file, else null)")
    p.add_argument(
        "--repo", default=".", help="repo path for --show (default: cwd)")
    args = p.parse_args(argv)
    if args.init:
        return _init(args.force)
    if args.show:
        return _show(Path(args.repo))
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
