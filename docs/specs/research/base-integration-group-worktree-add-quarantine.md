# Investigation: base-integration-group `git worktree add -f -B` quarantine (exit 255)

Handoff brief: `20260816-202504-diagnose-the-full-real-orchestrator`. Route I.

## Trigger

`worktrail-live full-real` run `full-1786931027` against `datalena`
(`openspec/changes/james-evidence-fixtures`, Route D, 10 tasks). Journal:
`~/.worktrail/runs/datalena/go-20260816-183529.yaml`, decision:

> "Orchestrator's base integration group quarantined (git worktree add
> collision, exit 255), so all 10 tasks fell back to individual cumulative
> tail PRs (#2300-#2309) instead of one integrated group PR."

This is a second, independently-triggered occurrence of base-group
quarantine (first: brief `20260814-153118`, a PR-discovery-mismatch, fixed
generically downstream by PR #494's N-way tail-PR dedupe). This note
diagnoses the trigger itself, per the brief's ask.

## Verified Observations

- `full-1786931027/base`'s reflog (`datalena/.git/logs/refs/heads/full-1786931027/base`)
  shows exactly one lifecycle: created from `dev` at `1786931076`, then one
  fast-forward merge of `james-evidence-fixtures/1.1` at `1786931077`. No
  further activity on that ref afterward.
- The run's own journal (`datalena-worktrees/run-james-evidence-fixtures.json`)
  records group `base` as `state: QUARANTINED`, `quarantine_reason:
  integration_error`, and group `feature-1` (the other 9 tasks) as
  `QUARANTINED` with reason `dependency_quarantined` — a pure cascade off
  `base`'s failure, not an independent second cause.
- `QUARANTINE_INTEGRATION_ERROR` is written from two structurally different
  places in the code:
  1. `integrate.py`'s own `integrate_one()`, when `_run_integration_smoke()`
     explicitly fails after a successful merge (a real, descriptive smoke
     failure).
  2. `live.py`'s generic `except Exception as exc` wrapper around the entire
     `integrate_one_fn(...)` call (`live.py` ~3843-3868), which quarantines
     the group as `integration_error` for **any** unhandled exception —
     including a `subprocess.CalledProcessError` from
     `integrate.py:_integration_worktree()`'s `git worktree add -f -B` call,
     which runs with the `subprocess.run(..., check=True)` default (via
     `live._git`) and therefore raises on any non-zero exit instead of
     returning a `CompletedProcess`.
  These two origins are indistinguishable in the persisted journal — both
  write the identical string `"integration_error"`. Confirmed as a real
  ambiguity, not merely a note-taking gap.
- **The specific git stderr and exit code that produced this incident's
  quarantine are not recoverable from any file.** `live.py`'s catch-all
  handler builds `quarantined[name] = f"integrate exception: {exc!r}"`,
  which reaches only `print(...)` (stdout of the backgrounded `full-real`
  process) and the in-memory `quarantined` dict — never
  `_write_group_journal()` / `_record_group_fn()`, both of which persist
  only the `quarantine_reason` *category* string, never a detail message.
  The backgrounded process's stdout in turn was captured only by the
  now-gone prior session's ephemeral task-output file, not by any file
  under the datalena or worktrail repos. This was checked directly: no
  `*.log`, run-record decision text, or journal field anywhere on this
  machine holds the original stderr.
- Separately, `subprocess.CalledProcessError.__repr__` (what `{exc!r}` would
  have printed even if it had reached a persisted log) does not include
  `.stderr` — so even the transient stdout capture would have shown only
  `CalledProcessError(255, ['git', '-C', ..., 'worktree', 'add', ...])`,
  not the actual git error text.
- `live.py` already has an established, working pattern for exactly this
  failure shape at the **task** level (`add_stacked_worktree()`'s `_add()`,
  ~line 1670-1683): attempt `git worktree add`, and on failure, run
  `worktree prune` once and retry, with the comment "a prior partial run may
  have left the branch committed (and then had its worktree dir cleaned)."
  On a second failure it raises `WorktreeAddError` with the real (truncated)
  stderr in the message. `integrate.py`'s **group**-level
  `_integration_worktree()` prunes once *before* the single `add` attempt
  but has no retry after a failed `add`, and lets the bare
  `CalledProcessError` propagate uncaught.

## Unknowns / Missing Evidence

- Whether this specific incident's exit-255 came from `_integration_worktree()`'s
  `git worktree add -f -B` call itself, or from a later step inside the same
  `with _integration_worktree(...) as iw:` block (`_write_group_task_status`,
  `addons_runner.run_addons`, `_run_drift_gate`, `smoke_cmd`, or `push`) that
  also happened to raise and fall through the same catch-all. The reflog
  proves `base`'s *first* `worktree add` succeeded (branch created, task 1.1
  merged); nothing rules out a second internal git operation failing before
  the merge loop finished the group's other deliverable tasks or before push.
- The exact git-level cause of the exit-255 itself (stale `.git/worktrees/`
  registration from a crashed prior run, a ref-lock collision from a
  concurrent process, or a transient disk/IO condition) — none of these can
  be distinguished without the original stderr, which is unrecoverable (see
  above).
- Whether a second, concurrent process was mutating `datalena`'s shared
  `.git` registry at `1786931076-77`. No other `go` run record for
  `datalena` overlaps that window (checked `~/.worktrail/runs/datalena/go-*.yaml`
  `started_at`/`completed_at` ranges), but a non-`/go`-dispatched process
  (manual `git worktree` command, a cron sweep) cannot be ruled out from
  present evidence.

## Hypotheses (unverified)

1. Stale `.git/worktrees/` registration left by an earlier interrupted run
   reusing (or colliding with) the integration worktree's registry entry —
   the exact scenario the task-level `_add()` comment already documents and
   already retries around.
2. A ref-lock collision on `refs/heads/full-1786931027/base` (or the
   `.git/worktrees` registry) from a different concurrent git operation
   against the same shared `.git` directory (task-level worktree add/remove
   is not run under `_integration_worktree`'s `git_lock` — it uses no lock
   at all, per `add_stacked_worktree()`).
3. A one-off transient OS/filesystem condition on this WSL host, unrelated to
   worktrail's own locking.

None of these three is confirmed or ruled out by available evidence.

## Recommended Next Route

**Route F**, narrow and low-risk, continued in the same run: apply the
already-proven task-level resilience pattern (prune-and-retry-once,
informative `WorktreeAddError` with real stderr) to the group-level
`_integration_worktree()`, and thread that detail into the persisted journal
for the specific catch-all path that currently discards it. This does not
claim to prove which of the three hypotheses caused this incident — retry
covers hypotheses 1 and 3 regardless of which is real, and the added detail
closes the observability gap that made hypothesis 2 unfalsifiable this time.
Confirmed via direct code reading (existing task-level precedent, exact
control flow of the catch-all handler); not a guess-fix for an unconfirmed
git-level cause.
