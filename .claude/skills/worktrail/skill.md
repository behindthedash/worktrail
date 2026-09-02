---
name: worktrail
description: Task orchestration internals — RunPlan safety, AddOn hand-off, task-source abstraction, and frontier scheduling for src/worktrail
triggers:
  files:
    - src/worktrail/conductor/**
    - src/worktrail/orchestrator/**
    - src/worktrail/taskformats/**
    - src/worktrail/addons/**
    - src/worktrail/workqueue/**
    - src/worktrail/router/**
  keywords:
    - RunPlan
    - runnable_frontier
    - TaskSource
    - add_ons
    - AddOn
    - worktree
    - spec_ref
---

You are working on **worktrail's task-orchestration core**: compiling specs/changes into a
schedulable plan, fanning work out across git worktrees, and handing finished work off to a PR.

## Business rules / invariants

- **RunPlan edge-dropping invariant** (`conductor/runplan.py` `apply_to_tasks`): a dependency
  edge may only be dropped if BOTH endpoints carry a non-empty `files` scope. This falls
  directly out of `runnable_frontier` (`orchestrator/coordinator.py`), which treats an empty
  file set as "collides with nothing" — decoupling a scope-less task from its predecessor would
  let it race with unbounded blast radius. Never relax this to "drop if either side has scope."
- **File collision detection normalizes paths** (`coordinator._norm_files`) via
  `os.path.normpath` — `./src/a.ts` and `src/a.ts` must compare equal or two tasks that declare
  the same file run in parallel and collide at integration.
- **Task status vocab is not symmetric**: `"done"` = worker completed in the *current* run
  (branch exists, deliverable); `"completed"` = already integrated in a *prior* run (never
  re-merge). Conflating them re-merges dead branches.
- **`TAIL_KINDS = {"e2e", "cleanup"}`** are always held out of the parallel fan-out and run
  serialized last — a RunPlan can add fields but can never "un-hold-out" a tail task (`kind` is
  sticky in the safe direction).
- **AddOns run after a task's own work, before commit** (`addons/runner.run_addons`,
  called from `orchestrator/integrate.py` and `router/preflight.py`). `install`/`configure`
  failures are swallowed (best-effort priming); `run()` failures propagate. An add-on config'd
  `required: true` turns a `run()` failure into `AddOnFailure`, which blocks/quarantines the PR
  the same way a failed drift/smoke gate does; a non-required failure is logged `WARN` and
  skipped. Never call an `AddOn`'s methods directly outside `addons/runner.py`.
- **An add-on's self-reported `changed` is not trusted for committing** — `_stage_and_commit`
  always re-checks `git diff --cached --quiet` after staging, so an add-on claiming a change
  that turns out identical to HEAD produces no empty commit.
- **`policy["add_ons"]` needs real YAML, not `parse_policy_yaml`'s one-level-nesting subset**
  (`router/policy._resolve_add_ons`) — per-add-on config is itself a nested dict
  (`add_ons: {aspens: {enabled, target, required}}`), and the one-level parser flattens those
  keys up as siblings of the add-on name instead of nesting them. A malformed top-level shape
  falls back to `{}`, never widening the zero-add-ons default.
- **`AspensAddOn.install` never overwrites an `aspens` CLI already on PATH** (`addons/aspens.py`):
  it only runs `npm install -g aspens` when `shutil.which("aspens")` is empty, and still refreshes
  the machine-local freshness marker when the CLI is present. An unconditional install replaced an
  operator's `npm link`ed fork with the registry build (2026-09-01) and reintroduced a destructive
  doc-sync rewrite — keep the PATH check.
- **Adding a new add-on**: implement `AddOn` (`addons/base.py`), register it in
  `addons/resolve.addon_for` — an unresolved name must raise `ValueError`, never silently no-op.

## Critical files
- `conductor/runplan.py` — RunPlan safety rule and `unordered_file_collisions()` assertion
- `orchestrator/coordinator.py` — `runnable_frontier`/`plan_groups`, pure/side-effect-free
- `taskformats/base.py` — `TaskSource` protocol; `orchestrator/` must never construct a
  format-specific task path itself, always go through the active `TaskSource`
- `addons/runner.py` — the only caller of `AddOn.install`/`configure`/`run`

---
**Last Updated:** 2026-09-02
