## Why

A refusal is the pipeline's answer to "why didn't my PR land?", but two of its refusal
paths throw away the answer. The pr-landing-pipeline requirement *Refusal leaves the
remote untouched* says the pipeline SHALL "return a refused outcome naming the failed
step **and its output**". Today `refused_step` is the entire diagnostic on the two most
common refusals -- a failing pre-PR gate and an uncommittable dirty tree -- and a step
name alone does not name a cause: `"dirty_tree"` is returned from four different
failures, and `"preflight"` from four more, with no text distinguishing them.

Verified against the real functions on base `36af09a1` (not a mock): `_run_preflight_and_labels`
on a worktree whose preflight denies returns `("preflight", [])` while the refusal reason
(`PRE-PR GATE: FAIL -- unconfigured (default-deny) ...`, 353 chars) is written to **stderr**
and captured stdout is empty. A stdout-only capture would therefore record an empty detail
for exactly the most common refusal, so the fix must capture both streams.

## What Changes

- `_run_preflight_and_labels` captures the pre-PR gate's stdout **and** stderr while
  running `preflight.main(argv)` in-process, and returns that output as a new `detail`
  element of its result tuple at all four of its refusal points (SystemExit from an
  invalid `--risk`, non-zero gate exit, unreadable pass marker, stale marker state).
  This mirrors the existing sibling helper `_run_record_main` (same module), which
  already solves the identical problem for the run-record module: run the module's
  `main(argv)` under `redirect_stdout`/`redirect_stderr`, keep a string `SystemExit`
  code as the detail, otherwise fall back to captured stderr.
- `land_pr()` threads that detail into `LandOutcome(detail=...)` at the preflight
  refusal site, matching the compile-marker and push refusal sites beside it.
- `_commit_pending` returns a cause-specific detail alongside its refusal, so the four
  distinct failures (`git status` failed, no `commit_message` supplied, `git add` failed,
  `git commit` failed) are distinguishable, with the failing git subcommand's stderr
  included where there is one.
- `land_pr()` threads that detail into `LandOutcome(detail=...)` at the dirty-tree
  refusal site.
- Regression tests that exercise the **real** functions -- the existing
  `test_preflight_failure_refuses_and_never_pushes` mocks `_run_preflight_and_labels`
  itself and asserts nothing about detail, so it cannot catch this defect.
- The `pr-landing-pipeline` spec's *Refusal leaves the remote untouched* requirement is
  sharpened so "and its output" is unambiguous per refusal type, with scenarios covering
  the two repaired paths.

Not a behavior change: the same inputs refuse at the same steps and still leave the
remote untouched. Only the refusal's diagnostic content changes.

## Capabilities

### New Capabilities

<!-- None: this restores conformance with an existing requirement. -->

### Modified Capabilities

- `pr-landing-pipeline`: the *Refusal leaves the remote untouched* requirement is
  sharpened -- every refusal path must populate the refused outcome's `detail` with the
  failing step's own output, with explicit scenarios for the preflight denial (gate
  output) and the dirty-tree refusal (which sub-cause fired). The *Preflight denies*
  scenario's "quotes the gate's output" gains a named scenario of its own in the refusal
  requirement, and *Labels are computed by the preflight gate* is left unchanged.

## Impact

- `src/worktrail/router/land_pr.py` -- `_run_preflight_and_labels` (return shape and
  docstring), `_commit_pending` (detail), and `land_pr()`'s two refusal call sites.
- `tests/router/test_land_pr.py` -- five existing `_run_preflight_and_labels` unit tests
  and `test_preflight_failure_refuses_and_never_pushes` unpack the current two-element
  tuple and must be updated; new tests exercise the real functions end to end.
- Other `_run_preflight_and_labels` callers in tests (`test_land_pr_resume.py`,
  `test_land_pr_push_refusal.py`, `test_pr_creation_callsite_enforcement_coverage.py`,
  `test_land_pr_blocked_required_contexts.py`) unpack the return value and need the
  same update.
- No CLI, entry-point, or `LandOutcome`/JSON shape change: `detail` and
  `refused_step` already exist and are already emitted; `refused_step` keeps its
  current values, including `"dirty_tree"`, so the documented invocation contract in
  `tests/router/test_documented_land_pr_invocations.py` is unaffected.
