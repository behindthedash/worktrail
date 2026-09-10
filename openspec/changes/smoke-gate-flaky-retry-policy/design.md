## Context

`_run_integration_smoke(iw, name, smoke_cmd)` returns `(ok, detail)`; the call
site in `integrate_one` quarantines on `ok is False` before push/PR. The smoke
command is an arbitrary operator shell string from
`.worktrail/policy.yaml` (`integrate_smoke_cmd`), reaching `integrate_one` via
`live.py`'s `--smoke-cmd` / `_default_smoke_cmd` plumbing. The brief's three
open questions are settled below.

## Decisions

### D1. Retry is opt-in per repository, default off

A new `integrate_smoke_retries` policy key (integer >= 0, default 0) gates the
behavior. A repo that has not set it gets byte-identical gate behavior: one
run, quarantine on first red. Rationale: every existing repo and test fixture
was tuned against a zero-tolerance gate, and whether a suite's reds are
"usually load flakes" or "usually real" is a per-repo fact only the repo
author knows. Making tolerance the default would silently weaken the gate for
repos whose suites are deterministic. The key is an integer rather than a
boolean so an operator can express "one retry" (the brief's ask) without a
second key if a suite ever warrants two; the orchestrator does not cap it,
but the documentation recommends `1`.

The count is read from policy in `live.py` (a `_default_smoke_retries(repo)`
sibling of `_default_smoke_cmd`) and forwarded as an `integrate_one`
keyword. No CLI flag: unlike `--smoke-cmd`, there is no scenario where the
calling agent needs to override the repo's own tolerance setting per run.

### D2. The retry is unconditional, not diff-scoped

The brief asked for a retry "when the failure is outside the group's own
diff". That needs a failing test node id to map to a source path, and
`integrate_smoke_cmd` is an opaque shell string (`PYTHONPATH=src pytest -q &&
orchestrate check` in this repo, `cd app && npm ci && npm test` elsewhere).
There is no runner-agnostic way to recover a failing test id from a captured
tail, and `pre_pr_gate.changed_paths()` gives the diff side only. Guessing
from output text would be a heuristic that silently degrades per runner.
Decision: when opted in, every non-zero exit is eligible for retry regardless
of what failed. The diff-scoping idea is recorded as rejected, not deferred;
D3 is the mitigation for the masking risk it was meant to address.

### D3. Pass-after-retry is evidence, never a clean pass

A retry can mask a genuine intermittent defect. The mitigation is that the
first failure is never discarded: `_run_integration_smoke` returns the
first-attempt failure tail on a pass-after-retry (a third element, or
`detail` carrying it, whichever keeps the existing `(ok, detail)` callers
working), the call site prints a `FLAKY [<group>]` line naming the attempt
counts and the tail, and the run journal gains a top-level `smoke_flakes`
map (`{group_name: "attempt 1 exit N: <tail>"}`) written by a small
`_record_smoke_flake(journal_path, name, detail)` helper that follows
`record_unreconciled_tail`'s direct atomic-write pattern. That keeps the
per-group record schema (`_write_group_journal` / `_record_group`) untouched
and the evidence durable across a cold `continue`. The group proceeds to push
and PR exactly as a first-attempt pass would.

### D4. Only non-zero exits retry

`subprocess.TimeoutExpired` and `OSError` return failure immediately on the
first attempt, as today. A timeout already consumed `ORCH_SMOKE_TIMEOUT`
seconds (default 1800); re-running it would double the gate's cost for a
failure class that is not what the brief observed. A spawn error is
deterministic. Exhausting retries quarantines with
`QUARANTINE_INTEGRATION_ERROR` and the last attempt's tail, so downstream
quarantine handling is unchanged.

## Alternatives Considered

- **Default-on single retry:** rejected per D1; weakens deterministic suites'
  gate without their authors opting in.
- **Runner-specific test-id parsing (pytest `FAILED` lines) with
  `changed_paths()` scoping:** rejected per D2; worktrail is runner-agnostic
  and the fallback for unrecognized output would be either "never retry"
  (no fix) or "always retry" (this design, with extra code).
- **Filing a work-queue brief per flake:** deferred; the journal entry is
  the minimum durable evidence, and brief creation from the orchestrator is
  a separate policy question.
