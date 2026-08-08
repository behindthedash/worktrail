## ADDED Requirements

### Requirement: Non-terminal run records are partitioned into live and stale
`_active_conflicts()` SHALL classify each non-terminal run record matching the
scanned `specification` as **stale** when both hold: (a) its `worktree` field is a
non-empty path that does not exist on disk, and (b) at least one path candidate
extracted from its `files_changed` list resolves as a blob or tree at the scanned
repo's base branch via `git cat-file -e <base_branch>:<path>`, with every extracted
candidate resolving (not merely one). A record failing either condition SHALL be
classified as **live**. `cmd_active_conflicts` SHALL print `{"live": [...],
"stale": [...]}` instead of a flat array.

#### Scenario: Worktree gone and files merged
- **WHEN** a non-terminal record's `worktree` path does not exist on disk, and every
  path candidate from its `files_changed` resolves on the repo's base branch
- **THEN** the record appears in the `stale` partition, not `live`

#### Scenario: Worktree still exists
- **WHEN** a non-terminal record's `worktree` path exists on disk
- **THEN** the record appears in `live`, regardless of `files_changed` content

#### Scenario: Worktree gone but files not yet merged
- **WHEN** a non-terminal record's `worktree` path does not exist on disk, but at
  least one `files_changed` path candidate does not resolve on the base branch (or
  `files_changed` is empty or has no extractable path candidates)
- **THEN** the record appears in `live`, not `stale`

#### Scenario: No worktree field recorded
- **WHEN** a non-terminal record has no `worktree` field, or it is empty/null
- **THEN** the record appears in `live` (the worktree-gone signal cannot be
  evaluated, so staleness is never inferred from `files_changed` alone)

### Requirement: The active-conflicts hard stop blocks only on live conflicts
`#active-conflicts-scan` (and any other caller that treats a non-empty scan result
as a hard stop) SHALL reconcile every entry in the `stale` partition before
evaluating whether to block, and SHALL block only when the `live` partition is
non-empty.

#### Scenario: Only stale conflicts found
- **WHEN** `active-conflicts` for a given `specification` returns an empty `live`
  list and a non-empty `stale` list
- **THEN** the caller reconciles each stale entry and proceeds without blocking

#### Scenario: Live conflict present
- **WHEN** `active-conflicts` returns at least one entry in `live`
- **THEN** the caller reports the live conflict(s) and hard-stops exactly as
  before this change, regardless of what `stale` contains

### Requirement: Reconciling a stale record closes it with an auditable reason
A new `reconcile RUN_PATH --note "..."` subcommand SHALL re-run the staleness test
against the run record at `RUN_PATH` at write time, and if it still classifies as
stale, SHALL close it via the same path `finish --status completed_and_merged`
uses, recording `--merge-result` with the given `--note` (or a default reconciler
message identifying the check as the source) so the record's closure is
distinguishable from a normal session-driven finish.

#### Scenario: Record is still stale at reconcile time
- **WHEN** `reconcile` is invoked against a run record that still satisfies the
  staleness test at the time of the call
- **THEN** the record's `final_status` becomes `completed_and_merged` and its
  `merge_result` documents that it was closed by the staleness check

#### Scenario: Record is no longer stale at reconcile time
- **WHEN** `reconcile` is invoked against a run record whose worktree now exists,
  or whose `files_changed` no longer fully resolves on the base branch (state
  changed between the scan and the reconcile call)
- **THEN** `reconcile` SHALL NOT close the record and SHALL report that it is no
  longer classified as stale
