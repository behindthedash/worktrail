# quarantine-visibility Specification

## Purpose
TBD - created by archiving change quarantined-group-visibility. Update Purpose after archive.
## Requirements
### Requirement: Detect quarantined orchestrator groups from run journals
The system SHALL provide a `check_repo(repo: Path) -> Dict[str, Any]` function
that scans every `run-*.json` file under `<repo>-worktrees/` (the run-journal
directory convention `dashboard.py`'s `_journal_verify_pending()` already uses)
and returns one finding per group record whose `state == "QUARANTINED"`. Each
finding SHALL carry the spec id (derived from the journal filename), the group
name, the group's `pr_url` (empty string if a PR was never opened for that
group), and an `age_days` value computed from the journal file's modification
time.

#### Scenario: Repo has no quarantined groups
- **WHEN** `check_repo(repo)` is run against a repo whose `<repo>-worktrees/`
  directory has no `run-*.json` file with a `QUARANTINED` group entry, or has no
  `run-*.json` files at all
- **THEN** `check_repo()` returns an empty `findings` list for that repo

#### Scenario: Repo has one or more quarantined groups
- **WHEN** `check_repo(repo)` is run against a repo with at least one
  `run-*.json` file containing a `groups` entry with `state: "QUARANTINED"`
- **THEN** `check_repo()` returns one finding per quarantined group, each
  naming its spec id, group name, `pr_url`, and `age_days`

### Requirement: Cross-repo sweep and CLI, matching sibling self-checks
The system SHALL provide a `sweep(repos_root: Path) -> List[Dict[str, Any]]`
function and a `main()` CLI entry point accepting `--repo`, `--repos-root`, and
`--json`, with the same non-blocking posture, output shape, and exit-code
convention (0 = clean, 1 = findings present) as `automerge_selfcheck.py` and
`policy_drift_selfcheck.py`.

#### Scenario: Single-repo check via CLI
- **WHEN** `quarantine_selfcheck.py --repo <path>` is run against a repo with no
  quarantined groups
- **THEN** the command exits 0 and reports no findings

#### Scenario: Sweep flags a repo among several
- **WHEN** `quarantine_selfcheck.py --repos-root <path> --json` is run over
  multiple repos and exactly one has a quarantined group
- **THEN** the command exits 1 and the JSON output's `flagged` count is 1, with
  that repo's `findings` naming the quarantined spec id and group

### Requirement: Dashboard surfaces quarantined-group findings
`worktrail-dashboard`'s `scan_repos()` SHALL call `quarantine_selfcheck.check_repo()`
for each candidate repo and attach the result as a `quarantine_findings` list on
that repo's row, following the same wiring as `policy_findings`,
`automerge_findings`, and `drift_findings`. `render_dashboard()` SHALL render a
non-empty `quarantine_findings` list as one capped summary line (repo name, spec
id, group name, and age in days for up to 4 entries, with a "+N more" suffix
beyond that), with a "→ review" nudge, following the exact rendering pattern
already used for `policy_flags`/`automerge_flags`/`drift_flags`. An empty
`quarantine_findings` list across all repos SHALL produce no additional output
in the rendered dashboard.

#### Scenario: No quarantined groups across any scanned repo
- **WHEN** `render_dashboard()` is called with every repo row's
  `quarantine_findings` empty
- **THEN** the rendered dashboard text is unchanged from today's output (no new
  section appears)

#### Scenario: One repo has quarantined groups
- **WHEN** `render_dashboard()` is called with one repo row carrying a non-empty
  `quarantine_findings` list
- **THEN** the rendered dashboard includes one summary line naming that repo,
  the count of quarantined groups, and a "→ review" nudge, capped at 4 entries
  shown with a "+N more" suffix when there are more
