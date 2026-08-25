## Why

Worktrail's guard flows currently couple a decision to the prompt API available in the executing host. That leaves interactive Codex/OpenCode runs without a reliable equivalent to Claude's `AskUserQuestion`, while headless adapter and drain runs can guess, fail opaquely, or strand a claimed brief.

## What Changes

- Add a provider-neutral pending-user-decision envelope for go guard results, with stable decision identity, typed options, source/run/brief context, and explicit resume data.
- Make the worktrail-go/skill-dispatch boundary return, persist, render, answer, and resume this envelope without embedding provider UI in guard or orchestrator logic.
- Map attended Claude, Codex, and OpenCode execution to their host-supported prompt/resume path; make unattended adapter and drain execution fail closed with a structured `pending_user_decision` result.
- Preserve audit history and brief-claim recoverability across native Skill, adapter, subprocess, and drain dispatch modes.
- Add a provider/dispatch-mode contract matrix covering collision, staleness, and related-brief guards.
- Link to the existing `human-decision-queue` capability for durable storage and operator answering without modifying that capability's requirements.

## Capabilities

### New Capabilities

- `pending-user-decision-dispatch`: Provider-neutral guard prompt and resume behavior at the worktrail-go/skill-dispatch boundary.

### Modified Capabilities

None. The existing `human-decision-queue` requirements remain unchanged and are consumed as a durable decision-storage dependency.

## Impact

- `src/worktrail/router/` guard, run-record, dispatch, and resume surfaces.
- `src/worktrail/orchestrator/` result contracts where orchestration cannot begin before a guard decision.
- `src/worktrail/drain/` terminal-state and claim-recovery handling.
- `skills/worktrail-go/` and `skills/worktrail-sdd-workflow/` procedure text.
- Router, workqueue, adapter, drain, and plugin-surface tests; no new external dependency.
