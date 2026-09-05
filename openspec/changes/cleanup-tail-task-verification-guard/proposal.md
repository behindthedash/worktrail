## Why

`TAIL_KINDS = {"e2e", "cleanup"}` (`coordinator.py:49`) holds both kinds out of
the parallel fan-out and exempts both from the file-scope requirement
(`compile.py:167`), so `worktrail-compile` treats `[e2e]` and `[cleanup]` as
interchangeable. They are not: at dispatch, `live.py:4429-4438` and
`:5710-5714` short-circuit any task whose status maps to `role ==
dispatch.ROLE_CLEANUP` straight into `cleanup_task_in_python` (`live.py:3723`),
whose own docstring says "a pure state transition: no filesystem write, no
commit". No worker is ever spawned and no prompt is ever built on this path.
`[e2e]` by contrast is spawned normally and actually runs a worker.

Compile's own guidance steers authors into the mismatch: `compile.py:800`
tells an author resolving a no-file-scope gap to "give them a tail kind
(docs/e2e/cleanup)" without saying what distinguishes them, so a task whose
real intent is "run pytest and confirm it's green" gets tagged `[cleanup]`
as readily as `[e2e]`.

Live instance (run `go-20260904-153010`, change
`quarantine-live-merge-recheck-terminal-check-failure-bailout`, now archived):
task 2.1 was authored `- [ ] 2.1 [cleanup] Run PYTHONPATH=src pytest -q and
PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check; confirm
both are green ... and run openspec validate --strict`. It was marked done in
0s: `[16:09:17]   OK 2.1 cleanup   (python) -> done  0s`, with
`cleanup_task_in_python`'s journaled report reading `tests: "none"`. Nothing
ran. A merge commit later resolved the `tasks.md` conflict in main's favor, so
even the checkbox flip was lost — 2.1 stayed unchecked on base. The run exited
`rc=0`. The verification was only actually performed because the closing-out
agent re-ran it by hand afterward; without that, the change would have
archived asserting a verification that never executed.

The open change `tail-dispatch-noop-and-pr-discovery-guard` also touches
`ROLE_CLEANUP`, but its entire scope is `dispatch.py`'s `build_worker_prompt`
— the prompt rendered for a tail task once it reaches a worker. It does not
cover this defect: `live.py`'s short-circuit fires *before* `build_worker_prompt`
is ever called for `role == ROLE_CLEANUP`, so that change's fix cannot address
a task that is never dispatched to a worker in the first place. That change's
own design.md records "Alternative considered: special-case only
ROLE_CLEANUP. Rejected" — it deliberately generalized the prompt fix rather
than the no-spawn short-circuit this change addresses. The two are adjacent
but disjoint and this change makes no edits to that change's files.

## What Changes

- `worktrail-compile`'s plan-shape gate (`parallelism.shape_problems`, the
  same enforcement point that already rejects a too-serial plan or a
  same-file dependency chain) gains a fourth rule: a task tagged `[cleanup]`
  whose title reads as an imperative command to run something (`Run`/`run`
  plus a recognizable test/validation command, or a backticked shell
  fragment) is rejected at compile time, with a message pointing the author
  at `[e2e]` instead. `[e2e]` and `[docs]` tasks are unaffected; the rule
  only fires for `kind: cleanup`, since that is the one tail kind that
  genuinely executes nothing.
- `compile.py`'s scope-gap remediation message (`_print_scope_gap_error`,
  the "give them a tail kind (docs/e2e/cleanup)" line) is reworded to name
  what each tail kind actually does, so an author resolving a compile error
  is steered toward the right one instead of being told the three are
  interchangeable.
- Regression test coverage: a `[cleanup]` task whose body contains an
  imperative run instruction fails compile with a message naming the task
  and suggesting `[e2e]`; a `[cleanup]` task with a genuinely inert body
  (e.g. "remove debug logging"), an `[e2e]` task with the same imperative
  body, and a `[docs]` task all continue to pass.

## Capabilities

### Modified Capabilities
- `compile-plan-shape-gate`: adds a fourth plan-shape rule (cleanup/
  verification-body mismatch) alongside the existing three (serial critical
  path, same-file chain, missing test scope), and reworks the tail-kind
  remediation guidance text in the scope-gap error message.

## Impact

- `src/worktrail/conductor/parallelism.py` (`shape_problems`, new helper for
  the fourth rule)
- `src/worktrail/conductor/compile.py` (`_print_scope_gap_error` guidance
  text only — the gap-detection logic itself is unchanged)
- `tests/conductor/test_parallelism.py` / `tests/conductor/test_compile.py`
- No change to `src/worktrail/orchestrator/live.py`'s `ROLE_CLEANUP`
  short-circuit or `cleanup_task_in_python` itself — deferred (see design.md
  Non-Goals): this change stops the mismatch from being authored, not the
  dispatch behavior of a `[cleanup]` task that already has a correct,
  genuinely-inert body.
- No interaction with `tail-dispatch-noop-and-pr-discovery-guard` — that
  change's `build_worker_prompt` fix and this change's compile-time
  rejection guard disjoint, non-overlapping defects (see Why).
