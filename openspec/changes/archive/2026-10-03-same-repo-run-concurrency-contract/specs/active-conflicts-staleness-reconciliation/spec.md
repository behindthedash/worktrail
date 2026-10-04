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
