## Context

See `proposal.md` — Why. The design-relevant shape of the current code:

- `pre_pr_gate.py:main()` is one linear function. `--labels-only` returns at `:361`; the
  four drift checks live at `:373-417`, each guarded by `if not args.print_cmd`; the
  docs-only bypass, `pre_pr_cmd` resolution, and the `subprocess.run(["bash","-c",cmd])`
  execution follow. Exit codes are module constants: `SPEC_SYNC_DRIFT_EXIT = 1`,
  `CLARIFICATION_INTEGRITY_DRIFT_EXIT = 3`, `DOD_VERIFICATION_DRIFT_EXIT = 4`,
  `REQ_AC_COVERAGE_DRIFT_EXIT = 5`, `UNCONFIGURED_EXIT = 2`.
- Three of the four checks are diff-scoped through `changed_paths(repo, policy)`, which
  runs `git merge-base HEAD <base>` and `git diff --name-only` with `cwd=repo`. Only
  `spec_sync_drift` is whole-tree. This is the crux: the tree the gate is pointed at
  *is* the check population.
- `integrate_one` (`integrate.py:808`) already owns an integration worktree `iw`, already
  runs a subprocess against it (`_run_integration_smoke`, `:637`), and already has an
  established quarantine-and-bail shape at `:1030-1035`.
- `_resolve_pre_pr_gate()` (`:69-80`) resolves the gate script via `PRE_PR_GATE_SCRIPT`
  env override → `shutil.which("worktrail-pre-pr-gate")` → repo-local
  `router/pre_pr_gate.py`. The env override exists precisely so tests can substitute a
  stub; the new call site reuses it.

## Goals / Non-Goals

**Goals:**

- The four drift checks run on every orchestrator group PR, against the right tree, with
  the same logic and the same exit codes as the one-off route path — no second
  implementation that can drift from the first.
- Fail fast and cheap: the deterministic checks run before the smoke command.
- A drift quarantine is distinguishable from every other quarantine cause in the journal.

**Non-Goals:**

- Scope-completeness review (`--run`-gated) — see `proposal.md` Impact.
- Any change to `--labels-only`'s behavior, callers, or output.
- Any new deterministic check. This change relocates enforcement; it invents no new
  assertion.
- Making the drift gate configurable or opt-out-able per repo. The checks already no-op
  on repos that lack the artifacts they inspect (`spec_sync_drift` returns `[]` with no
  `docs/specs/`; the diff-scoped checks receive an empty path list); an explicit opt-out
  would recreate exactly the silent-bypass hole this change closes.

## Decisions

### D1: Extract the four checks into `run_drift_checks(repo, policy) -> int`, call it from both paths

The four check blocks move verbatim into one helper returning the drift exit code (or 0),
with the existing `print(..., file=sys.stderr)` reporting retained inside it. `main()`'s
default path calls it where the blocks used to be; `--checks-only` calls it and returns.

*Alternative rejected:* duplicate the check sequence in a separate `--checks-only` branch.
That is exactly the failure mode being fixed — `check_dod_failures` and
`check_req_coverage_failures` were each added to *one* code path and missed the other. A
single shared helper makes the next added check reach both call sites by construction.

*Note on `if not args.print_cmd`:* the checks are currently nested under that guard.
`--print-cmd` and `--checks-only` are mutually exclusive in intent; the helper is called
from the default path under the same `not args.print_cmd` guard it has today, so
`--print-cmd` behavior is byte-identical.

### D2: `--checks-only` short-circuits before policy command resolution, not after

The mode returns as soon as `run_drift_checks` does. It never reaches `resolve_cmd`, so a
repo with **no** `pre_pr_cmd` configured does not fail the orchestrator with
`UNCONFIGURED_EXIT = 2`. That default-deny is correct for the one-off route (which relies
on the gate to run the tests) and wrong here (`integrate.py` runs the repo's smoke command
itself, from `integrate_smoke_cmd`). Likewise it never reaches `is_docs_only`: the
docs-only bypass exists to skip an expensive *test command*, and there is no test command
to skip in this mode. Skipping the bypass is also strictly safer — the drift checks run on
docs-only group diffs too, which is where spec drift lives.

`--checks-only` also does not accept or evaluate `--risk`/`--labels-only`; label
resolution stays entirely in `_refresh_pr_labels`'s existing call.

### D3: New quarantine reason code `QUARANTINE_PRE_PR_DRIFT = "pre_pr_drift"`

*Chosen over reusing `integration_error`.*

For: the drain stage-remediation table and journal analytics both key off
`quarantine_reason`. `integration_error` today means "the merged tree fails its own tests"
— remediation is *fix the code*. Pre-PR drift means "the tree is fine but the spec/task
bookkeeping contradicts it" — remediation is *update the spec artifact*, usually a
one-command fix (`worktrail-check-spec-sync`, `worktrail-check-dod-verification`). Folding
two different remediations under one code makes the table lie.

