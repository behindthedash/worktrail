## Why

The `new` pipeline (`#spec-worktree-setup`) and the `modify` pipeline
(`#change-spec-worktree-setup`) both run the active-conflicts hard-stop scan
(the non-advisory half of `#sibling-worktree-check`) before doing anything
else — it aborts the dispatch if a non-terminal run record already targets
the same `$SPEC_ID`. The `implement` pipeline (Route D) never creates a
spec-authoring worktree of its own, so it never picked up that same guard at
its own step 1, before launching the orchestrator.

Reproduced live 2026-08-07: after a Route C spec PR merged to `main`, a
second concurrent `/go` session picked up the now-visible ready-to-implement
spec and launched its own `worktrail-live full-real --route D` against the
same spec a first session was already actively driving. Both orchestrators
spawned duplicate IMPLEMENT workers for the same task concurrently. No
corruption resulted — git's own atomicity plus the second session standing
down after verifying the first session's work was clean — but that was luck,
not a guarantee: two independent `full-real` processes writing into the same
task worktrees have no coordination once past the initial gate.

## What Changes

- Extract the active-conflicts hard-stop scan currently embedded in
  `#sibling-worktree-check` into its own citable anchor,
  `#active-conflicts-scan`, in `subagent-prompts.md`. `#sibling-worktree-check`
  calls it as its first step, unchanged for its existing `new`/`modify`
  callers — behavior at those two call sites does not change.
- Add a call to `#active-conflicts-scan` as the new first sub-step of the
  `implement` pipeline's step 1 (`pipeline-details.md#implement-pipeline`),
  before the existing `#stale-spec-check` → `#precheck-gate` chain and before
  the orchestrator is launched. The picked spec's id is `$SPEC_ID`, `$REPO` is
  the repo. No spec/change-authoring worktree exists at this point, so only
  the run-record scan runs — the advisory git-worktree/branch glob check in
  `#sibling-worktree-check` is specific to `spec/$SPEC_ID` / `chg/$SPEC_ID-*`
  authoring branches the `implement` pipeline never creates, and does not
  apply here.
- On a hit, the implement pipeline reports the conflicting run(s) and stops
  before creating any state, matching the existing hard-stop behavior at the
  other two call sites (`worktrail-run-record finish ... --status
  blocked_external_dependency`).

## Capabilities

### New Capabilities

- `implement-pipeline-dispatch-guard`: the `implement` pipeline's mandatory
  active-conflicts scan before it launches the orchestrator against a picked
  spec. No existing spec file documents this dispatch-guard behavior for any
  pipeline yet, so this is authored as a new capability rather than a delta
  against one.

### Modified Capabilities

(none — no existing `openspec/specs/` capability documents this behavior)

## Impact

- `skills/worktrail-go/references/subagent-prompts.md` — new
  `#active-conflicts-scan` anchor; `#sibling-worktree-check` refactored to
  call it.
- `skills/worktrail-sdd-workflow/references/pipeline-details.md` — `implement`
  pipeline step 1 gains the new sub-step.
- No `src/worktrail/**` code changes — `worktrail-run-record active-conflicts`
  already exists and is reused as-is. No version bump needed.
- Out of scope: an explicit run-record-backed lock/claim primitive on
  `spec_id` itself (distinct from this scan-before-launch guard) was raised
  by the same incident report as further hardening, but is not required to
  close this specific gap. Left for a separate change if the scan proves
  insufficient in practice.
