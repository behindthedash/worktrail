# worker-pre-commit-command Specification

## Purpose
Gives a repo one policy-declared command that every code-producing worker runs before each commit, backed by a deterministic orchestrator check, so a formatting or lint miss fails in the worktree instead of at CI.
## Requirements
### Requirement: pre_commit_cmd is a policy key
`pre_commit_cmd` SHALL be a repo policy key defaulting to None. A non-string value SHALL be forced back to None with a warning.

#### Scenario: Unset key changes nothing
- **WHEN** a repo's policy does not set `pre_commit_cmd`
- **THEN** worker prompts carry no pre-commit rule and no post-commit check runs

#### Scenario: Invalid value falls back
- **WHEN** a policy file sets `pre_commit_cmd: [ruff]`
- **THEN** loading the policy yields None for that key and records a warning naming it

### Requirement: Workers run pre_commit_cmd before every commit
When `pre_commit_cmd` is set, the implement, fix, and ci-fix worker prompts SHALL carry a hard rule to run that exact command from the worktree root immediately before every commit and to stage what it changes.

#### Scenario: Implement prompt carries the rule
- **WHEN** an implement worker prompt is built for a repo whose policy sets `pre_commit_cmd: "ruff check . --fix && ruff format ."`
- **THEN** the prompt's hard rules include that command as a before-every-commit step

#### Scenario: ci-fix prompt carries the rule
- **WHEN** a ci-fix group worker prompt is built for the same repo
- **THEN** the prompt's hard rules include the same command

### Requirement: The orchestrator re-runs pre_commit_cmd after each task commit
After an implement or fix worker reports a commit, the orchestrator SHALL run `pre_commit_cmd` in the task worktree. When the command changes files within the task's declared scope, the orchestrator SHALL stage those files and amend the worker's commit, updating the recorded head. Changes to files outside the task's scope SHALL be restored, not committed, and noted on the journal entry. A non-zero exit from the command SHALL be recorded on the journal entry and SHALL NOT by itself fail the task.

#### Scenario: Formatter changed an in-scope file
- **WHEN** the command reformats `src/worktrail/conductor/compile.py`, which is in the task's scope
- **THEN** the commit is amended to include the reformatted file and the task's head sha is updated

#### Scenario: Command left the tree clean
- **WHEN** the command exits 0 and changes nothing
- **THEN** the commit and head sha are unchanged

### Requirement: repo-init seeds pre_commit_cmd from detected CI lint steps
When `worktrail-repo-init` writes a new policy file, it SHALL set `pre_commit_cmd` from the lint tools it detects in the repo's workflow run steps: `ruff` yields `ruff check . --fix && ruff format .`, `oxlint` yields `npx oxlint --fix .`, and `prettier` yields `npx prettier --write .`, joined with `&&` when more than one is detected. When none is detected the key SHALL be omitted. An existing policy file SHALL NOT be modified.

#### Scenario: Ruff detected in CI
- **WHEN** a workflow step runs `ruff format --check .` and no policy file exists
- **THEN** the seeded policy sets `pre_commit_cmd: "ruff check . --fix && ruff format ."`

#### Scenario: No lint step detected
- **WHEN** no workflow step mentions ruff, oxlint, or prettier
- **THEN** the seeded policy has no `pre_commit_cmd` key

### Requirement: worktrail's own policy formats before commit and checks at integrate
This repository's policy SHALL set `pre_commit_cmd: "ruff check . --fix && ruff format ."` and its `integrate_smoke_cmd` SHALL include `ruff check . && ruff format --check .` so a formatting miss fails the group branch locally before a PR opens.

#### Scenario: Unformatted group branch fails the smoke command
- **WHEN** a group integration branch contains a file `ruff format --check .` would rewrite
- **THEN** the integrate smoke command exits non-zero and no PR opens for that group

