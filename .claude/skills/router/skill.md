---
name: router
description: GO v2 front door internals — policy resolution, run records, resume dashboard, and PR label correction for src/worktrail/router
triggers:
  files:
    - src/worktrail/router/**
  keywords:
    - policy.py
    - go-policy.yaml
    - worktrail-go-policy.yaml
    - run_record
    - dashboard
    - automerge_eligible
    - risk label
    - classify
---

You are working on **worktrail's GO v2 front door**: loading repo policy, classifying free-text
requests into routes, tracking run records, and rendering the resume dashboard.

## Domain purpose
`router/` is what `/go`-style front doors consume to decide what a repo allows (policy), which
route a request maps to (classify), what already happened (run_record, dashboard), and whether a
spawned agent's own `gh pr create` came out correctly labeled (pr_labels). Nothing here spawns
agents or writes task files — that is `orchestrator/`'s job.

## Business rules / invariants
- **`policy.py` uses two YAML parsers on purpose.** Most keys go through `parse_policy_yaml`, a
  flat, stdlib-only, one-nesting-level subset. `routing:` and `add_ons:` are re-parsed with real
  `yaml.safe_load` (`_resolve_routing`, `_resolve_add_ons`) because both need arbitrary nesting
  the flat parser would flatten into siblings of the wrong key. Adding a new deeply-nested policy
  key means adding a matching `_resolve_*` real-YAML path, not extending the flat parser.
- **Policy resolution fails closed.** A malformed `add_ons`/`routing`/`automerge` shape falls back
  to the safe default (`{}` / `None` / disabled) with a warning appended to `meta["warnings"]`,
  never widens autonomy. `automerge.max_risk`, `agent_cli`, `fallback_agent_cli`, `agent_model`,
  `max_workers`, and `pr_pacing_wait_s` are all validated/clamped the same way in `load_policy`.
- **`automerge.enabled: false` does not, by itself, block orchestrator-driven merges.** Only
  `automerge_eligible()` reads that key, and only when an agent follows sdd-workflow's Phase 8
  merge-gate instructions — `orchestrator`'s own `auto_merge()` is a separate code path that
  unconditionally calls `gh pr merge` once CI passes and does not consult this key at all.
- **Run records enforce ten explicit completion states** (`run_record.py` `finish`, §22) — a run
  can never end in vague language. `finish` also code-enforces `no_implementation_without_approval`
  (a route-A run cannot finish on an implementation-completion state without a recorded
  `decisions` entry first) and `pre_pr_gate.py`'s `scope_review_failures()`, unconditional on
  route, exactly once per run regardless of how many group PRs the orchestrator created.
- **`finish` best-effort-applies the `go:risk-*` PR label correction** (`pr_labels.
  ensure_pr_risk_label`) whenever the record carries a `pull_request` — a spawned headless agent's
  raw `gh pr create` is never reachable by the interactive Claude Code PreToolUse
  label-enforcement hook (Codex/OpenCode have no equivalent mechanism at all).
- **PR label writes use the REST endpoint, not `gh pr edit --add-label`.** `gh pr edit`'s GraphQL
  mutation also touches classic-Projects fields and fails outright on a repo/org with a legacy
  Projects (classic) board still attached (confirmed live 2026-08-07). `_current_pr_labels`'s
  read-only `gh pr view` call is unaffected and stays as-is.
- **`dashboard.py` detects spec artifacts by exclusion + content, never one strict filename
  pattern.** Known auxiliary files (`user-request.md`, `decision-log.md`,
  `traceability-matrix.md`, etc.) are named explicitly; any other top-level `*.md` is a candidate
  spec doc, with a dated filename winning when several exist. A `## Clarifications` heading is
  NOT the resolution-gate signal — only ~22/46 real specs carry one; the gate keys on unresolved
  `[NEEDS CLARIFICATION: ...]` markers in the spec body instead.

## Critical files
- `router/policy.py` — `load_policy()`; the single source of truth for a repo's resolved GO policy
- `router/run_record.py` — `finish()`'s ten-state enforcement and its two code-enforced gates
- `router/pr_labels.py` — the one place that issues the `go:risk-*` REST label correction; both
  `drain.py` and sdd-workflow's Phase 8 call into it rather than reimplementing it
- `router/dashboard.py` — pure file inspection (no git, network, or agents); spec lifecycle stage
  and next-action detection

---
**Last Updated:** 2026-08-16
