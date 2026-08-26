## Why

A planned devops-side `PreToolUse` hook (`worktree-conflict-guard.py`) will call
`worktrail-run-record worktree-conflict --dispatch-id <id>` before allowing edits or
git mutations inside a worktree, denying when it reports `conflict: true`. That
subcommand (shipped in PR #735) tells a run's own dispatch apart from a genuine
external one by comparing the caller's `--dispatch-id` against the run record's
stamped `dispatch_id` (set at `worktrail-run-record start --dispatch-id
"$INVOCATION_CONTEXT_DISPATCH_ID"`, per `skills/worktrail-go/SKILL.md`). Headless
worker subprocesses launched by the orchestrator's live-spawn path
(`src/worktrail/orchestrator/live.py` → `spawnlib.spawn_agent`) currently receive no
such identity in their process environment at all — confirmed by grep: no
`dispatch_id`, `DISPATCH_ID`, or matching env assignment exists anywhere in
`live.py` or `spawnlib.py` today. Without it, any `PreToolUse` hook subprocess a
worker's own headless session spawns has no dispatch identity to pass, so the
worktree-conflict check cannot tell the run's own worker apart from an external
actor and would report every one of the orchestrator's own edits as a conflict —
self-blocking the orchestrator's own parallel task execution, a severe regression.

## What Changes

- Add an optional `dispatch_id` parameter to `spawnlib.spawn_agent` (and thread it
  through `spawn_claude_p`), consumed by the existing `build_child_env` helper — the
  single point in the worker-spawn path where every headless worker's (claude,
  codex, opencode) child environment is assembled, mirroring the conditional
  `WORKTRAIL_SKILL_DISPATCH_DEPTH` passthrough already there. When a `dispatch_id`
  is supplied, it is exported into the worker's environment under a new, stable,
  documented name: `WORKTRAIL_DISPATCH_ID`. When not supplied, no env var is set —
  identical to today's behavior.
- Add a matching `dispatch_id` constructor parameter to `LiveSpawn`, stored and
  threaded into its `spawn_agent`/`spawn_claude_p` call so a run-level identity
  applies to every task worker `LiveSpawn.__call__` spawns.
- Add a `--dispatch-id` CLI argument to `worktrail-live full-real` (the production
  worker-spawn entry point real orchestrator runs use, per
  `skills/worktrail-go/references/subagent-prompts.md`), threaded through
  `full_real()` into the `LiveSpawn` it constructs.
- Add pytest coverage asserting `WORKTRAIL_DISPATCH_ID` is present with the correct
  value in a spawned worker's environment when a `dispatch_id` is supplied, and
  absent when it is not.

**Out of scope** (verified, not silently dropped — see design.md Non-Goals):
- The devops-repo `worktree-conflict-guard.py` hook itself (separate repo, separate
  change).
- Updating `skills/worktrail-go/references/subagent-prompts.md`'s `full-real`
  invocation line to actually pass `--dispatch-id "$INVOCATION_CONTEXT_DISPATCH_ID"`.
  That line runs inside a dispatched subagent's own prompt text, not the top-level
  `/go` shell session, so wiring it requires tracing how that subagent's prompt
  receives context — a distinct, non-trivial change from the env-plumbing this
  change ships. Until that follow-up lands, `WORKTRAIL_DISPATCH_ID` is available for
  a caller to set but no shipped caller sets it yet.

## Capabilities

### New Capabilities
- `worker-dispatch-identity`: the orchestrator's headless-worker spawn path accepts
  an optional run dispatch identity and, when supplied, exports it into every
  spawned worker's process environment under a stable, documented name.

### Modified Capabilities
(none — no existing spec's requirements change)

## Impact

- `src/worktrail/orchestrator/spawnlib.py`: `spawn_agent`, `spawn_claude_p`,
  `build_child_env` (new optional parameter + one new conditional env assignment).
- `src/worktrail/orchestrator/live.py`: `LiveSpawn.__init__`/`__call__`, `full_real()`,
  and the `full-real` argparse subparser + its dispatch in `main()`.
- `tests/orchestrator/` (or the existing spawnlib/live test modules): new coverage
  for the env var's presence/value/absence.
- No change to `src/worktrail/router/run_record.py` or
  `src/worktrail/router/invocation_context.py` — this change only consumes an
  already-generated dispatch id value passed in by a caller; it does not change how
  dispatch ids are generated or how run records are stamped.
