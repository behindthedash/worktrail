## Why

The orchestrator's integrated smoke gate (`_run_integration_smoke` in
`src/worktrail/orchestrator/integrate.py`) runs the repo policy's
`integrate_smoke_cmd` exactly once in a group's integration worktree and, on
any non-zero exit, the call site quarantines the group with
`QUARANTINE_INTEGRATION_ERROR` before any push or PR. There is no retry of any
kind. During run `agent-capacity-expired-gate-hygiene` (2026-09-05), two
load-flaky test reds unrelated to the group's own diff quarantined two of three
groups and cost a tail-2.1 ci-fix cycle. PR #1019 (`2c060112`) removed that
specific flake by making the fixed subprocess waits in `tests/router`
load-tolerant, but the gate's zero-tolerance shape is unchanged: the next
unrelated transient red quarantines a group the same way. `live.py` already
tolerates one CI red per group via its ci-fix retry, so the pre-PR gate is
stricter than the post-PR gate it exists to front-run.

The work-queue brief `20260905-221937-smoke-gate-retry-flaky-failures` raised
three design questions the human decision
(`dec-20260905-221937-smoke-gate-retry-0836147bdfe6`, answered "proceed as
scoped") left to the proposal: whether retry is opt-in or default, whether the
retry can be scoped to failures outside the group's diff, and how to keep a
retry from masking a genuine intermittent defect. `design.md` settles all
three.

## What Changes

- A new repository policy key, `integrate_smoke_retries` (integer, default
  `0`), in `src/worktrail/router/policy.py`. `0` keeps today's single-run,
  fail-on-first-red behavior for every repo that does not set it. Invalid
  values (non-integer, boolean, negative) are dropped to the default with a
  `_meta` warning, matching the existing integer-key validation.
- `_run_integration_smoke` accepts a `retries` count. When the command exits
  non-zero and retries remain, it re-runs the same command in the same
  worktree; the group passes if any attempt exits 0. A timeout or spawn error
  is never retried: those are structural, and re-running a timed-out command
  doubles the gate's wall-clock for no evidence gain.
- A pass-after-retry is never silent. The runner returns the first attempt's
  failure tail alongside the pass, the call site prints a `FLAKY` line, and the
  run journal gains a top-level `smoke_flakes` map (group name to first-attempt
  detail) written the same way `record_unreconciled_tail` writes its evidence.
  A group that fails every attempt is quarantined exactly as today, with the
  last attempt's tail in the quarantine reason.
- `live.py` resolves the retry count from policy next to `_default_smoke_cmd`
  and forwards it to `integrate_one` as a new keyword, mirroring `smoke_cmd`.
- The retry is not diff-scoped. `integrate_smoke_cmd` is an opaque operator
  shell string, so no failing test id can be recovered from it and no
  changed-paths comparison is possible; see design.md D2.
- Docs: the `worktrail-go` subagent-prompts integrated-smoke bullet and the
  `.claude/skills/worktrail/skill.md` gate notes describe the key and the
  flake evidence.

## Capabilities

### New Capabilities

- `integration-smoke-retry-policy`: opt-in bounded retry of the integrated
  smoke gate, with the first-attempt failure preserved as run evidence.

### Modified Capabilities

(none)

## Impact

- `src/worktrail/router/policy.py` -- `DEFAULTS` entry and integer validation
  for `integrate_smoke_retries`.
- `src/worktrail/orchestrator/integrate.py` -- `_run_integration_smoke` retry
  loop and flake return value; `integrate_one` `smoke_retries` keyword, `FLAKY`
  line, and `_record_smoke_flake` journal write.
- `src/worktrail/orchestrator/live.py` -- `_default_smoke_retries` and the
  `integrate_kwargs["smoke_retries"]` plumbing.
- `tests/router/test_policy.py`, `tests/orchestrator/test_integrate_complete.py`,
  `tests/orchestrator/test_default_smoke_cmd.py` -- coverage for each.
- `skills/worktrail-go/references/subagent-prompts.md`,
  `.claude/skills/worktrail/skill.md` -- documentation.

Out of scope: retrying `post_merge_smoke_cmd` (verify.py's cumulative gate)
or `pre_pr_cmd` (the one-off pre-PR gate), parsing test ids out of the smoke
command, filing a work-queue brief per observed flake, and any change to the
`WORKTRAIL_TEST_TIMEOUT` mechanism from PR #1019.
