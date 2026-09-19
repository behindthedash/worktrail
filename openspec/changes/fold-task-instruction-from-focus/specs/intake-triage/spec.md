## MODIFIED Requirements

### Requirement: Fold and propose are applied as a pull request, fail-closed
Before any other work begins, applying a `fold-into-change` or `propose-change` verdict SHALL atomically claim the brief (the same `claim()` primitive the work queue uses for its `queue/` → `picked/` transition); a concurrent `apply --confirm` for the same brief id that observes the brief already claimed SHALL perform no worktree, agent, or pull-request work and SHALL return an error result naming the concurrent claim, rather than racing the claiming invocation through its own worktree/PR pipeline. Once claimed, the system SHALL resolve the push remote once -- the remote named by `git config remote.pushDefault`, falling back to `origin` -- and SHALL use that same remote for fetching the base branch, for the unpushed-base staleness check (`<remote>/<base branch>..<base branch>`), and as the ref the fresh worktree is branched off (`<remote>/<base branch>`); no `origin` literal SHALL be used for any of these when `remote.pushDefault` is set. Applying a `fold-into-change` verdict SHALL, in that fresh worktree on a branch off `<remote>/<base branch>`, append a `## Folded from <brief-id>` section to the target change's `proposal.md` carrying both the brief's focus and the triage evidence, and append to its `tasks.md` an unchecked task group whose checklist item states the work to do -- the first sentence of the brief's focus, collapsed to one line -- rather than the triage evidence, which argues why the fold belongs and is not an instruction. The group SHALL carry a one-line rationale pointing at `proposal.md`'s matching section so the evidence remains one command away without displacing the instruction. When the brief has no readable focus the checklist item SHALL fall back to the collapsed evidence, so a fold never emits an empty task; applying a `propose-change` verdict SHALL create a new change under the target repo's `openspec/changes/` with proposal, design, specs, and tasks artifacts that pass `openspec validate`. In both cases the system SHALL land the pull request through the shared PR-landing pipeline: the change directory's compile marker SHALL be current and committed before anything is pushed, the PR's labels SHALL come from the preflight gate, the PR SHALL be CI-watched to a classified outcome, and a run record SHALL be finished with a real completion state. The system SHALL close the brief (`status: done`) only after the pull request exists, stamping `triaged-to: <repo>:change:<change-id>` and the pull-request URL in the brief's closure note. The apply result SHALL carry the landing outcome (landed, code defect, review threads blocking, ceiling, or refused) alongside the PR URL. If any step between the claim and the pull request fails — including a compile marker that is missing or stale after the compile attempt — the claimed brief SHALL be released back to `queue/` with unchanged focus/evidence content, nothing SHALL be pushed, and the failure SHALL be reported with the branch name.

#### Scenario: Fold succeeds
- **WHEN** `apply --confirm` executes `fold-into-change` targeting `datalena:change:084-automation-health-digest` and the pipeline lands the pull request
- **THEN** the change's `proposal.md`, `tasks.md`, and `.compile-ok` on the PR branch carry the folded content and a current marker, the PR passes CI's scope check without a hand-added commit, and the brief is in `picked/` with `status: done`, `triaged-to: datalena:change:084-automation-health-digest`, and the PR URL in its closure note

#### Scenario: Concurrent apply on the same brief is serialized
- **WHEN** two concurrent `apply --confirm` invocations both act on a verdict for the same brief id
- **THEN** the first to claim the brief proceeds through the worktree/PR pipeline, and the second observes an already-claimed brief, performs no git fetch, worktree creation, agent spawn, or pull-request work, and returns an error result naming the concurrent claim without ever opening a second pull request

#### Scenario: Compile marker cannot be made current
- **WHEN** `apply --confirm` executes `propose-change` and the compile reports scope gaps for the generated change
- **THEN** nothing is pushed, no pull request exists, the brief is released back to `queue/` with `status: queued` and unchanged focus/evidence content, and the run reports the compile gap output with the branch name for manual recovery

#### Scenario: Pull request creation fails
- **WHEN** `apply --confirm` executes `propose-change` and PR creation fails after the push
- **THEN** the brief is released back to `queue/` with `status: queued` and unchanged focus/evidence content, and the run reports the failure with the branch name for manual recovery

#### Scenario: Proposed change fails validation
- **WHEN** the generated change does not pass `openspec validate`
- **THEN** no commit is made, the claimed brief is released back to `queue/` unchanged, and the validation output is reported

