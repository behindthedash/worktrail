## Why

`journal_path_for()` keys a run journal only by the trailing path component of
`--spec` (`Path(spec_rel.rstrip('/')).name`), and `reconcile_from_journal()`
matches journal entries to the current task list purely by `task["id"]`, with
no check that the journal actually belongs to the `--spec` path passed in.
Devkit-format specs with `changes/<slug>` subdirectories generate generic,
sequence-based task ids (`TASK-1`, `TASK-2`, ...) independently per spec/change
directory, so two unrelated specs or changes can produce a journal file whose
trailing name and task ids both collide. A `full-real` invocation then silently
resumes the wrong journal onto the current task set instead of erroring or
warning. This was observed directly: pointing `--spec` at a base spec folder
resumed an unrelated, already-terminal 14-task journal from a different
change — harmless only because that journal happened to be fully done. On a
repo/change where the wrong journal still has pending work, this would fan out
the wrong tasks against the wrong branch with no signal to the operator.

## What Changes

- Add a guard that cross-checks a resumed journal's task ids against the task
  ids actually declared under the given `--spec` path before reconciling.
- On a mismatch, block the run (or warn, per the chosen enforcement level —
  resolved in design.md) instead of silently reconciling foreign entries onto
  the current task set.
- Surface the specific mismatched task ids and both spec paths (the path the
  journal was written for vs. the path just requested) so the operator can
  tell precheck/full-real apart from a legitimate resume.

## Capabilities

### New Capabilities
- `spec-path-journal-guard`: validates that a resumed run journal's task ids
  are a subset of the task ids declared under the `--spec` path currently
  being run, before any journal entry is reconciled onto in-memory task state.

### Modified Capabilities
(none — no existing `openspec/specs/` capability covers journal resume
validation)

## Impact

- `src/worktrail/orchestrator/live.py`: the resume block in `_full_real_inner`
  (around the `if resume and Path(journal_path).exists():` branch) and
  possibly `reconcile_from_journal()` itself.
- `worktrail-live precheck` (`#precheck-gate`) and/or `stale-spec-check` are
  candidate alternate/additional locations per the original brief — design.md
  resolves which one(s) actually own the check.
- No change to `journal_path_for()`'s naming scheme itself — this proposal
  adds validation, not a rename/relocation of journal files.
- Existing single-spec runs (the common case, where the journal genuinely
  belongs to the current `--spec`) are unaffected: the new check is only
  observable when task ids diverge.
