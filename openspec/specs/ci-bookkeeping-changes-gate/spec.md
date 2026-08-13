# ci-bookkeeping-changes-gate Specification

## Purpose
Classifies each pull-request diff in worktrail's CI as bookkeeping-only or
code-touching, so a bookkeeping-only PR skips the full `Lint, Test & Build`
suite while the branch ruleset's required status check still resolves green.
## Requirements
### Requirement: Bookkeeping-only diff classification

The system SHALL classify a pull request's diff as bookkeeping-only if and
only if every changed path matches `openspec/**`, `docs/**`, or `**/*.md`, OR
is `pyproject.toml` with a diff touching only the `version = ` line.

#### Scenario: Docs-and-openspec-only diff is bookkeeping-only
- **WHEN** a PR's diff touches only paths under `openspec/**` and `docs/**`
- **THEN** the diff is classified bookkeeping-only

#### Scenario: A src change alongside a docs change is not bookkeeping-only
- **WHEN** a PR's diff touches `docs/README.md` and
  `src/worktrail/router/dashboard.py`
- **THEN** the diff is not classified bookkeeping-only

#### Scenario: pyproject.toml version-only bump is bookkeeping-only
- **WHEN** a PR's diff touches only `pyproject.toml`, and that diff changes
  exclusively the `version = "X.Y.Z"` line
- **THEN** the diff is classified bookkeeping-only

#### Scenario: pyproject.toml non-version change is not bookkeeping-only
- **WHEN** a PR's diff touches `pyproject.toml` and the diff includes a
  changed line other than `version = `
- **THEN** the diff is not classified bookkeeping-only

### Requirement: Full suite skipped for bookkeeping-only diffs

The system SHALL skip the `Lint, Test & Build` job's pytest, orchestrator
golden-regression, and package-build steps when the pull request's diff is
classified bookkeeping-only.

#### Scenario: Bookkeeping-only PR skips the full suite
- **WHEN** a pull request's diff is classified bookkeeping-only
- **THEN** the `Lint, Test & Build` job's test/build steps do not run for
  that PR's CI run

### Requirement: Required status check still resolves for bookkeeping-only diffs

The system SHALL post a successful `Lint, Test & Build` status check for a
bookkeeping-only pull request, so the branch ruleset's required status check
(`.github/rulesets/protect-main.json`) is satisfied without running the full
suite.

#### Scenario: Bookkeeping-only PR gets a green required check
- **WHEN** a pull request's diff is classified bookkeeping-only
- **THEN** a `Lint, Test & Build` check with conclusion `success` is created
  against that PR's head SHA, and the PR is mergeable under the branch
  ruleset without the full suite having run

### Requirement: Full suite runs when classification is not bookkeeping-only or is ambiguous

The system SHALL run the full `Lint, Test & Build` suite whenever the diff
is not classified bookkeeping-only, and whenever the classification cannot
be computed (for example, a non-`pull_request` trigger such as a post-merge
`push` to `main`).

#### Scenario: Code-touching PR runs the full suite
- **WHEN** a pull request's diff includes any path outside
  `openspec/**`/`docs/**`/`**/*.md` other than a version-only
  `pyproject.toml` change
- **THEN** the `Lint, Test & Build` job's test/build steps run normally

#### Scenario: Post-merge push to main runs the full suite
- **WHEN** CI runs from a `push` event to `main` rather than a
  `pull_request` event
- **THEN** the `Lint, Test & Build` job's test/build steps run normally,
  regardless of what changed in that push

