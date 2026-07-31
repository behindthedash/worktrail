# Investigation: wire worktrail-plan-audit's compile-accuracy signal into verify.py

## Verified Observations

- `plan_audit.audit_plan` (`src/worktrail/conductor/plan_audit.py:70-106`) computes, per task,
  `undeclared = actual - declared` (files touched but not in the compiled RunPlan's file scope)
  and `unused = declared - actual`. It is a standalone, manually-invoked forensic tool — PR #62
  deliberately did not wire it into the live dispatch loop.
- `Verifier._forbidden_paths_touched` (`src/worktrail/orchestrator/verify.py:525-567`) already
  computes, for every real run, per PR group: `touched` (`git diff --name-only pre_sha..gb`) and
  `declared` (`self.declared_files[group_name]`). This is the same shape of data plan_audit.py
  needs, already materialized on the hot path, at group granularity rather than task granularity.
- `self.log` on `Verifier` is an injected callable (default `print`), already used elsewhere in
  the class for non-blocking diagnostic output (e.g. the auto-merge evidence log at
  `verify.py:512-516`).

## Unknowns / Missing Evidence

- None material to this change. The wiring point, the data already available, and the exact
  signal to surface were all fully specified by the handoff brief and confirmed by reading both
  files before editing.

## Hypotheses

Not applicable — this was not a defect investigation. The brief requested a scoped
instrumentation addition with a fully specified mechanism (reuse `touched`/`declared` already
computed in `_forbidden_paths_touched`, log the `touched - declared` set). Route I's allowance
for "minimal diagnostics (logging/asserts/repro tests)" covers this directly.

## Confirmed Root Cause

Not applicable (no defect). Confirmed instead: `_forbidden_paths_touched` already had every
input plan_audit.py needs to compute the same undeclared-file signal, at zero extra git calls,
for every real run instead of only on manual invocation.

## Change Made

Added a log-only branch in `_forbidden_paths_touched` (`verify.py:562-572`): when the group has
declared files, compute `set(touched) - declared` and, if non-empty, `self.log()` it prefixed
`compile-accuracy:`. The deny-list return value is untouched — verified by
`test_touched_not_declared_is_logged_but_does_not_change_result`,
`test_no_undeclared_files_logs_nothing`, and
`test_no_declared_files_at_all_skips_the_compile_accuracy_check` in
`tests/orchestrator/test_verify.py`.

## Recommended Next Route

None required — the requested wiring is complete and merged via this run (Route I, diagnostics
only, no further route needed). The brief itself names a distinct, larger follow-up ("aggregate
the logged mismatches across runs to actually measure whether the new compile.py PROMPT wording
reduced the under-reporting rate") — that is real future work, not part of this change's scope,
and requires the signal from this change to exist in production logs first before it can be
attempted.
