# implement-pipeline-dispatch-guard Specification

## Purpose
TBD - created by archiving change implement-pipeline-active-conflicts-guard. Update Purpose after archive.
## Requirements
### Requirement: Active-conflicts scan before implement-pipeline orchestrator launch

The `implement` pipeline (Route D, `pipeline-details.md#implement-pipeline`)
SHALL run the active-conflicts scan (`#active-conflicts-scan`:
`worktrail-run-record active-conflicts --dir --repo --specification
$SPEC_ID --exclude $RUN`) as the first sub-step of its step 1, before
`#stale-spec-check`, before `#precheck-gate`, and before the orchestrator is
launched. If the scan reports one or more non-terminal runs already
targeting `$SPEC_ID`, the pipeline SHALL abort the dispatch — finishing the
current run record with status `blocked_external_dependency` and reporting
the conflicting run(s) — without launching the orchestrator, without
creating any task worktree, and without any other file-system mutation.

#### Scenario: No conflicting run
- **WHEN** the `implement` pipeline picks a `ready-to-implement` spec and the
  active-conflicts scan for that spec's id returns no non-terminal runs
- **THEN** the pipeline proceeds to `#stale-spec-check` → `#precheck-gate`
  and launches the orchestrator exactly as before this change

#### Scenario: A prior session's run already targets the same spec
- **WHEN** the `implement` pipeline picks a `ready-to-implement` spec and the
  active-conflicts scan finds a non-terminal run record for the same
  `$SPEC_ID` started by a different run
- **THEN** the pipeline does not launch the orchestrator, does not create
  any task worktree, finishes its own run record with status
  `blocked_external_dependency`, and reports the conflicting run's id,
  start time, and request summary

#### Scenario: Implement pipeline runs no advisory glob check
- **WHEN** the `implement` pipeline runs its active-conflicts scan
- **THEN** it does not additionally run the advisory `git worktree
  list`/`for-each-ref` glob check that `#sibling-worktree-check` runs for the
  `new` and `modify` pipelines, because the `implement` pipeline never
  creates a `spec/$SPEC_ID` or `chg/$SPEC_ID-*` authoring worktree or branch
  for that check to match against

### Requirement: Shared active-conflicts scan implementation

The active-conflicts hard-stop scan SHALL be defined in exactly one place,
`#active-conflicts-scan`, and reused by every pipeline that needs it
(`#sibling-worktree-check`, called from the `new` and `modify` pipelines,
and the `implement` pipeline directly). No pipeline SHALL inline a separate
copy of the `worktrail-run-record active-conflicts` invocation.

#### Scenario: New and modify pipelines are unaffected
- **WHEN** the `new` pipeline's `#spec-worktree-setup` or the `modify`
  pipeline's `#change-spec-worktree-setup` runs `#sibling-worktree-check`
- **THEN** the active-conflicts scan it runs is unchanged in behavior,
  inputs, and outputs from before this change — only its implementation
  moved into the shared `#active-conflicts-scan` anchor

