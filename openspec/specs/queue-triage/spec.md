# queue-triage Specification

## Purpose
TBD - created by archiving change queue-triager-automation. Update Purpose after archive.
## Requirements
### Requirement: Repo-grouped inventory with dedup skip
The `evaluate` step SHALL inventory every brief in `$WORK_QUEUE_DIR/queue/`, group briefs by
their `repo:` frontmatter value (briefs sharing a `repo:` value form one group; briefs with no
`repo:` value, including `null`, form a single additional group), and SHALL exclude from
evaluation any brief whose body contains a `## Triage <date>` section dated within the
configured `--skip-if-triaged-within-days` window (default 25 days) of the run.

#### Scenario: Two briefs share a repo
- **WHEN** `evaluate` runs against a queue containing two briefs both with
  `repo: /home/user/projects/example`
- **THEN** both briefs are assigned to the same evaluator group and are evaluated by a single
  spawned agent for that repo

#### Scenario: Brief has no repo
- **WHEN** `evaluate` runs against a queue containing a brief with `repo: null`
- **THEN** that brief is assigned to the no-repo group and evaluated without a repo-fetch step

#### Scenario: Recently triaged brief is skipped
- **WHEN** `evaluate` runs and a brief's body contains `## Triage 2026-08-01` and the run
  date is within 25 days of 2026-08-01
- **THEN** that brief is excluded from every evaluator group and no evaluator agent is spawned
  or spent on it

### Requirement: Evidence-required verdict per brief
For every brief passed to a group's evaluator agent, the `evaluate` step SHALL require a
verdict of exactly one of `keep`, `stale-close`, `needs-update`, `duplicate-of`,
`fold-into-change`, `propose-change`, `work-directly`, or `needs-decision`, and SHALL
require non-empty `evidence` text for every verdict. `fold-into-change` SHALL additionally
require a non-empty `target_change` (`<repo>:change:<id>` naming an active change presented
as a candidate); `propose-change` SHALL additionally require a non-empty `target_repo` and a
kebab-case `proposed_change_name`; `needs-decision` SHALL additionally require a non-empty
`question`. A verdict that is missing, malformed, or missing required evidence or required
target fields SHALL be recorded as `keep` with the evaluator's raw output retained as
evidence text, never silently dropped from the output.

#### Scenario: Well-formed stale-close verdict
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "stale-close", "evidence":
  "PR #42 merged 2026-07-01 delivers this", "confidence": "high"}`
- **THEN** the verdict file records brief `X` as `stale-close` with that evidence

#### Scenario: Well-formed fold verdict
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "fold-into-change",
  "target_change": "worktrail:change:work-queue-dependency-diagnostics", "evidence": "...",
  "confidence": "high"}`
- **THEN** the verdict file records brief `X` as `fold-into-change` with that target

#### Scenario: Fold verdict names a change that was not a candidate
- **WHEN** an evaluator returns `fold-into-change` with a `target_change` that is not an
  active change in the brief's repo
- **THEN** the verdict is recorded as `keep` with the raw verdict retained as evidence

#### Scenario: Undecidable case fails open
- **WHEN** an evaluator cannot find evidence to confirm or refute a brief's premise within its
  tool-call budget
- **THEN** the verdict for that brief is `keep`, with the evaluator's stated reason for
  inconclusiveness recorded as evidence

#### Scenario: Malformed verdict from an evaluator
- **WHEN** an evaluator's output for a brief cannot be parsed as a valid verdict object
- **THEN** the verdict file records that brief as `keep` with the raw unparsed text retained
  as evidence, and the brief is never left out of the verdict file

### Requirement: Archived or renamed target repo short-circuits its group
Before evaluating any brief in a repo group with a non-null `repo:` value, the evaluator SHALL
check whether that repo is archived (via `gh repo view --json isArchived`). When the check
confirms the repo is archived, every brief in that group SHALL be verdicted `stale-close` with
the archival fact as evidence, without further per-brief evaluation. When the check fails
(network error, `gh` unavailable, or unauthenticated) or returns an inconclusive result, the
group SHALL proceed to per-brief evaluation as if the repo were not archived — archival is
never inferred from a check failure.

