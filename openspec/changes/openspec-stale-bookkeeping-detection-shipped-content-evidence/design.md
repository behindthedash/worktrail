## Context

`_task_files_are_shipped(repo, files, tracked)` (`src/worktrail/router/dashboard.py:533`) is the
single shared predicate behind all three stale-bookkeeping detectors
(`_pending_impl_stale`, `_pending_tail_stale`, `_pending_openspec_stale`) and is reused verbatim by
`check_spec_collision.py`'s `verify()`. Today it treats "declared file is git-tracked at the base
checkout and present on disk" as sufficient proof that a task's work shipped. That is only true for
a task whose entire job was to create a new file. See proposal.md - Why for the confirmed
false-positive this produces on a change whose tasks only ever modify pre-existing tracked files.

## Goals / Non-Goals

**Goals:**
- Require git evidence that a declared file's *content* changed at or after the task/change's own
  creation, not merely that the file currently exists and is tracked.
- Apply the fix once, in the shared helper, so all four call sites benefit without divergent logic.
- Keep the fix conservative on uncertainty (no baseline, git failure) in the same direction the
  existing helper already commits to: unprovable stays not-stale, never the reverse.

**Non-Goals:**
- Per-task creation timestamps. Devkit tasks live as individual `TASK-*.md` files that are not
  reliably committed atomically per task in practice; OpenSpec tasks are checklist lines inside one
  shared `tasks.md`, which has no per-line git history at all. A per-change/per-spec-directory
  baseline is the finest granularity available uniformly across both formats.
- Cross-repository clock synchronization guarantees. The multi-repo case (a task's files span a
  primary and sibling checkout) compares each repo's own commit timestamps against one baseline
  timestamp taken from the primary repo. This assumes the two repos' commit clocks are roughly
  consistent, which already held implicitly for every other timestamp-based check in this codebase
  (e.g. `_openspec_delta_drift`).

## Decisions

**Baseline = oldest commit touching the task/change's own directory, expressed as a Unix
timestamp, not a commit hash.** Two designs were considered:

1. *Commit-range membership* (`git rev-list <baseline_commit>..HEAD -- <file>`): requires the
   baseline commit to be reachable from the file's own repository history, which breaks for the
   multi-repo case — a task's declared file can live in a sibling checkout that has no ancestry
   relationship to the primary repo's commit graph at all.
2. *Timestamp comparison* (chosen): `git log -1 --format=%ct -- <file>` in whichever repository
   the file resolves to, compared against a plain integer baseline. Timestamps are comparable
   across unrelated repositories on the same machine, which is exactly the property the multi-repo
   case needs, and collapses to a single, uniform rule:

   > shipped ⇔ the file's most recent commit timestamp ≥ the baseline timestamp

   This one inequality covers all three required scenarios without separate "does it pre-exist"
   and "did it change" branches: a brand-new file's only commit lands at/after the baseline
   (`≥` holds); an untouched pre-existing file's last commit predates the baseline (`≥` fails); a
   modified pre-existing file has a newer commit at/after the baseline (`≥` holds). It also
   preserves the existing rename behavior for free — a renamed file's history under its new path
   starts at the rename commit, which is itself a post-baseline event, so the destination path's
   "most recent commit" is that rename.

**`≥` (at-or-after), not `>` (strictly-after).** A file committed in the very same commit that
created the task/change directory (a legitimate pattern: `check_spec_collision.py`'s existing test
fixtures commit the candidate spec's doc and its shipped artifact together) must still count as
shipped. Using strict `>` would treat that same-commit case as "not evidenced," which is both
wrong (the file demonstrably did not predate the task) and a regression against
`check_spec_collision.py`'s current passing tests.

**Baseline directory per caller:**
- `_pending_impl_stale` / `_pending_tail_stale`: the devkit `spec_dir` passed in.
- `_pending_openspec_stale`: the OpenSpec `change_dir` passed in.
- `check_spec_collision.py`'s `verify()`: the collision candidate's own `spec_dir` — the same
  concept (this spec's own creation point), applied to a different spec than "the current change."

**No baseline ⇒ conservative `False` for every file.** If the task/change directory itself has no
commit history (never committed), there is no reference point at all, so no file can be judged
"at or after" it. This mirrors the helper's existing philosophy (`_git_tracked` returns an empty
set on any git failure, treated as "not tracked") and keeps an unprovable case on the
orchestrator-eligible path rather than silently marking it closed.

**Both new git calls are `functools.lru_cache`d by `(repo, path)`,** matching the existing
`_rename_destinations` cache in the same module — a scan re-evaluates the same directories and
files repeatedly across a single dashboard run.

## Risks / Trade-offs

- [Existing test fixtures for `StaleBookkeeping`/`OpenSpecStaleBookkeeping` never commit their
  spec/change directory to git, so today they have no baseline at all and every stale-detection
  test would flip to "not stale"] → Update the shared `_spec_dir()` / `_change()` test helpers to
  commit the spec doc / proposal+tasks.md immediately after writing them, before any "shipped"
  file is committed. This is a one-line addition per helper and every existing test in both
  classes already calls that helper first, so ordering is preserved automatically.
- [Same-second git commit timestamp resolution could make a "shipped after" commit collide with
  the baseline commit in a fast-running test] → Not a problem for the "shipped" direction (`≥`
  treats a tie as shipped, which is correct). It IS a problem for the new "unchanged pre-existing
  file" test case, which requires the file's last commit to be strictly *before* the baseline;
  that test uses explicit `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` timestamps (the existing
  `_commit_at`-style pattern already used elsewhere in this test file) to guarantee ordering
  instead of relying on real wall-clock sequencing.
- [Multi-repo commit clocks could disagree] → Accepted per Non-Goals; this is the same assumption
  `_openspec_delta_drift` already relies on for cross-artifact timestamp comparisons, so it is not
  a new class of risk for this codebase.

## Migration Plan

No data migration. Pure logic change behind the same public function signatures for the three
`dashboard.py` detectors (an added optional-in-practice-but-always-supplied baseline parameter);
`check_spec_collision.py`'s call site is updated in the same commit. No cached `RunPlan` format
changes. Rollback is a plain revert.
