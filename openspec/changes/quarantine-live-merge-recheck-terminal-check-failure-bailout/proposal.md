## Why

`Verifier._wait_for_external_merge()` (`src/worktrail/orchestrator/verify.py:1271-1291`)
polls only for `state == "MERGED"`. It never inspects `statusCheckRollup`, so when a
PR's required checks have already COMPLETED with FAILURE while GitHub keeps the
externally-armed auto-merge request armed (waiting for a new commit this run will never
push), the merge is structurally impossible — yet the function still burns its full
`max_polls` budget (360 polls with `1.4**poll` backoff capped at `poll_interval_max`,
roughly 5-6 hours at live settings) before returning False and quarantining. It also
emits no per-poll log line, so during that wait the process is indistinguishable from
hung.

Observed live: worktrail PR #958 on 2026-09-04 (run `go-20260904-113146`, change
`orchestrator-review-loop-bounded-convergence`) — `autoMergeRequest` armed at 19:12:45Z,
`mergeStateStatus` BLOCKED and unchanged from 19:40:02Z with both required checks
("Lint, Test & Build" and "Scope check") COMPLETED/FAILURE, and the process sitting in
`hrtimer_nanosleep` with no new log line for 38+ minutes.

## What Changes

- **Terminal-failure bail-out in the bounded external-merge wait.** Inside
  `_wait_for_external_merge`'s poll loop, after the existing MERGED test, classify the
  PR's live required checks with the idiom `_block_on_checks` already establishes:
  `classify_checks(st.get("statusCheckRollup"), required=self._required_check_names())`.
  When the result is NOT pending AND `failing` is non-empty, return `False` immediately
  with a reason naming the failing checks — going straight to quarantine instead of
  consuming the remaining poll budget.
- **Per-poll observability.** Emit one log line per iteration, mirroring
  `_block_on_checks`'s own pattern (group name, what it is waiting on, poll number), so
  the wait is visible in the run log rather than silent.

Explicitly unchanged: `max_polls`, `poll_interval`, `poll_interval_max`; the passive
posture (no merge is armed or attempted); `_block_on_checks`, `auto_merge()`, and every
other call site; and behavior when checks are still pending or the rollup is empty — a
legitimate wait must not be shortened.

Not a breaking change: it only ends a wait early in a case where the wait's own outcome
was already fixed at `False`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `quarantine-live-merge-recheck`: the "Bounded wait for an externally-armed auto-merge
  before finalizing quarantine" requirement gains two properties — the bounded wait also
  ends early when the PR's required checks have terminally failed (naming those checks in
  the quarantine reason), and the wait is observable per poll instead of silent.

## Impact

- `src/worktrail/orchestrator/verify.py` — `Verifier._wait_for_external_merge()` only.
- `tests/orchestrator/test_verify.py` — regression coverage added to the existing
  `LiveMergeRecheckUnit` class, using the harness already present there (`FakeRun`,
  `view(...)`, the `GREEN`/`RED` rollup fixtures, `mk()`).
- No API, dependency, or configuration surface changes; no policy keys added.
