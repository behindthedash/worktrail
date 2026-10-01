## MODIFIED Requirements

### Requirement: Writable roots come from one shared helper

All three launch sites SHALL obtain their Codex sandbox flags from a single shared helper that
emits one `--add-dir` per writable root. The root set SHALL be the child's working directory,
that directory's worktree-specific administrative git directory and git common directory when
it is a git checkout, the operator state directory, the work-queue root, the target repo's
sibling `<repo>-worktrees/` directory when a target repo is given, and any caller-supplied
extra roots. The helper SHALL de-duplicate roots and emit them in a stable order, and SHALL
never add a user's home directory or a checkout's working tree other than the child's own
working directory.

#### Scenario: Linked worktree child can commit

- **WHEN** the child working directory is a linked git worktree
- **THEN** the emitted roots include that worktree's administrative git directory and its git
  common directory, so `git commit` inside the child can write both worktree-local and shared
  git state under the sandbox

#### Scenario: Normal checkout does not duplicate its git directory

- **WHEN** the child working directory is a normal git checkout whose administrative and common
  git directories are the same
- **THEN** that git directory appears only once among the emitted roots

#### Scenario: Caller-supplied roots are merged

- **WHEN** `worktrail-skill-dispatch --agent codex --add-dir X` is run
- **THEN** `X` appears among the emitted `--add-dir` values alongside the helper's default
  roots, and no default root is dropped

#### Scenario: Roots are reproducible

- **WHEN** the helper is called twice with the same inputs
- **THEN** it returns identical argv, with no duplicate `--add-dir` value
