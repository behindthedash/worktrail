---
name: drain
description: Unattended queue-draining loop — fresh-context one-shot spawning and stop conditions for src/worktrail/drain
triggers:
  files:
    - src/worktrail/drain/**
  keywords:
    - drain
    - queue_empty
    - circuit_breaker
    - capacity_gated
    - no_pick
    - fallback-agent
---

You are working on **worktrail's queue-drain driver**: repeatedly spawning fresh-context headless
one-shots against the router until the work queue empties or a stop condition fires.

## Domain purpose
Each iteration spawns exactly ONE headless agent CLI process (default `claude -p "worktrail-go
auto"`), waits for it to exit, classifies the outcome from the newest run record under the runs
dir, then re-checks the queue. A fresh process per iteration means fresh context by
construction — nothing accumulates across iterations.

## Business rules / invariants
- **The prompt is `"worktrail-go auto"`, never `"/go auto"`.** `worktrail-go` has never been a
  registered slash command — only a skill. Claude Code's CLI fails immediately on an
  unrecognized `/go` ("Unknown command... Did you mean /goal?") but exits 0, which this module's
  own `no_pick` classification then reads as "nothing was eligible to claim" rather than "the
  one-shot never even started." Using the wrong prompt form silently no-ops every drain
  iteration that uses `agent=claude`.
- **Seven distinct stop conditions, each printed, never silent**: `queue_empty`, `no_pick`,
  `capacity_gated` (every configured agent — primary + `--fallback-agent` chain — is gated),
  `circuit_breaker` (N consecutive failed iterations, default 2), `max_items`,
  `budget_exhausted`, `lock_held` (another drain already owns this queue).
- **`completed_awaiting_human_approval` is a gate working, not a stall** — the driver notes the
  pending PR and continues to the next iteration rather than treating it as failure.
- **A timeout (exit 124) with a PR already recorded classifies as `timeout_after_pr`, not
  `failed`.** The substantive work succeeded and only post-PR wrap-up was still running when the
  timeout fired, so it does not count toward `circuit_breaker`. A timeout with no PR remains a
  plain `failed` iteration.
- **A record-less iteration that classifies as an account-level failure**
  (`agent_capacity.classify_failure`: auth/billing, the latter also covering "usage limit"/
  "session limit" wording) is `blocked`, not `failed` — it does not count toward
  `circuit_breaker`, and persists a bare-agent-keyed capacity gate with a `retry_after` parsed
  from the notice when present, else the class's generic cooldown.
- **Agent selection re-runs every iteration** in fixed priority order (`[--agent] +
  --fallback-agent...`, `select_available_agent`) — a gated primary is skipped in favor of a
  fallback automatically and picked back up automatically once its gate expires. Only
  `capacity_gated` (every configured agent gated at once) actually stops the drain.
- **Applies the same `go:risk-*` PR label correction as the interactive path**
  (`pr_labels.ensure_pr_risk_label`) after a spawned one-shot's own `gh pr create`, since neither
  Codex/OpenCode nor a headless `claude -p` session reliably runs the interactive PreToolUse
  label-enforcement hook.

## Critical files
- `drain/drain.py` — the whole driver: iteration loop, stop-condition classification, agent
  fallback selection

---
**Last Updated:** 2026-08-16