#### Scenario: The checklist item states the work, not the case for it
- **WHEN** `apply --confirm` executes `fold-into-change` for a brief whose focus opens "Add a zero-execution guard to every test-executing CI job in datalena's qa-pipeline, so a required check can never report green having run nothing." and whose verdict `evidence` argues that the target change already scopes that class and has not done it yet
- **THEN** the target change's `tasks.md` gets a `- [ ] N.1` checklist item whose body is that opening sentence, the evidence appears nowhere in the task body, and `proposal.md`'s `## Folded from <brief-id>` section carries the brief's focus and that evidence

#### Scenario: Multi-line evidence is collapsed for the tasks.md checklist item
- **WHEN** `apply --confirm` executes `fold-into-change` for a verdict whose `evidence` spans multiple lines
- **THEN** the evidence does not appear in the `- [ ] N.1` checklist item at all -- the item states the work from the brief's focus -- and `proposal.md`'s `## Folded from <brief-id>` section carries the evidence verbatim with its line breaks intact; only when the brief has no readable focus does the item carry the evidence, collapsed to a single line with no embedded newlines

#### Scenario: Multi-line focus is collapsed for the tasks.md checklist item
- **WHEN** `apply --confirm` executes `fold-into-change` for a verdict whose brief focus spans multiple lines
- **THEN** the checklist item is a single line with no embedded newlines, and the multi-line text is preserved verbatim in `proposal.md`'s `## Folded from <brief-id>` section

#### Scenario: Brief has no readable focus
- **WHEN** `apply --confirm` executes `fold-into-change` for a brief with neither a `focus:` frontmatter field nor a `## Focus` body section
- **THEN** the checklist item falls back to the collapsed evidence rather than being emitted empty, and the fold otherwise proceeds unchanged

#### Scenario: Task file scope covers paths named only in the focus
- **WHEN** the brief's focus cites `src/worktrail/workqueue/queue_triage.py` and the verdict evidence cites no path at all
- **THEN** the appended task carries a `files:` line naming that path (and its existing sibling test file), derived from the focus as well as the evidence

#### Scenario: Fetch fails before the worktree is created
- **WHEN** `apply --confirm` executes `fold-into-change` or `propose-change` and `git fetch <push remote> <base branch>` fails after the brief was claimed
- **THEN** no worktree is created, the claimed brief is released back to `queue/` unmodified, and the run reports the fetch failure with the branch name that would have been used

#### Scenario: Target was archived upstream since the last local fetch
- **WHEN** `apply --confirm` executes `fold-into-change` and the target change's directory was archived by a commit on `<push remote>/<base branch>` that the local checkout had not yet fetched
- **THEN** the freshly-fetched worktree no longer has that target's `proposal.md`/`tasks.md`, the fold fails closed with that reported as the error, and the claimed brief is released back to `queue/` unmodified

#### Scenario: Propose-change prompt names the compile gate
- **WHEN** `_apply_propose_change()` formats `PROPOSE_CHANGE_PROMPT_TEMPLATE` for a brief
- **THEN** the formatted prompt instructs the agent to run `worktrail-compile` against the new change directory and fix any reported problem, in addition to `openspec validate --strict`

#### Scenario: CI reports a code defect on the landed PR
- **WHEN** the pipeline's CI watch classifies the opened PR's failure as a code defect
- **THEN** the brief is closed against the existing PR URL as before, the apply result reports the code-defect outcome with the failing check names and the surviving worktree path, and the run record is left unfinished for repair

#### Scenario: Fork-configured checkout uses the push remote for the base ref
- **WHEN** `apply --confirm` executes `fold-into-change` or `propose-change` in a checkout whose `git config remote.pushDefault` is `fork`
- **THEN** the system runs `git fetch fork <base branch>`, compares `fork/<base branch>..<base branch>` for the unpushed-base check, creates the worktree with `git worktree add -b <branch> <dir> fork/<base branch>`, and never fetches, compares against, or branches off `origin/<base branch>`

#### Scenario: Local base ahead of the push remote names that remote
- **WHEN** `remote.pushDefault` is `fork` and the local base branch carries commits absent from `fork/<base branch>`
- **THEN** the apply fails closed before any worktree is created, the claimed brief is released back to `queue/` unmodified, and the error names `fork/<base branch>` (not `origin/<base branch>`) as the ref the local branch is ahead of

#### Scenario: Unconfigured checkout still uses origin
- **WHEN** `git config remote.pushDefault` is unset
- **THEN** the fetch, unpushed-base check, and worktree base ref all use `origin/<base branch>`, exactly as before
