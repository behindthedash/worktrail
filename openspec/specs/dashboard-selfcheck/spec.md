# dashboard-selfcheck Specification

## Purpose
TBD - created by archiving change dashboard-selfcheck. Update Purpose after archive.
## Requirements
### Requirement: Detect tied-ambiguity spec directories
The system SHALL provide a `check_repo(repo: Path) -> Dict[str, Any]` function
that scans every `docs/specs/*/` directory in a repo and flags a directory
whose spec doc `find_spec_file()`-equivalent candidate set has 2 or more
`.md` files tied with no naming-convention signal (no dated `YYYY-MM-DD--`
prefix, and none named `spec.md`, `SPEC.md`, `brainstorm.md`, or ending in
`-specs.md`/`-spec.md`) — mirroring `dashboard.py`'s `find_spec_file()`
ambiguity rule exactly, so a directory this check flags is exactly a
directory where `find_spec_file()` returns `None` for this reason.

#### Scenario: Spec directory has zero or one unsignaled candidate
- **WHEN** a `docs/specs/<id>/` directory has zero `.md` spec-doc candidates,
  or exactly one candidate with no naming-convention signal, or any number of
  candidates where a dated/named candidate is present
- **THEN** `check_repo()` reports no finding for that directory

#### Scenario: Spec directory has 2+ tied unranked candidates
- **WHEN** a `docs/specs/<id>/` directory has 2 or more `.md` spec-doc
  candidates that all lack a dated prefix and a recognized name
  (`spec.md`/`-specs.md`/`-spec.md`/`brainstorm.md`)
- **THEN** `check_repo()` reports a finding for that directory naming the
  tied candidate files

### Requirement: Cross-repo sweep and CLI, matching sibling self-checks
The system SHALL provide a `sweep(repos_root: Path) -> List[Dict[str, Any]]`
function and a `main()` CLI entry point accepting `--repo`, `--repos-root`,
and `--json`, with the same non-blocking posture, output shape, and exit-code
convention (0 = clean, 1 = findings present) as `policy_selfcheck.py` and
`automerge_selfcheck.py`.

#### Scenario: Single-repo check via CLI
- **WHEN** `dashboard_selfcheck.py --repo <path>` is run against a repo with
  no ambiguous spec directories
- **THEN** the command exits 0 and reports no findings

#### Scenario: Sweep flags an ambiguous repo among several
- **WHEN** `dashboard_selfcheck.py --repos-root <path> --json` is run over
  multiple repos and exactly one has a tied-ambiguity spec directory
- **THEN** the command exits 1 and the JSON output's `flagged` count is 1,
  with that repo's `findings` naming the tied directory and candidate files

