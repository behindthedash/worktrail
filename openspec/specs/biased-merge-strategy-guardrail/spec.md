# biased-merge-strategy-guardrail Specification

## Purpose
Keeps biased git merge strategy options (`-X ours` and kin) out of
`src/worktrail/`, where they twice silently discarded real content instead of
failing loud, by enforcing the prohibition in CI rather than in prose comments.
## Requirements
### Requirement: Biased merge strategy options fail the build

The test suite SHALL fail if a biased git merge strategy option appears as a
string literal in executable code anywhere under `src/worktrail/`. The guarded
shapes SHALL include the separated (`-X` followed by `ours`/`theirs`) and joined
(`-Xours`/`-Xtheirs`) option forms, the `--strategy-option` long form, and
`ours`/`theirs` named as a top-level merge strategy. The failure SHALL name each
offending file and line and state why the shape is prohibited.

#### Scenario: A biased merge option is reintroduced

- **WHEN** a module under `src/worktrail/` passes a biased merge strategy option
  to git as a string literal
- **THEN** the guard fails, reporting that file and line

#### Scenario: The current tree is clean

- **WHEN** the guard runs against the repository as it stands
- **THEN** it passes, because no such invocation exists

### Requirement: Documenting the prohibition does not trip the guard

The guard SHALL inspect only string literals reachable as executable code,
excluding comments and docstrings, so that source text explaining why a biased
merge strategy is deliberately not used continues to pass. A word such as
`ours` or `theirs` used on its own — in conflict-side handling that does not
select a merge strategy — SHALL NOT be flagged.

#### Scenario: Explanatory prose at an existing call site

- **WHEN** a comment or docstring names the prohibited option in order to
  explain why it is not used
- **THEN** the guard does not flag it

#### Scenario: Legitimate conflict-side handling

- **WHEN** code refers to a conflict's own side by name without selecting a
  merge strategy
- **THEN** the guard does not flag it

### Requirement: The guard blocks merges through an existing required check

The guard SHALL run as part of the repository's existing required test step, so
that it is merge-blocking and locally runnable without adding a new workflow or
a new required status check.

#### Scenario: Guard runs in CI

- **WHEN** the required test job runs for a pull request
- **THEN** the guard is executed as part of it, and its failure fails that job

