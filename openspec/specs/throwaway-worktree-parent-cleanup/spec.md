# throwaway-worktree-parent-cleanup Specification

## Purpose
TBD - created by archiving change implement-pipeline-spec-root-neutral-cwd. Update Purpose after archive.
## Requirements
### Requirement: Throwaway checkout parent directories are removed when empty
When `_integration_worktree`, `detect_checkbox_status_divergence` or `sync_checkbox_status`
tears down its throwaway checkout (`<repo>-integrate/<leaf>`, `<repo>-checkbox-check/<leaf>`,
`<repo>-checkbox-sync/<leaf>`), the system SHALL also remove the parent directory it created
if and only if the parent is then empty. The removal SHALL happen under the same registry lock
as the leaf's `git worktree remove`. A non-empty parent SHALL be left untouched, and a failed
removal SHALL NOT raise. `<repo>-worktrees/` SHALL NOT be removed by any of these paths.

#### Scenario: Integration worktree torn down
- **WHEN** `_integration_worktree` exits for the only group using `<repo>-integrate/`
- **THEN** neither the leaf checkout nor `<repo>-integrate/` exists afterwards

#### Scenario: Sibling checkout still present
- **WHEN** `_integration_worktree` exits while another leaf directory still exists under
  `<repo>-integrate/`
- **THEN** the other leaf and `<repo>-integrate/` are untouched

#### Scenario: Checkbox divergence check torn down
- **WHEN** `detect_checkbox_status_divergence` returns (findings or none)
- **THEN** `<repo>-checkbox-check/` no longer exists

#### Scenario: Run state directory is preserved
- **WHEN** any of the three helpers tears down its checkout
- **THEN** `<repo>-worktrees/` and its contents are unchanged

