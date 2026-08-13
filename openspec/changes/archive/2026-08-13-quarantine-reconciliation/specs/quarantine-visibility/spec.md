## MODIFIED Requirements

### Requirement: Detect quarantined orchestrator groups from run journals
The system SHALL provide a `check_repo(repo: Path) -> Dict[str, Any]` function
that scans every `run-*.json` file under `<repo>-worktrees/` (the run-journal
directory convention `dashboard.py`'s `_journal_verify_pending()` already uses)
and returns one finding per group record whose `state == "QUARANTINED"`, after
excluding any finding the quarantine-reconciliation capability auto-resolves
(see that capability's own requirements for the exclusion rules). Each
surviving finding SHALL carry the spec id (derived from the journal filename),
the group name, the group's `pr_url` (empty string if a PR was never opened
for that group), and an `age_days` value computed from the journal file's
modification time.

#### Scenario: Repo has no quarantined groups
- **WHEN** `check_repo(repo)` is run against a repo whose `<repo>-worktrees/`
  directory has no `run-*.json` file with a `QUARANTINED` group entry, or has no
  `run-*.json` files at all
- **THEN** `check_repo()` returns an empty `findings` list for that repo

#### Scenario: Repo has one or more quarantined groups
- **WHEN** `check_repo(repo)` is run against a repo with at least one
  `run-*.json` file containing a `groups` entry with `state: "QUARANTINED"`,
  and reconciliation does not auto-resolve it
- **THEN** `check_repo()` returns one finding per unreconciled quarantined
  group, each naming its spec id, group name, `pr_url`, and `age_days`

#### Scenario: A quarantined group is reconciled
- **WHEN** `check_repo(repo)` finds a `QUARANTINED` group whose work is
  confirmed landed by the quarantine-reconciliation capability
- **THEN** that group does NOT appear in the returned `findings` list, and its
  reconciliation is recorded separately (see quarantine-reconciliation's own
  requirements)
