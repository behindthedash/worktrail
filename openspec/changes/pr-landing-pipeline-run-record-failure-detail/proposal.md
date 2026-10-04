## Why

The capability's *Run record is completed with a real state* requirement ends: "When the run
record cannot be finished, the landing result SHALL carry the run-record tool's own failure
message as its detail." The pipeline's single in-process run-record wrapper, `_run_record_main`
(`src/worktrail/router/land_pr.py:843`), cannot honor that when the message went to stdout:
its `detail` element is populated only from a string `SystemExit` code or the captured
**stderr** -- the stdout buffer it already holds is never consulted. A non-zero exit with an
empty stderr therefore yields `detail=""`, and the four ceiling returns built to surface
exactly this case (`land_pr.py:1484`, `:1968`, `:2034`, `:2066`, all reading
`"... but run record could not be completed"`) hand that empty string to the caller as the
whole diagnostic -- nothing in the landing result says why the record was not finished.

run_record reports through stdout by convention (every command prints its JSON payload there;
e.g. `assert-terminal` prints its non-terminal diagnostic to stdout at `run_record.py:1707`
and returns 1 at `:1720`), so a failure message carried only by stdout is squarely within the
tool's normal shape -- and the requirement's scenario says "fails for any reason", not
"fails with a message on stderr".

This is the same defect `land-pr-refusal-diagnostics` (#1411, merged 2026-10-03) repaired one
function over. `_preflight_main` -- described by that change as mirroring
`_run_record_main`'s shape -- received the full fallback chain (a string `SystemExit` code
becomes the detail, else the captured stderr, else the captured stdout), pinned by
`test_nonzero_exit_detail_falls_back_to_captured_stdout` (`tests/router/test_land_pr.py:386`).
`_run_record_main` itself was left with the stderr-only channel; this change closes that half.

## What Changes

- `_run_record_main`'s returned `detail` becomes `detail or stderr or stdout` -- the exact
  fallback chain `_preflight_main` already uses -- so the run-record tool's failure message
  reaches the landing result whichever stream carried it, and a stderr-less non-zero exit is
  no longer an empty-detail case. The string-`SystemExit` precedence is unchanged
  (`str(exc.code)` stays authoritative), and the tuple's stdout element (second item) is
  unchanged: `_ensure_run_record` parses `start`'s JSON path line from it.
- Regression tests in `tests/router/test_land_pr.py` cover the capture cases: a stdout-only
  message with `sys.exit(<int>)` (the initially failing reproduction), a string `SystemExit`
  kept verbatim, a clean `sys.exit(0)` staying exit 0, an int exit with stderr present still
  preferring stderr, and the non-`SystemExit` non-zero return that printed only to stdout
  (the same expression's other path).
- The *Run record is completed with a real state* requirement is sharpened with "whether the
  tool wrote that message to stdout or to stderr", mirroring the wording the refusal
  requirement already carries for the preflight gate.

Not a behavior change beyond the diagnostic: the same inputs fail at the same steps and
produce the same outcomes (ceiling, `failed_recoverable`, the same `merge_result`); only the
failure's `detail` gains the tool's own message.

## Capabilities

### New Capabilities

<!-- None: this restores conformance with an existing requirement. -->

### Modified Capabilities

- `pr-landing-pipeline`: the *Run record is completed with a real state* requirement's
  failure-detail clause is sharpened -- the run-record tool's own failure message must reach
  the landing detail no matter which stream carried it -- with the existing
  *Run record cannot be finished* scenario gaining the same stream-independence clause.

## Impact

- `src/worktrail/router/land_pr.py` -- `_run_record_main` (return expression and docstring).
  Every run-record interaction in the landing pipeline goes through this one helper (`start`,
  `set`, `append`, `scope-review`, `finish`), so no call site changes.
- `tests/router/test_land_pr.py` -- new capture tests; no existing test changes. Existing
  simulations of a run-record failure raise a string `SystemExit` (e.g.
  `test_finish_systemexit_string_surfaces_as_ceiling_detail` at `:1356`), whose precedence the
  fix preserves, and `RunRecordSpy` always succeeds (exit 0).
- No CLI, entry-point, `LandOutcome`/JSON shape, or run-record file-format change: `detail` is
  already threaded into `LandOutcome(detail=...)` at every ceiling site.
