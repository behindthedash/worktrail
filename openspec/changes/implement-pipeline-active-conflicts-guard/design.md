## Context

`#sibling-worktree-check` (`skills/worktrail-go/references/subagent-prompts.md`)
bundles two checks under one anchor, run before either the `new` or `modify`
pipeline creates its spec/change-authoring worktree:

1. An **active-conflicts scan** — `worktrail-run-record active-conflicts
   --dir --repo --specification --exclude`, a hard stop. Non-terminal run
   records already targeting the same `$SPEC_ID` abort the dispatch before
   any worktree, branch, or file is touched.
2. An **advisory glob check** — local `git worktree list` / `for-each-ref`
   against `$SIBLING_WT_GLOB` / `$SIBLING_REF_GLOB` (`spec/$SPEC_ID` or
   `chg/$SPEC_ID-*`), which only warns.

The `implement` pipeline (`pipeline-details.md#implement-pipeline`, Route D)
operates with `SPEC_ROOT=$REPO` — the spec is already on `main`, and this
pipeline never creates a `spec/$SPEC_ID` or `chg/$SPEC_ID-*` worktree of its
own. It currently goes straight from picking a `ready-to-implement` spec to
`#stale-spec-check` → `#precheck-gate` → launching the orchestrator, with no
conflict check in between. That is precisely the gap the 2026-08-07 incident
exploited: two `/go` sessions both saw the same spec as `ready-to-implement`
and both launched `full-real --route D` against it.

## Goals / Non-Goals

**Goals:**
- Give the `implement` pipeline the same hard-stop protection the `new` and
  `modify` pipelines already have, without duplicating the scan's shell logic
  a third time.
- Leave existing `new`/`modify` call-site behavior byte-for-byte unchanged.

**Non-Goals:**
- Cross-machine detection. `#sibling-worktree-check` already documents this
  as a known gap (local refs only); the active-conflicts scan is run-record
  based, not git-ref based, so it is unaffected by that limitation but does
  not close it either — a run on a different machine still shows up in this
  scan only once its run record is visible from wherever `$RUN_RECORD_DIR`
  resolves.
- An explicit run-record-backed lock/claim primitive on `spec_id` (proposed
  as a further hardening idea in the source incident report). The
  active-conflicts scan already prevents a second orchestrator launch once a
  first run's record exists; a pre-claim lock would additionally prevent two
  sessions from *both reaching* that point believing they're first. Out of
  scope for this change — see proposal.md's Impact section.

## Decisions

**Extract the scan into its own anchor rather than inlining a third copy.**
`#active-conflicts-scan` becomes the sole owner of the
`worktrail-run-record active-conflicts` shell block currently living inside
`#sibling-worktree-check`. `#sibling-worktree-check` is rewritten to open
with "run `#active-conflicts-scan`" instead of embedding the block, then
continues with its advisory glob check as before. This keeps the shell code
in exactly one place; a future change to the CLI invocation only touches one
anchor. Alternative considered: inline the same block a third time directly
into `pipeline-details.md`. Rejected — the two existing copies (`new` and
`modify` share `#sibling-worktree-check`) already come from one shared
section for this reason, so adding a third un-shared copy would be a
regression, not just extra text.

**Call it from `pipeline-details.md#implement-pipeline` step 1, not from a
new step 0.** `new` and `modify` run the check at their step 0, before any
worktree exists. `implement` has no equivalent pre-worktree moment — its
step 1 already runs `#stale-spec-check` → `#precheck-gate` before the
orchestrator launches, and nothing before step 1 does any repo-mutating
work. Placing the scan as the first sub-step of step 1 preserves "before
anything is created or launched" without inventing a new numbered step for
one line of behavior.

**No glob check for `implement`.** The advisory half of
`#sibling-worktree-check` checks for `spec/$SPEC_ID` / `chg/$SPEC_ID-*`
authoring branches — branches only the `new` and `modify` pipelines create.
`implement` never creates one, so running that check here would always be a
no-op that adds a confusing, permanently-empty warning path. Only
`#active-conflicts-scan` is called.

## Risks / Trade-offs

- [Risk] A stale/orphaned run record (a crashed session that never called
  `finish`) could block a legitimate second implement attempt on the same
  spec. → Mitigation: this is the exact same failure mode `new`/`modify`
  already accept at their own call sites; no new exposure. Recovery is
  documented at the existing `#active-conflicts-scan` call sites (inspect
  and manually finish/abandon the stale run record).
- [Risk] Doc-only change (no `src/worktrail/**` edit) means CI's
  `Version Bump Check` does not gate this PR, and the fix's correctness
  depends entirely on the shell block being copy-correct. → Mitigation:
  the extracted `#active-conflicts-scan` block is verbatim-identical to the
  code already exercised at the `new`/`modify` call sites — same `--dir`,
  `--repo`, `--specification`, `--exclude`, same exit/finish handling — so
  no new shell logic is introduced, only a new citation of existing logic.

## Migration Plan

Pure documentation change to two skill reference files, no code, no data
migration, no rollback complexity beyond reverting the PR. Existing
in-flight `/go` sessions read the skill text fresh each invocation, so the
next `implement` dispatch after merge picks up the new sub-step
automatically.
