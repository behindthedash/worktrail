## Why

`dashboard.py`'s `_task_files_are_shipped()` classifies a pending task as "shipped" whenever its
declared files are git-tracked on the base branch and present on disk. That check is correct only
for a task that creates a brand-new file. For the common case of a task that *modifies* a
pre-existing tracked file, the file was already tracked before the task ever started, so the
current check is a false positive: it silently marks unimplemented work as complete. Confirmed
live 2026-08-29 against this repo's own `openspec/changes/worker-dispatch-identity-env-var`: the
dashboard classified `stage: stale-bookkeeping` with `next_action: confirm & close` for all 11
tasks, but a grep confirmed zero references to the feature's intended symbols anywhere in the repo
and `git log` showed only the change's own creation commit — no implementation commits. Left
unfixed, this false positive tells an operator to close out a change that was never implemented.

## What Changes

- Strengthen `_task_files_are_shipped()`'s shipped criterion from "file exists, is git-tracked,
  and is present on disk" to "file exists, is git-tracked, present on disk, AND shows git evidence
  of having changed at or after the task/change's own creation" — i.e. the file's most recent
  commit timestamp is not older than the oldest commit that introduced the task/change's own
  directory (`docs/specs/<id>/` for devkit, `openspec/changes/<slug>/` for OpenSpec). A file whose
  most recent commit predates that baseline was already there before the task existed and is
  untouched evidence, not shipped evidence.
- Preserve the brand-new-file case: a file with no commit history before the baseline (or whose
  only commits land at/after it) still counts as shipped, matching today's behavior for newly
  created files and for git-renamed files (the destination path's own history starts at the
  rename, which is itself a post-baseline event).
- Apply the strengthened criterion uniformly to every caller of `_task_files_are_shipped`:
  `_pending_impl_stale`, `_pending_tail_stale`, `_pending_openspec_stale` (all in `dashboard.py`),
  and `check_spec_collision.py`'s `verify()` (which reuses the same helper to confirm an
  already-`Implemented` collision candidate's artifacts against that candidate's own creation
  baseline).
- Update the `openspec-stale-bookkeeping-detection` spec's "A pending task is stale only when its
  cached file scope is fully shipped" requirement to describe the content-evidence bar instead of
  mere existence+tracking.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `openspec-stale-bookkeeping-detection`: the "fully shipped" requirement now requires git
  evidence that a declared file's content changed at or after the change's own creation, not
  merely that the file exists and is git-tracked.

## Impact

- `src/worktrail/router/dashboard.py`: `_task_files_are_shipped` gains a creation-baseline
  parameter and a content-evidence check; `_pending_impl_stale`, `_pending_tail_stale`, and
  `_pending_openspec_stale` each compute and pass that baseline for their own spec/change
  directory.
- `src/worktrail/router/check_spec_collision.py`: `verify()` computes and passes the candidate
  spec's own creation baseline to the shared helper.
- `tests/router/test_dashboard.py`: existing `StaleBookkeeping` / `OpenSpecStaleBookkeeping`
  fixtures start committing their spec/change directory at creation time (previously left
  uncommitted) so a creation baseline exists; new cases cover an unchanged pre-existing file, a
  genuinely new file, and a pre-existing file modified after creation.
- No change to `check_spec_collision.py`'s existing test fixtures: they already commit the
  candidate spec's own doc in the same commit as the shipped file, which the new
  at-or-after-baseline comparison still counts as shipped.
