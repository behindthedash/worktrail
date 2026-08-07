## 1. Extract the shared active-conflicts scan anchor

- [x] 1.1 In `skills/worktrail-go/references/subagent-prompts.md`, add a new
  `### Active-conflicts scan {#active-conflicts-scan}` section directly above
  `### Sibling worktree/branch check {#sibling-worktree-check}`, containing
  the existing `worktrail-run-record active-conflicts --dir --repo
  --specification --exclude` shell block (moved verbatim from inside
  `#sibling-worktree-check`, including its surrounding explanation of why the
  scan is a hard stop and the `BLOCKED:`/`finish --status
  blocked_external_dependency` handling).
- [x] 1.2 Rewrite `#sibling-worktree-check` in the same file to open with a
  call to `#active-conflicts-scan` ("Before the advisory glob check below,
  run `#active-conflicts-scan`.") instead of embedding the block, then
  continue unchanged with the advisory `$SIBLING_WT_GLOB`/`$SIBLING_REF_GLOB`
  check. Confirm the two existing call sites
  (`#spec-worktree-setup`, `#change-spec-worktree-setup`) still read
  correctly with no other text changes required at either site.

## 2. Wire the implement pipeline into the shared scan

- [x] 2.1 In `skills/worktrail-sdd-workflow/references/pipeline-details.md`,
  add a new first sub-step to the `implement` pipeline's step 1
  (`#implement-pipeline`): run `#active-conflicts-scan` with `$SPEC_ID` = the
  picked spec's id and `$REPO` as the repo, before `#stale-spec-check`. On a
  hit, stop per `#active-conflicts-scan`'s own hard-stop handling — do not
  proceed to `#stale-spec-check`/`#precheck-gate` and do not launch the
  orchestrator.
- [x] 2.2 In the same section, add one sentence noting that no advisory glob
  check runs here (`implement` never creates a `spec/$SPEC_ID` or
  `chg/$SPEC_ID-*` authoring worktree/branch for that check to match
  against), so a future reader doesn't file it as a second gap.

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/test_plugin_surface.py`
  to confirm `test_cross_skill_anchor_citations_resolve` (and the rest of the
  plugin-surface suite) still passes with the new `#active-conflicts-scan`
  anchor and its citations from `#sibling-worktree-check` and
  `#implement-pipeline`.
- [x] 3.2 [e2e] Run the full repo gate: `PYTHONPATH=src pytest -q &&
  PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`.
- [x] 3.3 [e2e] Read both edited files
  (`skills/worktrail-go/references/subagent-prompts.md` and
  `skills/worktrail-sdd-workflow/references/pipeline-details.md`) end to end
  once more, confirming `#new-pipeline` and `#modify-pipeline`'s text is
  byte-for-byte unchanged except for the one line in each that now says "run
  `#active-conflicts-scan`" instead of embedding the block.
