## ADDED Requirements

### Requirement: Unresolved same-spec dependency detection
The system SHALL detect, for every task loaded from a spec, whether each entry
in that task's `deps` (same-spec dependency) list matches the `id` of another
task loaded from the same spec, and SHALL report a diagnostic naming the
dependent task id and the unresolved dependency id when it does not.

#### Scenario: Same-spec dependency resolves
- **WHEN** a task's `deps` entry matches another task's `id` in the same loaded spec
- **THEN** no diagnostic is reported for that dependency

#### Scenario: Same-spec dependency is dangling
- **WHEN** a task's `deps` entry does not match any task `id` loaded from the same spec
- **THEN** the system reports a diagnostic naming the dependent task's id and the unresolved dependency id

#### Scenario: External dependency is not treated as unresolved same-spec dependency
- **WHEN** a task declares a cross-spec reference through `external-dependencies:` (not `deps:`)
- **THEN** the same-spec dependency check does not report it, regardless of whether it resolves — cross-spec resolution stays owned by `resolve_external_dependency`

### Requirement: Pre-launch surfacing through precheck
The system SHALL surface every unresolved same-spec dependency and
decision-log diagnostic through `worktrail-live precheck` before the
orchestrator is launched, using the same per-finding WARN format and non-zero
exit contract `precheck` already uses for external-dependency findings.

#### Scenario: Precheck reports an unresolved same-spec dependency
- **WHEN** `worktrail-live precheck` runs against a spec containing a task with a dangling `deps` entry
- **THEN** precheck prints a WARN line naming the task and the unresolved dependency, and exits non-zero

#### Scenario: Precheck stays clean when all dependencies resolve
- **WHEN** every task's `deps` and `decision-refs` entries resolve
- **THEN** precheck emits no diagnostic for this check and its exit code is unaffected by it

### Requirement: Decision-log dependency coverage (devkit format)
For a devkit-format spec, the system SHALL validate a task's `decision-refs:`
frontmatter (when present) against that spec's `decision-log.md`: each
referenced decision id SHALL exist as a `## D<n>: <title>` entry in the log,
and SHALL be in a resolved status (`Status: decided` or `Status: resolved`,
case-insensitive) — reporting a distinct diagnostic for a missing decision id
versus one that exists but is still open.

#### Scenario: Decision reference resolves and is decided
- **WHEN** a devkit task's `decision-refs:` names a decision id present in `decision-log.md` with `Status: decided`
- **THEN** no diagnostic is reported for that reference

#### Scenario: Decision reference is missing from the log
- **WHEN** a devkit task's `decision-refs:` names a decision id absent from `decision-log.md`
- **THEN** the system reports a diagnostic naming the task and the missing decision id

#### Scenario: Decision reference is still open
- **WHEN** a devkit task's `decision-refs:` names a decision id present in `decision-log.md` whose `Status:` is not `decided` or `resolved`
- **THEN** the system reports a diagnostic naming the task, the decision id, and its open status

### Requirement: Decision-log check is devkit-only
The system SHALL report `decision-refs:` on an OpenSpec-format task as an
unsupported field rather than silently ignoring it, since OpenSpec changes
have no `decision-log.md` convention to validate against.

#### Scenario: decision-refs on an OpenSpec task
- **WHEN** an OpenSpec-format task carries a `decision-refs:` entry
- **THEN** the system reports a diagnostic stating decision-refs is unsupported for OpenSpec-format tasks
