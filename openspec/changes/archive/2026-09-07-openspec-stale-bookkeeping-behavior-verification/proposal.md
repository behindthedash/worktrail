## Why

Work-queue brief `20260907-151400-stale-bookkeeping-false-positive-on` reported the
dashboard routing a genuinely unimplemented OpenSpec change to
`stage: stale-bookkeeping` / "files already merged on base". Root cause confirmed live at
`src/worktrail/router/dashboard.py:711-748` (`_pending_openspec_stale`, HEAD `86287cd9`):
"shipped" is judged purely from cached-RunPlan file scope plus `_git_tracked` and
`_task_files_are_shipped` — a declared file counts as shipped when it is tracked and has any
commit at or after the change directory's creation baseline. Nothing checks that the change's
own claimed behavior is actually present in that file.

For the reported change (`verify-ignore-cancelled-superseded-ci-runs`), the single declared
file `src/worktrail/orchestrator/verify.py` is long-lived and busy: it was tracked and had been
committed since the change directory appeared, for reasons entirely unrelated to the change. The
file-level evidence passed, every pending impl task was classified stale, and `_safe_detect_openspec`
(`dashboard.py:1382-1405`) emitted `stale-bookkeeping` with the "flip task status → completed, no
orchestrator" next action — for work that had not been done. The instance is now resolved (that
change was implemented in `a76d8e52` / #1075 and archived), but the detector gap is not: any
OpenSpec change whose file scope lands on a file that other work touches is exposed to the same
false positive, and the failure mode is silent — it tells the operator to close real work.

## What Changes

- `_pending_openspec_stale` gains a **behavior-evidence gate** on top of the existing file-level
  check. For each candidate task, deterministic identifier evidence is extracted from the task's
  own text (backtick-quoted code-like tokens, excluding path-like tokens and the task's own
  declared file paths). A task whose extracted identifiers are not all present in the current
  on-disk content of its declared files is NOT classified stale, even when every declared file
  passes the file-level check. No model call, no network — same cache-only discipline the
  capability already requires.
- Tasks from which no identifier evidence can be extracted keep today's file-only classification
  (the capability keeps its value for changes that name no symbols), but the change is reported
  with an explicit evidence level so the operator is not told "already merged" on file evidence
  alone: `_safe_detect_openspec` gains a `stale_evidence` field (`"behavior"` when every stale
  task was symbol-verified, `"files-only"` otherwise) and, in the `files-only` case, a
  `next_action` that asks for behavior confirmation rather than asserting the files are merged.
- Regression coverage pinning the brief's exact shape: a pending task naming a symbol absent
  from a tracked, recently-committed declared file is not stale and the change stays
  `ready-to-implement`.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `openspec-stale-bookkeeping-detection`: file-level shipped evidence is no longer sufficient on
  its own — a task with extractable identifier evidence must also show that evidence present in
  its declared files, and the reported stage carries the evidence level it was decided on.

## Impact

- `src/worktrail/router/dashboard.py` (`_pending_openspec_stale`, `_safe_detect_openspec`; new
  identifier-extraction/presence helpers)
- `tests/router/test_dashboard.py` (stale-detection coverage)
- The devkit `_pending_impl_stale` / `_pending_tail_stale` paths are untouched — they read a
  declared `files:` frontmatter contract, not an inferred RunPlan, and were not implicated.
