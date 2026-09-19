## ADDED Requirements

### Requirement: Work-queue tests never spawn a real headless agent
Tests under `tests/workqueue/` SHALL NOT invoke a real headless agent. A package-scoped autouse
fixture SHALL replace `worktrail.workqueue.create_handoff.spawn_agent` with a stub for every test
in that package, so that a capture exercising a real repo path degrades to the deterministic slug
instead of spawning a worker whose output could contaminate a captured stdout/stderr stream. A
test that deliberately exercises the spawn path SHALL still be able to override the stub with its
own patch.

#### Scenario: Capture with a real repo path does not spawn
- **WHEN** a test under `tests/workqueue/` captures a brief with a `repo` that is an existing
  directory and does not patch the spawn path itself
- **THEN** the real `spawn_agent` is never called and the brief slug is the deterministic
  fallback

#### Scenario: A per-test spawn patch still wins
- **WHEN** a test patches `spawn_agent` itself and captures a brief with a real repo path
- **THEN** its own stub is what the capture calls, not the autouse stub

#### Scenario: CLI human-mode stderr carries only the overlap warning
- **WHEN** `worktrail-create-handoff` is run in human mode with a repo containing an overlapping
  spec directory
- **THEN** the captured stderr holds exactly the one `overlap warning: [spec-slug] ...` line,
  with no agent output interleaved
