## Why

Unattended drains can try to implement approved specs whose task DAG has yet to be created. The seeded brief recommends planning, but the classifier reads the spec ID and approval as high-confidence implementation signals.

## What Changes

- Honor Route C for needs-tasks handoffs when the request explicitly identifies the missing DAG and asks for task generation.
- Preserve explicit route overrides and existing behavior for other high-confidence disagreements.

## Capabilities

### Modified Capabilities

- `backlog-seeding`: Route planning-only task-generation briefs to planning when implementation signals come from the approved spec reference.

## Impact

The classifier and its regression tests change. The active task-ID normalization proposal addresses a separate dashboard and seeder condition and requires no shared source edits.
