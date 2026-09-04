## MODIFIED Requirements

### Requirement: Candidate targets are ranked brief-to-active-change

For each intake brief with a non-null `repo:`, the evaluation SHALL enumerate that repo's active OpenSpec changes (every `openspec/changes/<id>/` with a `proposal.md`, excluding `archive/`), compute a focus-overlap coefficient between the brief's focus-text tokens and each change's feature summary plus task-line tokens, and present the top-K (default 5) changes by coefficient to the evaluator as fold candidates, each with its id, feature summary, and open-task count. A change scoring below a minimum floor of 0.45 SHALL be excluded from the presented candidates regardless of its rank, so a weak, effectively-coincidental lexical match is never offered as a fold target. A brief whose repo has no active changes, or whose active changes all score below the floor, SHALL be evaluated with an empty candidate list. Brief-to-brief clustering SHALL NOT be used to select fold targets.

#### Scenario: Strong overlap with an active change

- **WHEN** an intake brief's focus shares >= 0.45 token overlap with active change `work-queue-dependency-diagnostics` and < 0.1 with every other change
- **THEN** `work-queue-dependency-diagnostics` is the first-ranked fold candidate presented to the evaluator

#### Scenario: Repo has no active changes

- **WHEN** an intake brief names a repo whose `openspec/changes/` contains only `archive/`
- **THEN** the evaluator receives an empty candidate list and may only return `propose-change`, `work-directly`, `needs-decision`, or an existing queue-triage verdict

#### Scenario: Null-repo brief has no candidates

- **WHEN** an intake brief has `repo: null`
- **THEN** no change candidates are enumerated, and the evaluator may return `propose-change` or `fold-into-change` only if its evidence names a repo it verified; otherwise it returns `needs-decision`

#### Scenario: Only weak matches exist

- **WHEN** an intake brief's focus scores below 0.45 token overlap against every one of its repo's active changes (e.g. the strongest match is a title-substring coincidence scoring 0.43)
- **THEN** the evaluator receives an empty candidate list for that brief and `fold-into-change` is not a valid verdict for it, even though the repo does have active changes

### Requirement: Fold and propose are applied as a pull request, fail-closed

Applying a `fold-into-change` verdict SHALL, in a fresh worktree on a branch off a freshly-fetched `origin/<base branch>`, append the brief's focus as a `## Folded from <brief-id>` section to the target change's `proposal.md` and append an unchecked task derived from the brief to its `tasks.md`, with the task's checklist-item text collapsed to a single line (internal newlines/whitespace normalized) regardless of how the source evidence was formatted; applying a `propose-change` verdict SHALL create a new change under the target repo's `openspec/changes/` with proposal, design, specs, and tasks artifacts that pass `openspec validate`. Both verdicts SHALL fetch the target repo's base branch from `origin` before creating the worktree the change is authored in, so the fold or propose reflects the target repo's true current state rather than a potentially-stale local checkout, and a target archived upstream since the last local fetch is detected rather than silently treated as still active. In both cases the system SHALL commit, push, and open a pull request against the base branch, and SHALL close the brief (`status: done`) only after the pull request exists, stamping `triaged-to: <repo>:change:<change-id>` and the pull-request URL in the brief's closure note. If any step before the pull request fails — including the pre-branch fetch — the brief SHALL remain in `queue/` unmodified and the failure SHALL be reported.

#### Scenario: Fold succeeds

- **WHEN** `apply --confirm` executes `fold-into-change` targeting `datalena:change:084-automation-health-digest` and the pull request is opened
- **THEN** the change's `proposal.md` and `tasks.md` on the PR branch carry the folded content, and the brief is in `picked/` with `status: done`, `triaged-to: datalena:change:084-automation-health-digest`, and the PR URL in its closure note

#### Scenario: Pull request creation fails

- **WHEN** `apply --confirm` executes `propose-change` and `gh pr create` fails
- **THEN** the brief is still in `queue/` with `status: queued` and unchanged content, and the run reports the failure with the branch name for manual recovery

#### Scenario: Proposed change fails validation

- **WHEN** the generated change does not pass `openspec validate`
- **THEN** no commit is made, the brief is unchanged, and the validation output is reported

#### Scenario: Multi-line evidence is collapsed for the tasks.md checklist item

- **WHEN** `apply --confirm` executes `fold-into-change` for a verdict whose `evidence` spans multiple lines
- **THEN** the target change's `tasks.md` gets a single-line `- [ ] N.1 <collapsed evidence>` checklist item with no embedded newlines, while `proposal.md`'s `## Folded from <brief-id>` section carries the evidence verbatim

#### Scenario: Fetch fails before the worktree is created

- **WHEN** `apply --confirm` executes `fold-into-change` or `propose-change` and `git fetch origin <base branch>` fails
- **THEN** no worktree is created, the brief remains in `queue/` unmodified, and the run reports the fetch failure with the branch name that would have been used

#### Scenario: Target was archived upstream since the last local fetch

- **WHEN** `apply --confirm` executes `fold-into-change` and the target change's directory was archived by a commit on `origin/<base branch>` that the local checkout had not yet fetched
- **THEN** the freshly-fetched worktree no longer has that target's `proposal.md`/`tasks.md`, the fold fails closed with that reported as the error, and the brief remains in `queue/` unmodified
