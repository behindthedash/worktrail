# codex-sandbox-confinement Specification

## Purpose
Confines every headless Codex child worktrail launches to an explicit, shared, auditable set
of writable roots using Codex's own `workspace-write` sandbox, so a child's `-C`/`--add-dir`
scope is enforced by the kernel rather than advisory, while loopback and network access keep
working.
## Requirements
### Requirement: Codex children launch under workspace-write

Every Codex launch command worktrail builds for a headless child -- skill dispatch,
orchestrator task workers, and drain one-shots -- SHALL pass `-s workspace-write` together with
the `sandbox_workspace_write.network_access=true` config override, and SHALL NOT pass
`-s danger-full-access` unless the sandbox-mode override is set.

#### Scenario: Skill dispatch Codex argv
- **WHEN** `worktrail-skill-dispatch --agent codex --dry-run` builds its command
- **THEN** the argv contains `-s workspace-write` and the network-access override and does not
  contain `danger-full-access`

#### Scenario: Orchestrator worker Codex argv
- **WHEN** the orchestrator builds a Codex worker command for a task worktree
- **THEN** the argv contains `-s workspace-write` and the network-access override and does not
  contain `danger-full-access`

#### Scenario: Drain one-shot Codex argv
- **WHEN** drain builds a Codex one-shot command
- **THEN** the argv contains `-s workspace-write` and the network-access override and does not
  contain `danger-full-access`

### Requirement: Writable roots come from one shared helper

All three launch sites SHALL obtain their Codex sandbox flags from a single shared helper that
emits one `--add-dir` per writable root. The root set SHALL be the child's working directory,
that directory's git common dir when it is a git checkout, the operator state directory, the
work-queue root, the target repo's sibling `<repo>-worktrees/` directory when a target repo is
given, and any caller-supplied extra roots. The helper SHALL de-duplicate roots and emit them
in a stable order, and SHALL never add a user's home directory or a checkout's working tree
other than the child's own working directory.

#### Scenario: Linked worktree child can commit
- **WHEN** the child working directory is a linked git worktree
- **THEN** the emitted roots include that worktree's git common dir, so `git commit` inside the
  child succeeds under the sandbox

#### Scenario: Caller-supplied roots are merged
- **WHEN** `worktrail-skill-dispatch --agent codex --add-dir X` is run
- **THEN** `X` appears among the emitted `--add-dir` values alongside the helper's default
  roots, and no default root is dropped

#### Scenario: Roots are reproducible
- **WHEN** the helper is called twice with the same inputs
- **THEN** it returns identical argv, with no duplicate `--add-dir` value

### Requirement: Drain grants per-repo git and worktree roots, not checkouts

Drain SHALL add, for each immediate child directory of `--repos-root` that is a git checkout,
that checkout's `.git` directory and its `<name>-worktrees` sibling as writable roots, and SHALL
NOT add the checkout's working tree or `--repos-root` itself. When `--repo` is given, only that
repo's roots are added.

#### Scenario: Drain across a repos root
- **WHEN** `--repos-root` contains checkouts `a` and `b` and a non-git directory `notes`
- **THEN** the drain one-shot argv contains `--add-dir` for `a/.git`, `a-worktrees`, `b/.git`,
  and `b-worktrees`, and none for `a`, `b`, `notes`, or the repos root

#### Scenario: Drain filtered to one repo
- **WHEN** `--repo a` is given
- **THEN** only `a/.git` and `a-worktrees` are added

### Requirement: Operator escape hatches are explicit and loud

The helper SHALL read `WORKTRAIL_CODEX_EXTRA_WRITABLE_ROOTS` as an `os.pathsep`-separated list
of additional writable roots, and SHALL read `WORKTRAIL_CODEX_SANDBOX_MODE` as the sandbox
mode, accepting only `workspace-write` (default) and `danger-full-access`. When the override
selects `danger-full-access`, the helper SHALL emit that flag with no `--add-dir` values and
print a one-line notice naming the variable to stderr on every launch. Any other value SHALL
raise an error naming the variable.

#### Scenario: Extra roots honored
- **WHEN** `WORKTRAIL_CODEX_EXTRA_WRITABLE_ROOTS=/a:/b` is set
- **THEN** `/a` and `/b` appear among the emitted `--add-dir` values

#### Scenario: Mode override restores old behavior loudly
- **WHEN** `WORKTRAIL_CODEX_SANDBOX_MODE=danger-full-access` is set
- **THEN** the argv contains `-s danger-full-access`, and a notice naming
  `WORKTRAIL_CODEX_SANDBOX_MODE` is written to stderr

#### Scenario: Unknown mode refused
- **WHEN** `WORKTRAIL_CODEX_SANDBOX_MODE=read-only` is set
- **THEN** building the command raises an error naming `WORKTRAIL_CODEX_SANDBOX_MODE`

### Requirement: Lifecycle harness gates Codex writes on the sandbox flag

The propose-spawn lifecycle fake agent SHALL treat `-s workspace-write` as the Codex flag that
grants write access, and the lifecycle test SHALL assert that a Codex dispatch's `--add-dir`
set includes the child working directory's git common dir and the run-record directory.

#### Scenario: Fake Codex child writes only under workspace-write
- **WHEN** the fake agent is invoked as `codex` with `-s workspace-write`
- **THEN** it authors the change files under its cwd; without that flag it exits 0 and writes
  nothing

