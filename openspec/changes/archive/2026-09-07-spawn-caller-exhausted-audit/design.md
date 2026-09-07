## Context

`worker-exhaustion-non-result` was written as a general rule but implemented for two consumers.
The signal (`SpawnResult.exhausted` / `failure_class`) is additive with safe defaults precisely
so existing callers keep compiling -- which also means a caller that ignores it looks fine and
behaves wrong. Five call sites ignore it; three of them turn the provider's error stream into a
decision.

## Goals / Non-Goals

**Goals**
- Every `spawn_agent` / `spawn_claude_p` call site in `src/` either handles exhaustion or is
  recorded as deliberately exempt with a reason.
- A capacity block costs no budget that exists to bound *worker* failure: not a verify strike,
  not a task attempt.
- The operator-facing outcome names capacity, not a bad model answer.

**Non-Goals**
- Gating policy, cooldowns, hop order, retry budgets.
- Re-running compile/verify/a task automatically once capacity returns.
- The journal `terminal_status` mapping (`capacity-crash-resume-retryable`).

## Decisions

### D1: An exception, not a per-caller flag check

The archived change chose fields over an exception (D1 there) so `spawn_agent`'s contract stayed
unchanged for every existing caller. That was right then. For the callers fixed here the
opposite is right: each of them sits behind a helper that returns a bare `str`
(`_default_spawn -> str`, `verify._make_live_spawn`'s `spawn(prompt, wt) -> str`), so there is no
field to propagate without widening three signatures and every test double that implements them.
`raise_if_exhausted(result, *, context)` at the boundary keeps those signatures intact and makes
the failure impossible to ignore downstream.

### D2: `SpawnExhausted` subclasses `NoExecutionTarget`

Both mean "no cell could serve this spawn"; they differ only in whether the row was found gated
before or after attempting. Callers that already catch `NoExecutionTarget` -- and
`capacity-crash-resume-retryable`'s classifier, which names "any future capacity-exhaustion
subclass of it" -- get correct behaviour with no coordination between the two changes, in either
merge order. It carries `failure_class` and a `context` string naming the call site.

### D3: Compile degrades, but says why, and the CLI exits 2

`compile_run_plan()` already wraps the spawn in a catch-all whose contract is "a failed compile
must not fail the run" -- an orchestrator run on a baseline plan is slow, not wrong, and that
stays true for a capacity block. So the raise is caught there as any other exception would be;
only the note changes, to a capacity-specific one that does not attribute the degrade to the
model's answer. The `worktrail-compile` CLI is where an operator or drain loop decides what to do
next, so that is where the distinction has to be visible: exit **2** with `blocked_no_capacity:`
on stderr, matching `--evaluate-brief-triage` and `--routing`. Exit 1 keeps its meaning (scope
gaps, ordering collisions, uncovered requirements -- all things a re-run will not fix).

The plan is not cached either way; the existing `give_up()` docstring already establishes that a
give-up must not be inherited by the next attempt, which is exactly the behaviour a transient
capacity block needs.

### D4: Verify aborts the loop instead of spending strikes

`max_strikes` bounds how many times a *worker* may fail to resolve a group. A gated provider
fails all three instantly and identically, and quarantines a group nothing ever attempted. So
`_spawn_group_worker()` lets `SpawnExhausted` propagate to the two loops, which log the capacity
block and return their existing "not mergeable / CI not fixed" outcome without incrementing the
strike counter and without recording a quarantine reason that blames a worker. The group's PR is
left exactly as it was, for the next run.

### D5: Live raises before the report-back parser sees the text

Handing an exhausted result to `parse_report_back()` reaches `retryable` today only because a
provider error message happens not to parse as a report-back -- text the model does not control
deciding a journal classification. `LiveSpawn.__call__` raises after the spawn returns (after the
`served_harness` label correction, which is still useful for the journal), so the drive loop's
existing `_safe_drive` wrapper sees a capacity exception and the task's attempt budget is
untouched.

### D6: The guard is an AST test with an allowlist, not a grep

The repo's convention for "this rule holds at every call site" is an AST-based enforcement test
(see `tests/`'s existing coverage tests). The two legitimately-exempt sites -- the research
pre-load, which reads only `session_id`, and `smoke()`, whose whole job is to report unexpected
output -- are listed in the test with their rationale, so exempting a new site is a visible,
reviewed edit rather than an omission.
