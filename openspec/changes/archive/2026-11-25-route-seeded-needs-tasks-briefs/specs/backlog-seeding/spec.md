## ADDED Requirements

### Requirement: Seeded task-generation requests route to planning

The route classifier SHALL choose Route C when a handoff recommends Route C, its focus identifies the needs-tasks dashboard stage and requests spec-to-tasks, and its otherwise high-confidence Route D selection is based solely on the spec ID and approval signals. An explicit forced route SHALL take precedence.

#### Scenario: Approved spec has no task DAG

- **WHEN** a needs-tasks handoff recommends Route C and its spec ID and approval would otherwise produce a high-confidence Route D selection
- **THEN** classification selects Route C without requesting a manual override

#### Scenario: Caller explicitly forces a route

- **WHEN** the caller forces a route for a needs-tasks handoff
- **THEN** classification honors the forced route
