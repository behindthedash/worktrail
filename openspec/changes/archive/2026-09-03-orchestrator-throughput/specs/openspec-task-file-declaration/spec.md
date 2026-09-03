## ADDED Requirements

### Requirement: Inline review declaration parsing
An OpenSpec `tasks.md` task line MAY be followed, within the same continuation window as `files:`, by an indented `review:` line whose value is a single token. The parser SHALL surface that value on the task as its `review` field so the orchestrator's review-exempt fast path can honor `skip`. A task with more than one `review:` line SHALL keep the first and record a warning; a `review:` line with no value SHALL record a warning and leave the field unset. The compile step SHALL preserve an authored `review` value over one inferred by the model.

#### Scenario: review skip is parsed
- **WHEN** a task line is followed by `  review: skip`
- **THEN** the parsed task's `review` field is `skip` and the orchestrator treats the task as review-exempt

#### Scenario: files and review coexist
- **WHEN** a task line is followed by `  files: .worktrail/policy.yaml` and `  review: skip` in either order
- **THEN** the parsed task carries both the declared file and the review value

#### Scenario: Missing review value warns
- **WHEN** a task line is followed by `  review:` with nothing after the colon
- **THEN** the parse result records a warning naming the task and the task's `review` field is empty
