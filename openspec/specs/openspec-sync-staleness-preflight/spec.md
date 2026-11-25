# openspec-sync-staleness-preflight Specification

## Purpose
Ensures agent-driven OpenSpec synchronization uses the current remote canonical specs and cannot
overwrite already-converged requirements from a stale worktree snapshot.
## Requirements
### Requirement: Sync preflight uses the remote canonical baseline

Before the bundled sync operation evaluates or edits a canonical capability spec, it SHALL fetch
the target repository's `origin` remote and resolve its target base branch, defaulting to `main`
when the caller supplies none. It SHALL use the fetched `origin/<base>` canonical spec content as
the baseline for every delta merge and SHALL NOT use a stale worktree copy as that baseline. If
the fetch or remote-base resolution fails, it SHALL stop before editing any canonical spec and
report the failure.

#### Scenario: stale worktree has an older canonical requirement

- **WHEN** a sync worktree's `openspec/specs/<capability>/spec.md` lacks content present in
  `origin/<base>`
- **THEN** the sync reads the `origin/<base>` version as its merge baseline and preserves the
  remote content while applying the delta

#### Scenario: remote preflight cannot complete

- **WHEN** fetching `origin` or resolving the target remote base fails
- **THEN** the sync reports the preflight failure and makes no canonical-spec edits

### Requirement: Already-converged remote delta is an edit-free no-op

After the remote-base preflight and before editing, the bundled sync operation SHALL compare each
affected delta requirement block with the matching canonical requirement block at `origin/<base>`.
When every affected block is already equal after trailing-whitespace normalization, it SHALL report
the change as already synced and stop without editing, committing, or creating canonical spec
files. If any affected block is absent, differs, or cannot be matched, the operation SHALL use the
remote baseline to perform its normal intelligent merge rather than reporting an already-synced
no-op.

#### Scenario: remotely converged modified requirement is re-dispatched

- **WHEN** a change delta's modified requirement block equals the matching requirement at
  `origin/<base>` apart from trailing whitespace
- **THEN** the sync reports that the change is already synced and leaves the worktree unchanged

#### Scenario: remote canonical requirement contains a later variation

- **WHEN** an affected remote requirement differs from the delta block
- **THEN** the sync does not report an already-synced no-op and merges the delta against the
  remote requirement without discarding its existing content

### Requirement: Both bundled sync entry surfaces retain the staleness preflight

The Claude `/opsx:sync` command and the `openspec-sync-specs` skill SHALL both direct agents to
perform the remote-base preflight and already-synced no-op assessment before editing canonical
specs.

#### Scenario: plugin instruction surfaces are checked

- **WHEN** the plugin-surface test reads the bundled sync command and skill
- **THEN** it verifies that both instruct agents to fetch `origin`, use `origin/<base>` as the
  canonical baseline, and stop without edits for an already-converged remote delta

