## ADDED Requirements

### Requirement: A folded task whose evidence leaves the worktree declares its tail kind

The task a `fold-into-change` apply appends SHALL be tagged with the `e2e` tail kind when its
derived `files:` scope is empty while the brief's focus or the verdict evidence cites at least
one path that exists outside the worktree — the machine-local state beside it that such a
brief's evidence is made of. The tagged task SHALL be rendered with no `files:` line, and its
work is not in the shared tree the target change's workers commit into: compile's scope gate
exempts tail-kind tasks by kind alone, so the folded task stops depending on the model's
per-compile inference for a scope it cannot have, and `e2e` is the tail kind whose dispatch
spawns a worker that runs commands, which is the shape the triage work actually takes. The tag
SHALL be `e2e`, never `cleanup`, whose dispatch is a journal-only status transition that
executes nothing. When the derived scope is non-empty, the appended task SHALL carry it and no
kind tag, as before; when no cited path exists anywhere, no kind tag SHALL be added and the
task SHALL keep the existing undeclared, inferred-scope behavior.

#### Scenario: A fold citing only state beside the worktree declares e2e

- **WHEN** `apply --confirm` executes `fold-into-change` for a brief whose focus or verdict
  evidence cites the run journal beside the fold's worktree (`../run-<spec>.json`, an existing
  file) and the derived `files:` scope is empty
- **THEN** the appended checklist item is tagged `[e2e]`, carries no `files:` line, and the
  target change's `worktrail-compile` reports no scope gap for it without a model pass having
  supplied its scope

#### Scenario: A non-empty derived scope keeps the task an implementation task

- **WHEN** the derivation yields a non-empty `files:` scope
- **THEN** the appended task carries it and no kind tag, exactly as before this rule

#### Scenario: Citations that exist nowhere keep the inferred-scope behavior

- **WHEN** no path the focus or the evidence cites exists in or beside the worktree
- **THEN** no kind tag is added and the task is appended undeclared, for compile's own
  inference, exactly as before this rule
