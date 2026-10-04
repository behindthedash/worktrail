## Why

Worktrail requires Python 3.14+ and the Python 3.14 Ruff migration introduced PEP 758
unparenthesized multi-exception handlers. Thirty-eight executable Python files still start with
`#!/usr/bin/env python3`, however, so directly running any of them on a host where `python3`
resolves below 3.14 fails at parse time before the script can report a useful error. The affected
set includes `src/worktrail/drain/drain.py`, `src/worktrail/router/classify.py`,
`src/worktrail/workqueue/work_queue.py`, and `hooks/suggest_next_step.py`.

The Stop hook exposed the same failure mode and was separately corrected in PR #1364 by invoking
it with `python3.14`. That command-side repair does not make bare execution of the remaining
scripts safe. The repository needs a consistent direct-execution interpreter policy and a local,
CI-equivalent guard for it.

## What Changes

- Change each executable Python file that uses PEP 758 handler syntax from the generic
  `#!/usr/bin/env python3` shebang to `#!/usr/bin/env python3.14`.
- Extend the existing index-based shebang/exec-bit checker to reject a tracked executable Python
  file that combines a generic `python3` shebang with PEP 758 syntax, and cover both compliant and
  violating fixtures.
- Retain generic `python3` shebangs for executable Python that does not require Python 3.14; this
  change does not impose a blanket interpreter pin on every script.

## Capabilities

### New Capabilities

- `python314-executable-shebang-policy`: directly executable Python that uses PEP 758 syntax
  selects Python 3.14 rather than the host's unversioned `python3`.

### Modified Capabilities

- `shebang-exec-bit-consistency`: the existing index-based shebang check also detects the
  generic-interpreter/Python-3.14-syntax mismatch.

## Impact

- The 38 affected executable files across `hooks/`, `scripts/ci/`, and `src/worktrail/` receive a
  shebang-only edit.
- `scripts/ci/check_shebang_exec_bits.py` and its colocated tests gain the policy guard; the
  existing required CI step continues to invoke that checker.
- No package runtime, console-script entry point, file mode, or supported Python version changes.
