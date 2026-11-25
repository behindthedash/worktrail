## Context

The seeder already emits Route C and stable focus text identifying the needs-tasks dashboard stage and the spec-to-tasks action. The classifier receives the recommendation and focus, and its generic confidence gate excludes high-confidence disagreements.

## Goals / Non-Goals

Restore planning for the generated needs-tasks request and preserve other decisions, including explicit forced routes.

## Decisions

Use the existing Route C recommendation and explicit task-generation evidence in the focus to recognize the D-to-C exception. Bound it to the documented false-positive shape where D has only spec ID and approval signals. This works with existing queued briefs and uses the CLI inputs already passed by the front door.

## Risks / Trade-offs

The match depends on the seeder's focus wording. Exercise generated focus and the incident wording in regression coverage to expose drift.
