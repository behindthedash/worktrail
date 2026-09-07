## Why

`live.py`'s `_safe_drive` wrapper (`src/worktrail/orchestrator/live.py:4612-4626`, duplicated at
`:5851-5865`) treats *every* exception escaping `drive()` identically: it prints
`drive crashed: {e!r}`, sets `task["status"] = "failed"`, and journals
`_journal_failure_entry(task, "drive", ...)`, whose `terminal_status` defaults to `"failed"`
(`:1371-1392`). Nothing in that path inspects the exception type.

Capacity exhaustion arrives through that same path. When every cell in a task's routing row is
gated, `runtime/selection.py` raises `NoExecutionTarget` (`:319` for `select_execution_target`,
`:411` for `select_cell`); `spawnlib.spawn_agent` only catches it around its *hop* selections
(`:1162`, `:1260`) and lets the authoritative one propagate, as does `live.py`'s explicit-model
branch (`:2938`, "Raises NoExecutionTarget here (not caught)"). So a run that merely ran out of
provider capacity records the task exactly as a task whose worker produced a broken commit.

That misclassification is sticky, by design of the replay contract documented at
`live.py:1490-1497`: "replay bakes any non-`retryable` terminal_status back into task status on
every resume". Replay (`:912-918`) `continue`s past a `retryable` entry, leaving the task
`pending` for re-dispatch, but writes `failed` straight onto the task for anything else. A task
that failed only because the provider was gated is therefore permanently `failed` across every
subsequent resume, even once capacity is back -- the only escapes are `--fresh` (discards the
whole journal, re-running already-merged work) or a surgical `worktrail-*` `clear_tasks` call.
The precedent for the correct handling already exists one screen away: an unparseable
implement report-back is journaled `terminal_status="retryable"` (`:4528`, `:5781`) precisely so
resume retries it.

No active change covers this. The nearest, `agent-capacity-gate-liveness-reprobe`, scopes gate
self-healing in `agent_capacity.check()` and a `land_pr` ceiling re-check -- it makes gates lift
sooner, but a task already journaled `failed` by `_safe_drive` stays failed regardless. This
change was not offered as a fold candidate by triage.

## What Changes

- **Classify the crash before journaling it.** A new module-level helper in `live.py` maps a
  drive exception to its terminal status: `NoExecutionTarget` (and any future
  capacity-exhaustion subclass of it) -> `"retryable"`; every other exception -> `"failed"`,
  exactly as today.
- **Both `_safe_drive` wrappers use it** (`live_run_real`'s and the pipeline scheduler's), so the
  behavior does not depend on which entrypoint drove the run.
- **The operator sees the difference.** A capacity-gated crash prints a distinct line naming the
  gated cells from the exception message and stating the task will be re-dispatched on resume,
  instead of the undifferentiated `-- marking failed`.
- **In-run status is unchanged**: the task is still `failed` for the remainder of the current run
  (nothing can serve it, and downstream dependents must not be dispatched into the same gate).
  Only the journal's `terminal_status` changes, which is what resume reads.

## Capabilities

### New Capabilities

- `orchestrator-crash-terminal-classification`: how an exception escaping a task's drive loop is
  mapped to a journal `terminal_status`, so that a capacity-exhaustion crash is recorded as
  retryable (re-dispatched on the next resume) while a genuine task failure stays terminal.

### Modified Capabilities

(none -- no existing capability spec covers `_safe_drive`'s journaling or the replay
terminal-status contract; `grep -rn "terminal_status\|retryable" openspec/specs/` returns
nothing.)

## Impact

- **Code**: `src/worktrail/orchestrator/live.py` (one new helper; both `_safe_drive` bodies).
- **Tests**: `tests/orchestrator/test_capacity_crash_resume.py` (new).
- **Operator surface**: one changed console line for the capacity case; no new flags, env knobs,
  or journal fields (`terminal_status` and `retryable` both already exist).
- **Non-goals**: changing when `NoExecutionTarget` is raised or how capacity gates expire (that
  is `agent-capacity-gate-liveness-reprobe`'s scope); reclassifying any non-capacity exception;
  making a capacity-gated task park and retry *within* the same run; changing `clear_tasks`,
  `--fresh`, or the replay rule itself; touching `skill_dispatch.py:1031`'s own
  `NoExecutionTarget` handling.
