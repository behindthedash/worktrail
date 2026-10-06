# docs-only-risk-cap-scope Specification

## Purpose

Scope the docs-only risk cap in the shared PR-label computation to the diff it was written
for — the labeled change's own diff. The cap corrects a free-text classifier false positive
against real diff ground truth; where the inspected diff belongs to something else (the
change-authoring worktree a run is seeded from, whose committed diff is by construction the
change's own spec docs), the cap must not fire, because the seed is every group PR's only risk
input and the cap can never be undone downstream. Without this, every launched run carries the
lowest tier no matter what the merge decision was supposed to gate on.

## ADDED Requirements

### Requirement: The docs-only risk cap applies only to the labeled change's own diff

The shared label computation SHALL apply its docs-only risk cap only when its caller has
confirmed that the inspected diff is the labeled change's own diff. A caller whose inspected
diff is not the labeled change's own SHALL be able to skip the cap only by an explicit,
named opt-out; with no opt-out the cap SHALL apply exactly as before, and an unresolvable or
empty diff SHALL continue to fail closed to the uncapped risk.

#### Scenario: A caller with no artifact skips the cap
- **WHEN** the label computation runs with the explicit no-cap opt-out against a checkout whose
  committed diff matches `docs_only_paths` entirely
- **THEN** the risk passed in is used verbatim — a `high` risk yields `go:risk-high` and
  `go:no-automerge`, not `go:risk-low`

#### Scenario: A caller with the artifact's own diff keeps the cap
- **WHEN** the labels for the same docs-only checkout are computed without the opt-out
- **THEN** the risk is capped to `low` exactly as before this change

#### Scenario: The opt-out is refused where the cap must stay on
- **WHEN** the opt-out is supplied without the labels-only mode the launch seed uses
- **THEN** the command prints an error and exits non-zero without printing any label, so the
  in-worktree pre-PR gate path cannot stop capping

#### Scenario: An unresolvable diff is never treated as docs-only
- **WHEN** the checkout's base ref cannot be resolved, or its diff against the base is empty
- **THEN** no cap is applied, with or without the opt-out

### Requirement: The launch seed never applies the docs-only risk cap

The launch reference's seed computation — the labels passed as `--pr-label` before an
orchestrated run is dispatched, and the only risk input every group PR's labels are recomputed
from — SHALL pass through the classifier's risk verdict unchanged, because the seed's inspected
diff is the change-authoring worktree's spec commit, not the work the run will produce. The
launch reference and the CLI option SHALL be locked together by a test, so removing the option
from the reference fails the build rather than silently re-arming the misfire.

#### Scenario: The launch seed is uncapped
- **WHEN** the launch reference computes the seed labels against a change-authoring worktree
  whose only committed change is the change's own spec docs
- **THEN** the seeded risk is the classifier's verdict for the request, never a capped `low`

#### Scenario: Dropping the option from the launch reference fails
- **WHEN** the launch reference's seed invocation no longer passes the no-cap opt-out
- **THEN** the enforcement test fails naming the launch reference, and a file mentioning the
  label family without a registered proof fails the prose-registry test

#### Scenario: An elevated verdict is never seeded as low
- **WHEN** a run is launched for a request the classifier tiered `high` or `critical` and the
  change-authoring worktree's diff is docs-only
- **THEN** the seed carries that tier plus `go:no-automerge`, so no group PR from that run is
  auto-merge eligible under a `max_risk` that tier exceeds

### Requirement: A risk lowered by the docs-only cap is reported

When the cap actually lowers the risk, the shared label computation SHALL emit a line on
standard error naming the risk it lowered and the policy section that authorized the cap. It
SHALL emit nothing when it does not lower the risk. The label set on standard output SHALL be
unchanged by the report.

#### Scenario: The cap announces the lowering
- **WHEN** a `medium` risk is capped to `low` on a docs-only diff
- **THEN** standard error carries one line naming the `medium` to `low` lowering and the labels
  on standard output are byte-for-byte unchanged

#### Scenario: No lowering, no line
- **WHEN** the risk is already `low`, or the inspected diff is not docs-only
- **THEN** nothing is written to standard error
