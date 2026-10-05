## MODIFIED Requirements

### Requirement: Standard PR body

Every pull request the pipeline creates SHALL use the standard PR body: the caller's summary,
the route and spec lineage when known, the pre-PR gate evidence line, the risk level, the
applied labels, the auto-merge recommendation, and a `## Fold-in Fixes` section declaring the
run's fold-in fixes (one entry per fold-in, or `none` when there were none), in the section
layout the route reference defines. A caller SHALL NOT be able to omit the gate-evidence,
risk, label, or fold-in sections.

#### Scenario: Body carries the enforced sections

- **WHEN** the pipeline creates a pull request from a caller-supplied summary
- **THEN** the PR body contains the summary plus the pre-PR gate evidence, risk level,
  labels, and auto-merge recommendation sections

#### Scenario: Fold-in section declares the fold-ins

- **WHEN** the caller supplies fold-in entries for the run
- **THEN** the PR body contains a `## Fold-in Fixes` section listing each one

#### Scenario: A run with no fold-ins renders the none placeholder

- **WHEN** the caller supplies no fold-in entries
- **THEN** the PR body still carries the `## Fold-in Fixes` section, containing `none`
