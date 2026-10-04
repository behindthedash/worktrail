## MODIFIED Requirements

### Requirement: Non-terminal run records are partitioned into live and stale

`_active_conflicts()` SHALL classify each non-terminal run record it scans as **stale** when both
hold: (a) its `worktree` field is a non-empty path that does not exist on disk, and (b) at least
one path candidate extracted from its `files_changed` list resolves as a blob or tree at the
scanned repo's base branch via `git cat-file -e <base_branch>:<path>`, with every extracted
candidate resolving (not merely one). A record failing either condition SHALL be classified as
**live**.

The scanned set SHALL be the non-terminal records whose `specification` equals the scanned
specification when one is supplied; when no specification is supplied, every non-terminal record
for the repo SHALL be scanned, regardless of specification. Every entry in either partition
SHALL carry the record's own `specification` value (null or absent when the record has none)
alongside the fields it already carries. `cmd_active_conflicts` SHALL accept `--specification` as
optional and SHALL print `{"live": [...], "stale": [...]}` instead of a flat array.

When the records root `<dir>/<repo.name>` does not exist, the scan SHALL NOT return a silent
all-clear: both partitions SHALL be empty and `warnings` SHALL carry an entry naming that
unresolved records root (the exact `<dir>/<repo.name>` path), so an in-process caller can
distinguish "scanned and found nothing" from "never scanned". `cmd_active_conflicts` SHALL print
that JSON -- the warning included, so machine-readable consumers still see the diagnostic -- and
then exit nonzero on the same condition rather than reporting success. When the records root
exists, the scan's result and exit status SHALL be unchanged: an existing root with no matching
records remains a successful, warning-free empty result.

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

#### Scenario: Repo-wide scan spans specifications

- **WHEN** `active-conflicts --repo R` runs without `--specification` against a repo whose
  run records hold a non-terminal record for spec A, one for `fix:some-slug`, and one with no
  specification
- **THEN** all three are classified -- subject to the live/stale rule -- with each entry
  carrying its own record's `specification` value, including the null for the unset one

#### Scenario: Specification filter still narrows the scan

- **WHEN** `active-conflicts --specification spec-a` runs
- **THEN** only records whose `specification` is `spec-a` are classified, exactly as before
  this change, and every entry still carries that `specification` value

#### Scenario: Unresolved records root is reported, never a silent all-clear

- **WHEN** `_active_conflicts()` scans a records root `<dir>/<repo.name>` that does not exist
- **THEN** both `live` and `stale` are empty and `warnings` contains an entry naming that exact
  `<dir>/<repo.name>` path -- the call returns normally (no exception), so every in-process
  caller (the claim-time scan, the same-repo live-run detection, the quarantine-sweep conflict
  check) sees the unresolved root through the same `warnings` field it already reads

#### Scenario: CLI fails loud on an unresolved records root

- **WHEN** `cmd_active_conflicts`'s records root `<dir>/<repo.name>` does not exist
- **THEN** the command still prints the partitions and the unresolved-root warning as JSON, and
  exits with a nonzero status instead of 0

#### Scenario: Existing records root keeps today's result and exit status

- **WHEN** the records root exists -- including as an existing empty directory with no matching
  records
- **THEN** the partitions, the absence of an unresolved-root warning, and the exit status are
  exactly as before this change (a clean empty result, exit 0)
