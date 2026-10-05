## Why

`worktrail-live full-real` runs the policy's gates (`integrate_smoke_cmd`, `pre_pr_cmd`) through
PATH-resolved interpreter words — `.worktrail/policy.yaml` pins `python3.14`, and
`worktrail-detach launch` hands the detached orchestrator the caller's PATH unchanged. On this
host `python3.14` resolves to `/home/briank/.local/bin/python3.14`, a valid 3.14 that is **not**
the repository's development environment: `importlib.util.find_spec("pytest")` in it returns
`None`. In run `go-20261004-093132` (change `model-tier-routing-env-profile-error-provenance`)
the integrate smoke failed both attempts with `exit 1: /home/briank/.local/bin/python3.14: No
module named pytest`, then quarantined every group (`!! QUARANTINED [feature-1]
integration_error`) after ~16 minutes of fan-out; no PR was opened for that group, and the change
landed only because the tail group re-integrated the same task branch into its own PR. Re-running
the identical command with the repository-local `.venv/bin` first on PATH passed (7147 passed,
770.79s) — the code was never wrong, the orchestrator process's interpreter resolution was.

`AGENTS.md`'s Development section already documents the hazard and its fix ("Point PATH at an
interpreter that has them — on this machine that is the repo-local, gitignored `.venv`"), but
nothing *checks* it: the `#orchestrator` launch block in the `worktrail-go` skill does not put
that directory on PATH, and `full-real` fans out on whatever environment it inherited. A hazard
that is documented but unverified still cost a full fan-out.

The adjacent guard does not cover this surface. `python-interpreter-floor-preflight` (in flight,
authored concurrently) probes interpreter **versions** against the target repository's declared
`requires-python` floor, and its `design.md` Non-Goals exclude "commands that resolve their own
interpreter". A 3.14 that satisfies every declared floor with an interpreter that cannot run the
gate's own tooling passes that check and still quarantines every group.

## What Changes

- Add `worktrail-check-gate-interpreter`: it resolves the Python interpreter words declared in the
  target repository's `pre_pr_cmd`, `pre_commit_cmd`, and `integrate_smoke_cmd` through PATH the
  way a shell would, and for each resolved interpreter verifies that every tool that command asks
  it to run via `-m <module>` is present — without executing any gate command and without
  importing repository code. It reports every missing or undeterminable tool in one invocation,
  naming the interpreter word, its resolved path, the tool, the policy key and command that named
  it, and a remediation, and exits non-zero on findings.
- Refuse the launch before work starts: `worktrail-live full-real` runs the check for its target
  repository before the run lock, any worktree, the run journal, or any spawn, and aborts non-zero
  through a dedicated error carrying the findings.
- Make the documented launch path resolve the repository's own interpreter: the `#orchestrator`
  launch block prepends the repository-local `.venv/bin` (the target checkout's, then the spec
  checkout's, when each exists) to PATH in its own shell before it detaches — so the interpreter
  the gates will actually resolve already is the repository's — and runs the check interactively
  so a still-broken environment is reported before the fan-out rather than after it.
- Deliberately unchanged: `full-real` does not rewrite its own PATH (the check refuses a broken
  environment; it does not silently choose an interpreter for it), there is no environment-variable
  bypass, and `precheck` is not wired (see `design.md`).

## Capabilities

### New Capabilities

- `gate-interpreter-preflight`: worktrail verifies that the interpreters its policy's gate commands
  resolve can actually run the tools those commands invoke, and refuses an orchestrated launch
  whose own gates cannot execute instead of spending a full fan-out to quarantine every group.

### Modified Capabilities

## Impact

- New `src/worktrail/router/check_gate_interpreter.py`, a `worktrail-check-gate-interpreter` entry
  point in `pyproject.toml`, and `tests/router/test_check_gate_interpreter.py`.
- `src/worktrail/orchestrator/live.py`: `full_real()` refuses a gate-incapable environment before
  any spawn; `tests/orchestrator/test_live_gate_interpreter_guard.py`.
- `skills/worktrail-go/references/subagent-prompts.md`: the `#orchestrator` block prepends the
  repository-local `.venv/bin` and runs the check before detaching.
- No new dependency: the check is stdlib-only, read-only, and spends no model calls. (Work-queue
  brief `20261004-110541-interpreter-path-missing-dev-extras`.)
