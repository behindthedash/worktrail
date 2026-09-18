## Why

`worktrail-run-record sweep-orphans` closes only records whose liveness classifies as
`confirmed_orphan` (stale heartbeat AND a bound detached owner reporting `exited`/`gone`).
Every other stale record lands in `skipped_unknown_owner` and is never closed
(`src/worktrail/router/run_record.py:2291`). A dispatch that dies right after
`worktrail-run-record start` -- before any worktree, pid, or detached-owner binding is
written -- therefore stays open forever: it has no owner to probe, its heartbeat never
advanced past `route_selected`, and nothing else ever terminalizes it. The devops
orphan-sweep cron (`worktrail-run-record-orphan-sweep.sh`) reports these daily and
bridge-health-guard's `dead_dispatch` check re-fires on the same records every day until an
operator hand-runs `finish --status failed_recoverable`. Verified 2026-09-18 on two such
records (`go-20260918-031610`, `go-20260917-095919`), both `skipped_unknown_owner`.
(Work-queue brief `20260918-151400-worktrail-run-record-sweep-orphans`.)

## What Changes

- `sweep-orphans` gains an opt-in `--unknown-owner-ttl-seconds N` flag. When set, a
  non-terminal record classified `unknown_owner` is closed (via the same `finish` path and
  `--status` as confirmed orphans) only when **all** of the following hold: its heartbeat
  age is parsable and greater than `N`; it has no `worktree`; its `files_changed` list is
  empty; and it has no `pull_request`. The auto-reconciled `merge_result` note names
  `reconciliation=unknown_owner`, the heartbeat age, and the expired TTL so the audit
  trail distinguishes it from a confirmed-orphan close.
- Unknown-owner records younger than `N`, or carrying any of a worktree, changed files, or
  a PR, stay in `skipped_unknown_owner` exactly as today. Records with no parsable
  heartbeat (`no_heartbeat`, `unparsable_updated_at`) are also still skipped -- there is no
  age to compare against the TTL.
- Without the flag (default), behavior is unchanged: unknown-owner records are never
  closed automatically. The devops cron opts in by passing a long TTL (24h, well past
  bridge-health-guard's 2h `DEAD_DISPATCH_STALE_S`).
- The subcommand docstring's "never closed automatically" wording is updated to describe
  the bounded policy.

## Capabilities

### New Capabilities

### Modified Capabilities
- `run-record-liveness-reconciliation`: orphan sweeping may close an unknown-owner record
  once its heartbeat has exceeded an explicit, opt-in long TTL and the record shows no
  work product; the "requires confirmed dead-owner evidence" rule is narrowed to that
  bounded exception.

## Impact

- `src/worktrail/router/run_record.py` (`_sweep_orphans_repo_dir`, the `sweep-orphans`
  argparse block, the module docstring's `sweep-orphans` entry).
- `tests/router/test_run_record.py` (new sweep tests for the TTL, the work-product guards,
  the no-heartbeat case, the default-off case, dry-run, and the audit note).
- Additive CLI flag; JSON summary keys are unchanged (expired unknown-owner records are
  reported under `closed`). Existing callers that omit the flag see no change.
- Follow-ups outside this repo, not part of this change: the devops cron wrapper should
  pass `--unknown-owner-ttl-seconds 86400` once this ships, and the now-obsolete
  work-queue brief `20260918-084313-bridge-health-guard-dead_dispatch` can be closed.
