## Why

Worktrail declares its Python floor in `pyproject.toml` (`requires-python = ">=3.14"`) and pins
its own policy gates to `python3.14`, but nothing verifies the interpreter surface an operator
host actually resolves. On this host bare `python3` is 3.12.3, so `python3
scripts/ci/ruff_pinned.py --help` dies at parse time with `SyntaxError: multiple exception types
must be parenthesized` (the PEP 758 `except A, B:` at `ruff_pinned.py:93`) — a message that names
neither the interpreter nor the fix. Run `go-20260930-181818` (`/go spec implement
pin-python-314-dev-toolchain`, brief `20260930-191011-python-interpreter-floor-preflight`)
presented that mismatch as opaque worker failures and two quarantined groups instead of one clear
message, and the fleet keeps filing "orchestrator groups stuck in QUARANTINED" briefs.

The adjacent guards do not cover this surface. `python-parse-ci-gate` parses with the CI job's own
interpreter; `python314-shebang-policy` pins shebangs for direct execution; the synced
`python-314-development-toolchain` capability pins the policy gates and `AGENTS.md` recipes to
`python3.14`. None checks the interpreter an operator or agent actually gets from PATH — and an
interpreter below the floor cannot even parse the module that would report the problem.

## What Changes

- Add `worktrail-check-interpreter-floor`: it resolves the target repository's declared floor
  from `pyproject.toml` and the interpreters that repository's commands will actually run — the
  Python command words in `pre_pr_cmd`, `pre_commit_cmd`, and `integrate_smoke_cmd`, plus the
  ambient `python3`, each resolved through PATH the way a shell would — probes each with a
  snippet any older interpreter can still parse, and reports every below-floor, missing, or
  undeterminable interpreter with a concrete remediation.
- Refuse the launch before work starts: `worktrail-live full-real` runs the check for its target
  repository before creating any worktree, run journal, or worker and aborts non-zero on
  findings; `worktrail-live precheck` reports the same findings ahead of its task-DAG
  diagnostics, and the dispatch playbook's precheck reaction names that environment abort
  instead of offering "Proceed anyway".
- Keep the check reusable (one console script, `--json` for machine callers) rather than
  duplicating it into other launch surfaces; see `design.md` for why the pre-PR gate paths are
  deliberately not wired here.

## Capabilities

### New Capabilities

- `python-interpreter-floor-preflight`: worktrail verifies the resolved Python interpreter
  surface against the target repository's declared `requires-python` floor before launching
  orchestrated work, failing loudly with the remediation instead of a downstream parse error.

### Modified Capabilities

## Impact

- New `src/worktrail/router/check_interpreter_floor.py`, a `worktrail-check-interpreter-floor`
  entry point in `pyproject.toml`, and `tests/router/test_check_interpreter_floor.py`.
- `src/worktrail/orchestrator/live.py`: `full-real` refuses a below-floor environment before any
  spawn; `precheck` reports the floor failure first;
  `tests/orchestrator/test_live_interpreter_floor_guard.py`.
- `skills/worktrail-go/references/subagent-prompts.md`: the `#precheck-gate` reaction text
  distinguishes the environment abort from a task-DAG warning.
- No change to how gate commands are executed and no new dependency: the check is stdlib-only,
  read-only, and spends no model calls.
