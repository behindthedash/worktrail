## Why

`SpawnResult.exhausted` exists (`src/worktrail/orchestrator/spawnlib.py:116`, set at the three
give-up returns `:1113`, `:1221`, `:1302`/`:1334`) because a spawn that never got a model answer
must not be read as one. `worker-exhaustion-non-result` states that rule for *every* consumer:
"No consumer of the agent-spawn helper SHALL convert an exhausted spawn's output text into a
stored or reported result value."

Only the two brief-lifecycle consumers were actually fixed. `grep -rn 'spawn_agent(' src/
--include=*.py` plus `grep -n '\.exhausted'` over the remaining call sites confirms the gap:
`conductor/compile.py:444` and `:471`, `orchestrator/verify.py:239`, and
`orchestrator/live.py:2635`, `:2958`, `:2976`, `:6482` contain **zero** references to
`.exhausted`. Every one of them reads `result.text` unconditionally.

What each of them does with a provider's "You've hit your usage limit" error stream today:

- **`compile.py`** returns it as the compile worker's final message. `_extract_json()` finds no
  object, so `compile_run_plan()` calls `give_up("compile returned no JSON object; using the
  artifact's own deps")` and the whole run proceeds on a baseline RunPlan -- no inferred file
  scope, no inferred edges, everything serialised behind its neighbours. The note blames the
  model for a bad answer it was never asked to give, and `worktrail-compile` exits on the
  ordinary gap path, so nothing tells an operator (or an unattended drain loop) that the correct
  action is "re-run when capacity returns", not "fix the spec".
- **`verify.py`'s `_make_live_spawn`** returns it as a resolve/ci-fix worker's report-back.
  `_spawn_group_worker()` fails `parse_report_back()` and returns False, which `ensure_mergeable`
  and `wait_and_fix_ci` count as a **strike**. A gated provider therefore burns the group's
  entire 3-strike budget without a single worker running, and the group is quarantined as if
  three workers had tried and failed.
- **`live.py`'s `LiveSpawn.__call__`** returns it as a task worker's report-back, which the
  drive loop treats as an unparseable report-back. That lands on the journal's `retryable` path
  by coincidence of the text being unparseable, not because anything recognised the capacity
  block -- and it still consumes the task's implement/fix attempt budget against a row that
  cannot serve it.

The other two call sites are safe and stay as they are: the research pre-load (`live.py:2635`)
reads only `session_id` and already warns when it is empty, and `smoke()` (`:6482`) reports the
unexpected output and returns False, which is the correct outcome for a connectivity probe.

Nothing enforces the capability today, which is how five call sites were left behind by the
change that introduced the flag. This change closes the audit and adds the guard that keeps it
closed.

## What Changes

- **One shared way to fail closed.** `spawnlib` gains `SpawnExhausted(NoExecutionTarget)` and a
  `raise_if_exhausted(result, *, context)` helper. Subclassing `NoExecutionTarget` is deliberate:
  it is the exception the codebase already means by "nothing could serve this", and
  `capacity-crash-resume-retryable` explicitly maps "`NoExecutionTarget` (and any future
  capacity-exhaustion subclass of it)" to a `retryable` journal entry, so the two changes compose
  without either depending on the other landing first.
- **Compile reports a capacity block instead of blaming the model.** Both compile spawn helpers
  raise; `compile_run_plan()`'s existing catch-all degrades to the baseline plan as it does now
  but carries a distinct capacity note, and `worktrail-compile` exits **2** with a
  `blocked_no_capacity:` stderr line -- the same shape the triage entrypoints already use.
- **A capacity block is not a strike.** `_spawn_group_worker()` distinguishes an exhausted spawn
  from a failed worker: it logs the capacity block and aborts the resolve / ci-fix loop with the
  group left for a later run, rather than consuming strikes and quarantining a group no worker
  ever touched.
- **A task worker's capacity block is explicit.** `LiveSpawn.__call__` raises `SpawnExhausted`
  instead of handing the provider's error stream to the drive loop's report-back parser, so the
  task's attempt budget is not spent and the crash classifier sees a capacity exception rather
  than an unparseable string.
- **The audit stays done.** A new AST-based enforcement test walks every `spawn_agent` /
  `spawn_claude_p` call site in `src/` and fails unless the result is either checked for
  `exhausted` (directly or via `raise_if_exhausted`) or listed in an in-test allowlist with a
  written rationale, so a new caller cannot silently reintroduce the defect.

## Capabilities

### Modified Capabilities

- `worker-exhaustion-non-result`: the "never used as a result value" rule gains the remaining
  callers' specific outcomes -- a capacity-blocked compile is reported as such rather than as a
  rejected model answer, a capacity-blocked group worker consumes no strike, a capacity-blocked
  task worker raises rather than returning an error stream as a report-back -- plus a standing
  requirement that every call site is covered.

## Impact

- **Code**: `src/worktrail/orchestrator/spawnlib.py` (new exception + helper),
  `src/worktrail/conductor/compile.py` (`_default_spawn`, `_spawn_with_explicit_cell`,
  `compile_run_plan`'s give-up note, `main()`), `src/worktrail/orchestrator/verify.py`
  (`_make_live_spawn`, `_spawn_group_worker`, the two strike loops),
  `src/worktrail/orchestrator/live.py` (`LiveSpawn.__call__`).
- **Tests**: `tests/orchestrator/test_spawn_exhausted_callers.py` (new, the AST audit guard),
  plus new per-caller suites for compile, verify, and live.
- **Non-goals**: changing when a cell is gated, how long a gate lasts, or the retry/hop budget;
  automatically re-running anything once capacity returns; the journal `terminal_status` mapping
  itself (`capacity-crash-resume-retryable`'s scope -- this change only makes sure a capacity
  exception is what reaches it); the already-fixed triage-evaluator and handoff-slug consumers.
