# intake-triage Specification

## Purpose
Separates the work queue into an intake channel and an execution channel: handoff briefs are consumed into OpenSpec changes (folded into an existing change or proposed as a new one) and never worked directly, while unattended execution is initiated only from briefs seeded from specs.
## Requirements
### Requirement: Brief kind is derived from provenance
The system SHALL classify every queued brief as exactly one of two kinds: an **execution** brief, when its frontmatter carries a non-empty `seeded-from:` value, or an **intake** brief otherwise. Classification SHALL be derived at read time from existing frontmatter; no new frontmatter field is required and no existing brief SHALL need rewriting to be classified.

#### Scenario: Handoff capture is intake
- **WHEN** a brief was captured via `worktrail-handoff` and has no `seeded-from:` key
- **THEN** it is classified as an intake brief

#### Scenario: Seeded brief is execution
- **WHEN** a brief carries `seeded-from: <repo>:spec:<id>` (or any non-empty `seeded-from:` value)
- **THEN** it is classified as an execution brief

#### Scenario: Consolidated batch is intake
- **WHEN** a brief carries a `## Consolidated from` section or a `related:` list but no `seeded-from:`
- **THEN** it is classified as an intake brief

### Requirement: Unattended auto-pick never claims an intake brief
The unattended auto-pick used by `worktrail-go auto` (and therefore by every drain one-shot) SHALL skip every intake brief with the recorded skip reason `intake-untriaged`, before any other ranking or gating is applied, and SHALL only ever claim execution briefs. The skip SHALL be visible in the auto-pick miss log exactly like existing skip reasons.

#### Scenario: Queue holds only intake briefs
- **WHEN** `worktrail-go auto` runs against a queue whose every brief is an intake brief
- **THEN** no brief is claimed, and every brief is recorded as skipped with reason `intake-untriaged`

#### Scenario: Execution brief ranks normally
- **WHEN** the queue holds one intake brief and one execution brief that passes all existing gates
- **THEN** the execution brief is claimed and the intake brief is recorded as skipped with reason `intake-untriaged`

### Requirement: Interactive pickup of an intake brief triages it
When a user runs `worktrail-go <brief-id>` and the brief is an intake brief, the system SHALL run the intake-triage evaluation for that single brief and present its verdict for confirmation, instead of dispatching it for implementation. Applying the verdict SHALL follow the same apply semantics as the unattended pre-pass. The brief SHALL NOT be claimed into `picked/` for implementation by this path.

#### Scenario: User names an intake brief
- **WHEN** `worktrail-go 20260826-143940-consolidated-...` is invoked and that brief has no `seeded-from:`
- **THEN** the session evaluates the brief against its repo's active changes and offers fold/propose/work-directly/needs-decision, and no implementation dispatch occurs

#### Scenario: User names an execution brief
- **WHEN** `worktrail-go <brief-id>` is invoked for a brief carrying `seeded-from:`
- **THEN** the brief is claimed and dispatched exactly as before this change

### Requirement: Candidate targets are ranked brief-to-active-change
For each intake brief with a non-null `repo:`, the evaluation SHALL enumerate that repo's active OpenSpec changes (every `openspec/changes/<id>/` with a `proposal.md`, excluding `archive/`), compute a focus-overlap coefficient between the brief's focus-text tokens and each change's feature summary plus task-line tokens, and present the top-K (default 5) changes by coefficient to the evaluator as fold candidates, each with its id, feature summary, and open-task count. A brief whose repo has no active changes SHALL be evaluated with an empty candidate list. Brief-to-brief clustering SHALL NOT be used to select fold targets.

#### Scenario: Strong overlap with an active change
- **WHEN** an intake brief's focus shares >= 0.45 token overlap with active change `work-queue-dependency-diagnostics` and < 0.1 with every other change
- **THEN** `work-queue-dependency-diagnostics` is the first-ranked fold candidate presented to the evaluator

#### Scenario: Repo has no active changes
- **WHEN** an intake brief names a repo whose `openspec/changes/` contains only `archive/`
- **THEN** the evaluator receives an empty candidate list and may only return `propose-change`, `work-directly`, `needs-decision`, or an existing queue-triage verdict

#### Scenario: Null-repo brief has no candidates
- **WHEN** an intake brief has `repo: null`
- **THEN** no change candidates are enumerated, and the evaluator may return `propose-change` or `fold-into-change` only if its evidence names a repo it verified; otherwise it returns `needs-decision`

### Requirement: Fold and propose are applied as a pull request, fail-closed
Applying a `fold-into-change` verdict SHALL, in a fresh worktree on a branch off the target repo's base branch, append the brief's focus as a `## Folded from <brief-id>` section to the target change's `proposal.md` and append unchecked tasks derived from the brief to its `tasks.md`; applying a `propose-change` verdict SHALL create a new change under the target repo's `openspec/changes/` with proposal, design, specs, and tasks artifacts that pass `openspec validate`. In both cases the system SHALL commit, push, and open a pull request against the base branch, and SHALL close the brief (`status: done`) only after the pull request exists, stamping `triaged-to: <repo>:change:<change-id>` and the pull-request URL in the brief's closure note. If any step before the pull request fails, the brief SHALL remain in `queue/` unmodified and the failure SHALL be reported.

