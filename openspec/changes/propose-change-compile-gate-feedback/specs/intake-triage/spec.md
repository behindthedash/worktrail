## MODIFIED Requirements

### Requirement: Fold and propose are applied as a pull request, fail-closed

Applying a `fold-into-change` verdict SHALL, in a fresh worktree on a branch off the target repo's base branch, append the brief's focus as a `## Folded from <brief-id>` section to the target change's `proposal.md` and append unchecked tasks derived from the brief to its `tasks.md`; applying a `propose-change` verdict SHALL create a new change under the target repo's `openspec/changes/` with proposal, design, specs, and tasks artifacts that pass `openspec validate`. A `propose-change` verdict's authoring prompt SHALL name both gates the generated change must pass before the agent finishes — `openspec validate <name> --strict` and `worktrail-compile openspec/changes/<name>` — so the agent is told about the compile gate's own checks (a same-file task chain, a missing test-scope task) rather than only the validate gate. In both cases the system SHALL commit, push, and open a pull request against the base branch, and SHALL close the brief (`status: done`) only after the pull request exists, stamping `triaged-to: <repo>:change:<change-id>` and the pull-request URL in the brief's closure note. If any step before the pull request fails, the brief SHALL remain in `queue/` unmodified and the failure SHALL be reported.

#### Scenario: Fold succeeds

- **WHEN** `apply --confirm` executes `fold-into-change` targeting `datalena:change:084-automation-health-digest` and the pull request is opened
- **THEN** the change's `proposal.md` and `tasks.md` on the PR branch carry the folded content, and the brief is in `picked/` with `status: done`, `triaged-to: datalena:change:084-automation-health-digest`, and the PR URL in its closure note

#### Scenario: Pull request creation fails

- **WHEN** `apply --confirm` executes `propose-change` and `gh pr create` fails
- **THEN** the brief is still in `queue/` with `status: queued` and unchanged content, and the run reports the failure with the branch name for manual recovery

#### Scenario: Proposed change fails validation

- **WHEN** the generated change does not pass `openspec validate`
- **THEN** no commit is made, the brief is unchanged, and the validation output is reported

#### Scenario: Propose-change prompt names the compile gate

- **WHEN** `_apply_propose_change()` formats `PROPOSE_CHANGE_PROMPT_TEMPLATE` for a brief
- **THEN** the formatted prompt instructs the agent to run `worktrail-compile` against the new change directory and fix any reported problem, in addition to `openspec validate --strict`
