# implement-pipeline-neutral-cwd Specification

## Purpose
TBD - created by archiving change implement-pipeline-spec-root-neutral-cwd. Update Purpose after archive.
## Requirements
### Requirement: Implement pipeline runs from a neutral shell cwd
The `implement` pipeline SHALL, before `#precheck-gate`, move the shell to a neutral directory
that is outside every git work tree (`$REPO`'s parent directory, verified with
`git rev-parse --is-inside-work-tree`; a fresh `mktemp -d` when the parent is itself a work
tree), and SHALL NOT `cd` into `$REPO` or any worktree in any later step. `SPEC_ROOT` SHALL
remain `$REPO` (the canonical checkout); the pipeline SHALL state that a linked worktree is not
an acceptable substitute because run state and throwaway directories are derived from
`SPEC_ROOT`'s name. The `#orchestrator` block SHALL note that it assumes a neutral cwd.

#### Scenario: Launch with the worktree write guard installed
- **WHEN** the implement pipeline is run in a session whose shell was standing inside the
  canonical checkout and the machine-wide worktree write guard is active
- **THEN** precheck, compile and `worktrail-detach launch` run without a
  `Worktree guard: blocked write to the canonical checkout` denial, because every Bash call
  is issued from the neutral cwd and addresses the repo by path

#### Scenario: Run state stays in the canonical location
- **WHEN** the implement pipeline launches the orchestrator
- **THEN** `full-real` receives `--repo "$REPO"`, the run journal and task worktrees are
  created under `<repo>-worktrees/`, and no `expected 'main'` branch warning is printed

#### Scenario: Skill text is pinned by the plugin-surface test
- **WHEN** `tests/test_plugin_surface.py` runs
- **THEN** it fails if the `#implement-pipeline` section lacks the neutral-cwd step before
  `#precheck-gate`, contains `cd "$REPO"` or `cd "$SPEC_ROOT"`, or no longer passes
  `SPEC_ROOT=$REPO` to `#orchestrator`