Against: every consumer that switches on the reason code must learn the new value, and any
consumer that treats unknown codes as fatal would break. Mitigated in Risks below.

The drift exit code itself (1/3/4/5) is *not* mapped to four distinct quarantine codes —
one code plus the stderr excerpt in the human-readable reason is enough to route
remediation, and four codes would multiply the consumer-update surface for no additional
routing power.

### D4: Placement — after `_write_group_task_status`, before `if smoke_cmd:`

Non-negotiable ordering, for two independent reasons:

- **After** the status write (`:1028`): DoD verification's entire population is the set of
  task files the orchestrator just stamped `status: completed`. Running before the write
  would verify nothing.
- **Before** the smoke block (`:1029`): the drift checks are seconds; the smoke command is
  a full test suite. Failing fast saves the suite on every drift failure.

Both precede the push at `:1036` and PR creation at `:1119`, satisfying "no drifted PR is
ever created" — and also "no drifted branch is ever pushed", which is stronger and free.

### D5: `_run_drift_gate(iw, name, route)` mirrors `_run_integration_smoke`'s contract

Returns `(ok, detail)`. Uses `_resolve_pre_pr_gate()` (reusing the `PRE_PR_GATE_SCRIPT`
test override), `subprocess.run(capture_output=True, text=True, timeout=...)`, and
fail-closed handling: `TimeoutExpired`, `OSError`, and an unresolvable gate script all
return `(False, ...)`. This is the opposite of `_refresh_pr_labels`, which returns `None`
on every error and falls back to the caller's labels — correct for an advisory label
refresh, wrong for a gate. On failure, `detail` carries a short tail of stderr, matching
`_run_integration_smoke`'s excerpt convention.

The `--repo` argument is `str(iw)`. That is the single most important line in this change;
`str(repo)` would silently pass everything, which is why it gets its own regression test
rather than being covered incidentally by the happy-path test.

### D6: The gate runs on every group, sequentially, inline in `integrate_one`

No batching, no carrier-only special case. Per `proposal.md`, non-carrier groups have
their spec folder stripped, so the three spec-scoped checks find nothing; only
DoD-verification does real work there, and that is precisely the check whose population
spans all groups. `integrate_one` is already the per-group serialization point, so no new
concurrency reasoning is introduced.

## Risks / Trade-offs

- **A new quarantine reason code breaks a consumer that enumerates codes** → verified low:
  no consumer enumerates the full set. `quarantine_selfcheck.py:226` and `drain.py`'s
  `quarantined_budget_exhausted` remediation row both special-case *only*
  `QUARANTINE_BUDGET_EXHAUSTED` ("never actually failed, safely auto-resumable") and treat
  everything else as a real failure needing human review — which is the correct default
  for a drift quarantine, so no new remediation row is required. A confirming grep for
  each constant's string value before wiring the new one is still an explicit task.
- **Latent pre-existing drift now surfaces as mass quarantine on the first orchestrated
  run after this lands** → this is the change working as intended, but it can look like a
  regression. The three diff-scoped checks are inherently ratcheted (they only inspect
  paths in the group's own diff), and `check_req_coverage` additionally enforces only
  identifiers newly declared in the diff, so pre-existing tree-wide drift does not fail a
  group that did not touch it. `spec_sync_drift` is the one whole-tree check and is the
  realistic source of a surprise quarantine; the quarantine reason names the failing spec
  and the fixing command, and quarantine is recoverable by re-running after the fix.
- **The gate adds a subprocess per group** → bounded: the checks are file reads plus two
  `git` invocations, run once per group, guarded by a timeout, and they run *before* the
  far more expensive smoke command rather than in addition to a second one.
- **Extracting `run_drift_checks` could subtly change the one-off route's behavior** →
  the extraction is verbatim, including the stderr messages and the `changed_paths(repo,
  policy) or []` calls; the existing `tests/router/test_pre_pr_gate.py` suite covers that
  path and must pass unchanged.
- **`--checks-only` skipping `UNCONFIGURED_EXIT` weakens default-deny for that mode** →
  accepted deliberately (D2). Default-deny protects against *no test gate*; in this mode
  the caller (`integrate.py`) owns test execution, so the deny would fire on a correctly
  configured repo. The drift checks themselves remain unconditional.

## Migration Plan

Single PR, no data migration, no consumer-repo change. Rollback is a revert: the new mode
is additive and the new call site is one block; nothing else changes behavior. Consumer
repos (datalena, GGB) need no edit — they pick this up when they bump their pinned
`worktrail` version, per this repo's versioning contract.
