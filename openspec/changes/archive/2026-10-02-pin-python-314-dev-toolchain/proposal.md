## Why

Worktrail declares Python 3.14 as its runtime floor and CI selects 3.14, but
the local development path does not select that interpreter. `scripts/dev-install.sh`
uses bare `pip` and later bare `python3`; the repository policy uses bare
`pytest` and `python3`. Those commands resolve from a developer's PATH, so a
local install or required pre-PR gate can run under a different Python version
than the one the package and CI support. The absent `MEMORY.md` provides no
contrary repository convention.

Making the local command path explicit gives a missing or incorrectly
configured Python 3.14 installation an immediate, actionable failure instead
of a misleading result from another interpreter.

## What Changes

- Require `scripts/dev-install.sh` to run both pip and its post-install
  metadata check through `python3.14`, including the externally-managed
  fallback, and retain the existing canonical-checkout guard.
- Pin the repository's `.worktrail/policy.yaml` pre-commit and pre-PR commands
  to Python 3.14, including pytest's module invocation.
- Update the developer command reference to show the same Python 3.14 command
  path and add regressions that prevent a bare interpreter or pip from
  returning to those local entry points.

## Capabilities

### New Capabilities

- `python-314-development-toolchain`: local installation and repository policy
  gates use the same explicit Python 3.14 toolchain as CI.

### Modified Capabilities

None.

## Impact

- `scripts/dev-install.sh` and its hermetic shell regression test.
- `.worktrail/policy.yaml`, its policy-drift coverage, and `AGENTS.md`'s
  developer command examples.
- No package runtime support range, dependency versions, CI Python matrix, or
  public CLI behavior changes.
