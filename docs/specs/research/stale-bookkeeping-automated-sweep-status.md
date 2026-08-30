# Stale-bookkeeping automated sweep — status check

Source brief: `20260809-155831-build-an-automated-stale-bookkeeping` (queued
2026-08-09). Focus: build an automated detector that diffs each task's
declared `files:` against `git ls-tree` on the base branch and
flags/auto-PRs `status: pending` tasks whose files are all already merged
upstream (the "stale bookkeeping" pattern), on a schedule, instead of
requiring manual git-log forensics.

## Verified Observations

- `dashboard.py`'s `_pending_impl_stale()` (devkit task format) and
  `_pending_openspec_stale()` (OpenSpec task format) already implement
  exactly the described diff: a pending task whose declared files are all
  git-tracked and present on the base branch is reported
  `stage="stale-bookkeeping"` with a `next_action` and `stale_task_ids`
  field, rather than `ready-to-implement`. Confirmed by reading
  `src/worktrail/router/dashboard.py` (lines ~429-1050) and by two archived
  OpenSpec changes in this repo:
  - `openspec/changes/archive/2026-08-19-openspec-stale-bookkeeping-detection/`
    — extended the devkit-only check to OpenSpec tasks via the compiled
    `RunPlan` cache.
  - `openspec/changes/archive/2026-08-30-openspec-stale-bookkeeping-detection-shipped-content-evidence/`
    — merged **today** (PR #844, commit `c7549ce`); strengthened the
    "shipped" criterion to require git evidence the file changed at/after
    the task's own creation baseline, closing a false-positive gap.
- `worktrail-go`'s own Phase 2 `close-stale` dispatch action already
  consumes this detection to confirm and land a closing PR when a human (or
  an interactive `/go` run) picks a `stage="stale-bookkeeping"` item from
  the dashboard.
- A **separate**, already-scheduled mechanism exists for a related but
  distinct drift class: `worktrail-spec-sync-sweep`
  (`src/worktrail/router/spec_sync_sweep.py` +
  `spec_sync_sweep_checkbox_check.py`), wired to a weekly cron
  (`~/bin/spec-sync-sweep.sh`, Monday 06:00, per
  `~/projects/devops/docs/ops/spec-sync-sweep.md`). It sweeps every repo
  under `~/projects` for (a) spec-sync drift and (b) checkbox-completion
  drift (`status: completed` frontmatter with unticked body checkboxes —
  the **inverse** pattern from this brief), filing one dedup'd Drift Brief
  per repo per drift class into the work queue.
- `worktrail-spec-sync-sweep` / `spec_sync_sweep.py` contains **no** call
  into `_pending_impl_stale`, `_pending_openspec_stale`, or any equivalent
  check (`grep` for those symbols across `spec_sync_sweep*.py` returns no
  hits). No cron entry, GitHub Actions workflow, or other scheduler in this
  workspace invokes the stale-bookkeeping detector outside of an
  interactively-run `/go` dashboard scan.
- The brief's cited instances (specs 046/075/080/092/053, `TASK-CHG-*.md`)
  are not present in this repo's own `docs/specs/` (`find` for those
  numbers returns nothing) — that pattern was observed in a different
  repo's spec tree (this repo's specs use OpenSpec `changes/`, not
  numbered `docs/specs/<NNN>-slug>` directories for anything this recent).

## Unknowns / Missing Evidence

- Whether the specific spec instances named in the brief (046/075/080/092/
  053) have since been closed out manually or already self-resolved via the
  now-shipped dashboard detection the next time someone ran `/go` against
  their owning repo. Not checked — that repo was not identified in the
  brief (`repo: null`) and is out of scope for this note.

## Confirmed Root Cause

Not a defect — a genuine capability gap, confirmed by evidence above: the
diff-and-flag detection this brief asks for already exists and has been
actively hardened as recently as today, but it only runs when a human (or
an unattended `drain`/`auto` run) triggers an interactive `/go` dashboard
scan against a given repo. There is no scheduled sweep and no
auto-PR/auto-file path independent of that trigger — the "recurring
manual-investigation tax" the brief describes is reduced (detection no
longer requires git-log forensics once someone looks) but not eliminated
(nobody is prompted to look).

## Recommendation

Extend `worktrail-spec-sync-sweep` with a third, independent check —
mirroring `spec_sync_sweep_checkbox_check.py`'s shape — that calls the
existing `_pending_impl_stale` / `_pending_openspec_stale` machinery across
every repo with a `docs/specs/` or `openspec/` tree and files a dedup'd
Drift Brief (or opens a closing PR directly, matching the `close-stale`
dispatch action's existing behavior) per repo when it finds stale-pending
tasks. This reuses the already-shipped detection and the already-scheduled
weekly cron infrastructure rather than building either from scratch.

Given this changes production router/sweep behavior with its own OpenSpec
spec (`openspec-stale-bookkeeping-detection`) already owning the detection
requirement, propose it as a Route G (spec change) or Route D (feature)
via `/opsx:propose` against that existing spec, rather than a direct
patch — matching how every prior change to this detector was made (see the
archived changes cited above). Scope is comparable to the original
checkbox-drift-sweep addition (new check module, brief-filing/dedup
wiring, tests, docs, devops cron update) — not a small inline patch, so
this note recommends a follow-up rather than continuing inline.

**Recommended next route: G** (or D if scoped as a new sweep feature
rather than a spec amendment) — extend `worktrail-spec-sync-sweep` to add
a stale-bookkeeping check reusing the shipped dashboard detection, and wire
it into the existing weekly cron.
