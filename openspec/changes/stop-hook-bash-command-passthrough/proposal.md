## Why

`check_dedup_gate` in `hooks/suggest_next_step.py` only sends `--touched-path` and
`--run-record` to `worktrail-check-durable-artifact-capture-gate`. The checker's
`merge_markers_in(bash_commands)` (`src/worktrail/router/check_durable_artifact_capture_gate.py`)
therefore always receives `[]`, so `find_hits` can never emit a `merged_docs_only_spec_pr` hit
from the hook — durable-artifact-dedup-gate's own Requirement: Merged Docs-Only Spec PR
Detection Is Transcript-Local, Scenario "In-session spec merge detected" is unreachable in
practice even though the checker itself implements it correctly (confirmed by
`tests/router/test_check_durable_artifact_capture_gate.py`). Verified 2026-09-12 by `rg`: the
only callers of `--bash-command` are the checker module and its own test file; `hooks/`
never passes it. Out of scope for OpenSpec change `stop-hook-deferral-flag-always-capture-bugs`
(fixes-only, two other named problems, neither requirement it touched is this one).

## What Changes

- `scan_transcript` collects each Bash tool call's raw command text during its existing single
  transcript pass (alongside the run-record and durable-artifact-path signals it already
  collects) and returns it as a fourth value.
- `check_dedup_gate` accepts that collected command list and forwards it to the checker via
  repeated `--bash-command` flags, so `merge_markers_in` finally sees real command text.
- The `durable-artifact-dedup-gate` spec's Merged-Docs-Only-Spec-PR requirement is clarified to
  state the Stop hook's own responsibility to source and forward this evidence — the missing
  half of the contract that let the gap go undetected.

## Capabilities

### New Capabilities

### Modified Capabilities
- `durable-artifact-dedup-gate`: the Stop hook now actually feeds Bash command text to the
  merged-docs-only-spec-PR detector, closing the gap that made that hit path dead code.

## Impact

- `hooks/suggest_next_step.py` (`scan_transcript`, `check_dedup_gate`, `main`).
- `hooks/test_suggest_next_step.py` (existing `scan_transcript` unpacking updated for the new
  return value; new coverage for Bash-command collection and the end-to-end
  `merged_docs_only_spec_pr` hit through `main()`).
- No change to `worktrail-check-durable-artifact-capture-gate` itself — its `--bash-command`
  flag and `merge_markers_in` already work; only the caller was incomplete.