#### Scenario: Fold succeeds
- **WHEN** `apply --confirm` executes `fold-into-change` targeting `datalena:change:084-automation-health-digest` and the pull request is opened
- **THEN** the change's `proposal.md` and `tasks.md` on the PR branch carry the folded content, and the brief is in `picked/` with `status: done`, `triaged-to: datalena:change:084-automation-health-digest`, and the PR URL in its closure note

#### Scenario: Pull request creation fails
- **WHEN** `apply --confirm` executes `propose-change` and `gh pr create` fails
- **THEN** the brief is still in `queue/` with `status: queued` and unchanged content, and the run reports the failure with the branch name for manual recovery

#### Scenario: Proposed change fails validation
- **WHEN** the generated change does not pass `openspec validate`
- **THEN** no commit is made, the brief is unchanged, and the validation output is reported

### Requirement: Work-directly converts an intake brief into an execution brief
Applying a `work-directly` verdict SHALL stamp `seeded-from: triage:<run-date>:direct` and `recommended-route: F` on the brief in place, leaving it in `queue/`, so it becomes claimable by unattended auto-pick. The evaluator SHALL only be permitted to return `work-directly` when its evidence cites a reproducible defect (a failing test, failing check, or command output) and the brief names a single repo; a `work-directly` verdict without such evidence SHALL be downgraded to `keep` with the raw verdict retained as evidence.

#### Scenario: Verified small defect
- **WHEN** an evaluator returns `work-directly` with evidence naming a failing test in the brief's repo
- **THEN** the brief gains `seeded-from: triage:2026-08-27:direct` and `recommended-route: F`, remains in `queue/`, and is claimable by the next drain iteration

#### Scenario: Work-directly without reproduction evidence
- **WHEN** an evaluator returns `work-directly` whose evidence contains no test, check, or command reference
- **THEN** the verdict is recorded as `keep` with the raw verdict as evidence and the brief is not converted

### Requirement: Needs-decision files a pending decision and keeps the brief queued
Applying a `needs-decision` verdict SHALL file a pending-decision envelope in the human decision queue whose subject is the brief id and whose question is the evaluator's stated ambiguity (for example, which repo owns a `repo: null` brief), and SHALL leave the brief in `queue/` with `status: queued`. A brief with an unresolved pending decision SHALL be skipped by subsequent triage runs until the decision is answered, and SHALL be skipped by auto-pick as before.

#### Scenario: Null-repo brief
- **WHEN** an evaluator returns `needs-decision` for a `repo: null` brief with the question "which repo owns this?"
- **THEN** a pending decision exists naming the brief and question, and the brief remains queued

#### Scenario: Decision answered
- **WHEN** the pending decision is answered with a repo
- **THEN** the next triage run evaluates the brief with that repo's active changes as candidates

### Requirement: Per-repo WIP cap on active changes
The repo policy SHALL support an integer `max_active_changes` key, defaulting to `0` (no cap). When a repo's count of active OpenSpec changes is greater than or equal to a non-zero cap, applying a `propose-change` verdict for that repo SHALL be downgraded to `keep` with a `## Triage <date>` note stating the cap, the current count, and the top fold candidates, and the run report SHALL count briefs held by the cap per repo. `fold-into-change`, `work-directly`, and `needs-decision` SHALL NOT be affected by the cap.

#### Scenario: Repo over cap
- **WHEN** datalena's policy sets `max_active_changes: 20`, datalena has 49 active changes, and an evaluator returns `propose-change` for a datalena brief
- **THEN** the brief stays queued with a triage note naming the cap and count, and the report shows one brief held by the cap for datalena

#### Scenario: Cap not set
- **WHEN** a repo's policy omits `max_active_changes`
- **THEN** `propose-change` verdicts for that repo are applied without any cap check

#### Scenario: Fold under an over-cap repo
- **WHEN** a repo is over its cap and an evaluator returns `fold-into-change`
- **THEN** the fold is applied normally

### Requirement: Drain pre-passes close the intake loop
`worktrail-drain` SHALL accept `--intake-triage` and `--seed-backlog` flags. When set, before the first drain iteration it SHALL run, respectively, the intake-triage `evaluate` then `apply --confirm` over the queue, and the backlog seeder; each pre-pass SHALL be reported in the drain JSON summary with counts (briefs evaluated, verdicts by type, PRs opened, briefs held by cap, seeds captured) and SHALL never abort the drain on its own failure (the failure is reported and the drain proceeds). Both flags SHALL default to off.

#### Scenario: Both pre-passes enabled
- **WHEN** `worktrail-drain --intake-triage --seed-backlog --max-items 4` runs
- **THEN** the summary contains an `intake_triage` block and a `seed_backlog` block populated before iteration 1, and iteration 1 claims only execution briefs

#### Scenario: Pre-pass fails
- **WHEN** the intake-triage evaluator cannot spawn an agent
- **THEN** the summary's `intake_triage` block records the error, and drain iterations still run

#### Scenario: Flags omitted
- **WHEN** `worktrail-drain` runs without either flag
- **THEN** no pre-pass runs and the summary carries no pre-pass blocks, matching behavior before this change

