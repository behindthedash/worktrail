## Why

The scope-review gate still refuses a terminal `finish` when a caller-supplied
run has no complete scope review (`pre_pr_gate.py:365`). `land_pr()` also has
terminal paths that report `completed_and_merged`. The supplied checkpoint
invocation and its observed result have not been confirmed, so this change
must establish the narrow checkpoint contract and exercise it rather than
claiming an incident-specific fix is complete.

## What Changes

- Specify that a checkpointed landing which observes an already merged PR
  records the `completed_and_merged` outcome as a decision and leaves the run
  record non-terminal.
- Add regression coverage that uses a caller-supplied run without scope-review
  entries, proving the checkpoint path does not invoke the terminal
  scope-review gate or report a recoverable failure for that reason.
- Preserve normal, non-checkpoint completion and all other landing outcomes.

## Capabilities

### New Capabilities

- `checkpointed-pr-landing-status`: checkpoint-mode status recording for an
  already merged pull request.

### Modified Capabilities

None.

## Impact

- `src/worktrail/router/land_pr.py`: checkpoint status handoff, only if the
  regression test exposes a mismatch with the specified behavior.
- `tests/router/`: focused merged-checkpoint and scope-gate regression
  coverage.
- No CLI, run-record schema, dependency, or non-checkpoint behavior change.
