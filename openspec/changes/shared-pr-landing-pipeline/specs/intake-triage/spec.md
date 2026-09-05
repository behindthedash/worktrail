## MODIFIED Requirements

### Requirement: Fold and propose are applied as a pull request, fail-closed
Applying a `fold-into-change` verdict SHALL, in a fresh worktree on a branch off the target repo's base branch, append the brief's focus as a `## Folded from <brief-id>` section to the target change's `proposal.md` and append unchecked tasks derived from the brief to its `tasks.md`; applying a `propose-change` verdict SHALL create a new change under the target repo's `openspec/changes/` with proposal, design, specs, and tasks artifacts that pass `openspec validate`. In both cases the system SHALL land the pull request through the shared PR-landing pipeline: the change directory's compile marker SHALL be current and committed before anything is pushed, the PR's labels SHALL come from the preflight gate, the PR SHALL be CI-watched to a classified outcome, and a run record SHALL be finished with a real completion state. The system SHALL close the brief (`status: done`) only after the pull request exists, stamping `triaged-to: <repo>:change:<change-id>` and the pull-request URL in the brief's closure note. The apply result SHALL carry the landing outcome (landed, code defect, review threads blocking, ceiling, or refused) alongside the PR URL. If any step before the pull request fails — including a compile marker that is missing or stale after the compile attempt — the brief SHALL remain in `queue/` unmodified, nothing SHALL be pushed, and the failure SHALL be reported with the branch name.

#### Scenario: Fold succeeds
- **WHEN** `apply --confirm` executes `fold-into-change` targeting `datalena:change:084-automation-health-digest` and the pipeline lands the pull request
- **THEN** the change's `proposal.md`, `tasks.md`, and `.compile-ok` on the PR branch carry the folded content and a current marker, the PR passes CI's scope check without a hand-added commit, and the brief is in `picked/` with `status: done`, `triaged-to: datalena:change:084-automation-health-digest`, and the PR URL in its closure note

#### Scenario: Compile marker cannot be made current
- **WHEN** `apply --confirm` executes `propose-change` and the compile reports scope gaps for the generated change
- **THEN** nothing is pushed, no pull request exists, the brief is still in `queue/` with `status: queued` and unchanged content, and the run reports the compile gap output with the branch name for manual recovery

#### Scenario: Pull request creation fails
- **WHEN** `apply --confirm` executes `propose-change` and PR creation fails after the push
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

#### Scenario: Propose-change prompt names the compile gate
- **WHEN** `_apply_propose_change()` formats `PROPOSE_CHANGE_PROMPT_TEMPLATE` for a brief
- **THEN** the formatted prompt instructs the agent to run `worktrail-compile` against the new change directory and fix any reported problem, in addition to `openspec validate --strict`

#### Scenario: CI reports a code defect on the landed PR
- **WHEN** the pipeline's CI watch classifies the opened PR's failure as a code defect
- **THEN** the brief is closed against the existing PR URL as before, the apply result reports the code-defect outcome with the failing check names and the surviving worktree path, and the run record is left unfinished for repair

### Requirement: Interactive pickup of an intake brief triages it
When a user runs `worktrail-go <brief-id>` and the brief is an intake brief, the system SHALL run the intake-triage evaluation for that single brief and present its verdict for confirmation, instead of dispatching it for implementation. Applying the verdict SHALL follow the same apply semantics as the unattended pre-pass, including landing any resulting pull request through the shared PR-landing pipeline in the same invocation. The session SHALL report the landing outcome the apply step returns; when that outcome is a code defect or blocking review threads, the session SHALL continue the CI watch loop's repair procedure against the reported branch and run record rather than stopping at "PR opened". The brief SHALL NOT be claimed into `picked/` for implementation by this path.

#### Scenario: User names an intake brief
- **WHEN** `worktrail-go 20260826-143940-consolidated-...` is invoked and that brief has no `seeded-from:`
- **THEN** the session evaluates the brief against its repo's active changes and offers fold/propose/work-directly/needs-decision, and no implementation dispatch occurs

#### Scenario: User names an execution brief
- **WHEN** `worktrail-go <brief-id>` is invoked for a brief carrying `seeded-from:`
- **THEN** the brief is claimed and dispatched exactly as before this change

#### Scenario: Work-directly continues into dispatch
- **WHEN** an interactive pickup's applied verdict is `work-directly` and the brief is now
  stamped `seeded-from: triage:<run-date>:direct`
- **THEN** the same invocation claims the brief and proceeds through classification and
  dispatch as it would for an execution brief named directly

#### Scenario: Keep is recorded interactively
- **WHEN** an interactive pickup's verdict is `keep` for a brief not yet due for escalation
- **THEN** the brief gains a `verdict: keep` triage note exactly as a scheduled run would
  write, and the session reports the note and stops

#### Scenario: Confirmed fold lands and is watched in the same invocation
- **WHEN** the user confirms a `fold-into-change` verdict and the apply step returns a PR URL with a landed outcome
- **THEN** the session reports the PR URL and the completion state the pipeline recorded, and stops

#### Scenario: Confirmed propose reports a code defect
- **WHEN** the user confirms a `propose-change` verdict and the apply step returns a PR URL with a code-defect outcome
- **THEN** the session does not stop at the report; it repairs the defect in the reported worktree and re-invokes the landing pipeline against the same run record until a terminal outcome is reached
