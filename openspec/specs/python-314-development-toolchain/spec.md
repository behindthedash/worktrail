# python-314-development-toolchain Specification

## Purpose
Keeps the local development entry points — the editable install script and the
repository's own policy gates and documented commands — on the same explicit Python 3.14
toolchain that CI selects. A missing or misconfigured 3.14 installation then fails
immediately and actionably instead of a PATH-resolved interpreter silently producing a
misleading local install or gate result.
## Requirements
### Requirement: Local development installation uses Python 3.14 explicitly
`scripts/dev-install.sh` SHALL invoke pip as `python3.14 -m pip` for its normal
editable development installation and its externally-managed-environment
fallback. It SHALL invoke `scripts/check_packaging_metadata.py` with
`python3.14`. The linked-worktree refusal SHALL run before either command and
remain unchanged.

#### Scenario: Canonical checkout has Python 3.14
- **WHEN** a developer runs `scripts/dev-install.sh` from the canonical
  checkout and `python3.14` is available
- **THEN** the script installs `.[dev]` and performs packaging metadata
  verification through that interpreter

#### Scenario: Python 3.14 is unavailable
- **WHEN** a developer runs `scripts/dev-install.sh` without `python3.14` on
  PATH
- **THEN** the command fails before an alternative `pip` or `python3`
  executable can install or verify the package

#### Scenario: Linked worktree is refused first
- **WHEN** a developer runs `scripts/dev-install.sh` from a linked worktree
- **THEN** it reports the existing canonical-checkout refusal and invokes
  neither Python 3.14 pip nor metadata verification

### Requirement: Repository-managed local gates use Python 3.14 explicitly
The repository `.worktrail/policy.yaml` SHALL run its `pre_pr_cmd` and
`pre_commit_cmd` through `python3.14`; pytest in `pre_pr_cmd` SHALL be invoked
as `python3.14 -m pytest`. `AGENTS.md` SHALL present the same Python 3.14
commands for the documented test, golden-regression, lint, and shebang checks.

#### Scenario: A developer follows the documented commands
- **WHEN** a developer copies a documented local test, golden-regression,
  lint, or shebang command from `AGENTS.md`
- **THEN** that command uses `python3.14` rather than a PATH-selected Python
  executable

#### Scenario: Worktrail runs its own policy gate
- **WHEN** Worktrail executes this repository's configured pre-PR or
  pre-commit command
- **THEN** every Python and pytest invocation in that command resolves through
  Python 3.14