#### Scenario: Archived repo closes its whole group
- **WHEN** `gh repo view --json isArchived` for a group's repo returns `{"isArchived": true}`
- **THEN** every brief in that group is verdicted `stale-close` with the archival fact as
  evidence, and no further per-brief tool calls are made for that group

#### Scenario: gh check fails
- **WHEN** the `gh repo view` check for a group's repo errors or times out
- **THEN** the group proceeds to normal per-brief evaluation; archival is not assumed

### Requirement: Verdict file and human-readable report
The `evaluate` step SHALL write two outputs for every run: a machine-applyable JSON verdict
file listing every evaluated brief's verdict, evidence, and confidence, and a human-readable
Markdown report summarizing the run (briefs evaluated, briefs skipped via dedup, verdict
counts by type, and the full per-brief verdict list with evidence). Neither output SHALL be
written to a location inside the target repos being evaluated.

#### Scenario: Successful evaluate run produces both outputs
- **WHEN** `evaluate` completes a run over a non-empty queue
- **THEN** a JSON verdict file and a Markdown report both exist at the run's output directory,
  and the report's verdict counts match the JSON file's contents exactly

### Requirement: Apply step never closes a brief without an approved verdict
The `apply` step SHALL only ever act on verdicts present in a verdict file supplied via
`--verdict-file`, SHALL require an explicit `--confirm` flag before executing any
`stale-close`, `needs-update`, `duplicate-of`, `fold-into-change`, `propose-change`,
`work-directly`, or `needs-decision` action, and SHALL take no queue-mutating action for any
`keep` verdict. `apply` SHALL NOT expose any flag or code path that closes or edits a brief
without both an existing verdict file entry and the `--confirm` flag. Without `--confirm`,
every planned action — including the branch, target change, and pull-request title a fold or
propose would create — SHALL be printed and nothing SHALL be modified in the queue or in any
target repo.

#### Scenario: Apply without --confirm is a dry run
- **WHEN** `apply` is invoked with a verdict file but without `--confirm`
- **THEN** every planned action (claim+done for stale-close/duplicate-of, in-place edit for
  needs-update, branch+PR for fold-into-change/propose-change, in-place stamp for
  work-directly, decision envelope for needs-decision) is printed but no brief in `queue/` or
  `picked/` is modified and no target repo is written to

#### Scenario: Apply with --confirm executes stale-close
- **WHEN** `apply --confirm` runs against a verdict file containing a `stale-close` verdict
  for brief `X`
- **THEN** brief `X` is claimed and marked done, with the verdict's evidence recorded as the
  closure note

#### Scenario: Apply with --confirm executes needs-update
- **WHEN** `apply --confirm` runs against a verdict file containing a `needs-update` verdict
  for brief `Y`
- **THEN** a `## Triage <run-date>` section containing the verdict's evidence is appended to
  brief `Y`'s body in place, and brief `Y` remains in `queue/` with `status: queued`

#### Scenario: Apply with --confirm executes fold-into-change
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change`
  verdict for brief `Z`
- **THEN** the fold is executed per the `intake-triage` capability's fail-closed
  pull-request semantics, and brief `Z` is closed only after the pull request exists

### Requirement: Duplicate-of verdicts resolve safely within a batch
When applying a `duplicate-of` verdict whose target brief is itself verdicted anything other
than `keep` (or is not present) in the same verdict file, `apply` SHALL refuse to execute that
specific verdict, SHALL log a warning identifying the dangling reference, and SHALL leave the
referencing brief untouched (equivalent to `keep` for that brief in this run).

#### Scenario: Duplicate target is also being closed in the same batch
- **WHEN** `apply --confirm` runs against a verdict file where brief `A` is `duplicate-of: B`
  and brief `B` is itself verdicted `stale-close` in the same file
- **THEN** brief `A` is left untouched and a warning is logged; brief `B` is still closed per
  its own verdict

