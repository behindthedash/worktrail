# Pipeline Execution Details

Referenced by sdd-workflow Phase 7 routes C, D, F, and G. Anchors: `#new-pipeline`,
`#implement-pipeline`, `#modify-pipeline`.

Templates, stage-result handling, worktree commands, orchestrator gates: `subagent-prompts.md`
(anchors `#name`). **Never author on the base checkout.**

---

## `new` pipeline {#new-pipeline}

Route C (feature-planning), and Route D when no spec exists.

**Pre-step: Overlap Check** — `../../worktrail-go/references/subagent-prompts.md#overlap-check`.

0. **Format + id + spec worktree** — `../../worktrail-go/references/subagent-prompts.md#spec-worktree-setup`. `/go new` defaults to OpenSpec and accepts
   `WORKTRAIL_SPEC_FORMAT=devkit` for legacy authoring. Includes a mandatory
   sibling-worktree/branch check against `$SPEC_ID` before creating `$WT` (advisory, not a
   hard stop — `../../worktrail-go/references/subagent-prompts.md#sibling-worktree-check`). Outputs `$SPEC_ID`, `$WT`,
   `$SPEC_DIR`. Sandbox-denied → surface and stop. If later stages need repo-local
   tooling in `$WT`, bootstrap dependencies in that worktree per the repo's documented
   install step before running them. Commit on `spec/$SPEC_ID` after each writing step.
   For `devkit`, create the initial artifact shape before authoring:
   `worktrail-spec-create --repo "$WT" --format devkit --id "$SPEC_ID" --request "<request>"`.
1. **create/author** — create the selected format's scaffold with
   `worktrail-spec-create` when using the devkit format, or `/opsx:propose "<request>"`
   for OpenSpec, per `../../worktrail-go/references/subagent-prompts.md#openspec-propose`. Generates
   proposal, delta specs, design, and tasks in one step. Run inline: it is a slash
   command, not an `Agent` dispatch. Ambiguous seed → `../../worktrail-go/references/subagent-prompts.md#openspec-explore` first.
2. **validate** — `openspec validate <change-id>` per `../../worktrail-go/references/subagent-prompts.md#openspec-validate`. Catches
   the silent failures OpenSpec's own schema warns about (scenarios needing exactly
   four hashtags; a requirement with no scenario).
3. **scope-check** — for OpenSpec changes, run `worktrail-compile` against the change
   directory *before* the docs-only spec PR push (Route C closeout, below). This is
   the same scope-gap check `../../worktrail-go/references/subagent-prompts.md#orchestrator`
   already runs before `full-real` — running it here instead of only there is what closes the gap: an
   under-scoped `tasks.md` (OpenSpecTaskSource always emits `files: []` per task, so
   any change whose steps are investigation/verification-only is easy to under-scope)
   used to merge as a docs-only PR and surface only once the orchestrator launched,
   forcing a second PR against a branch whose predecessor had already squash-merged
   (reproduced directly in datalena PR #2128 → #2129 conflict → #2130 recovery,
   2026-08-05/06).

   ```bash
   worktrail-compile "$WT/openspec/changes/$SPEC_ID" || {
     echo "ERROR: worktrail-compile found scope gaps in $SPEC_ID -- fix tasks.md in $WT (add explicit files: scope, a tail kind for investigation/verification-only steps, or a deps edge for an unordered file collision) and re-run before pushing the spec PR." >&2
     exit 1
   }
   ```

   Fix `tasks.md` in place inside `$WT` and re-run until it passes — never push
   the docs-only PR on a failing compile. Devkit-format changes already declare
   file scope in task frontmatter and take compile's free seed path (no model
   call), so this is effectively a no-op there.

   **Structural backstop (`CI: Scope Check`, `.github/workflows/scope-check.yml`
   in the worktrail repo itself):** a passing compile writes a content-fingerprint
   marker (`.compile-ok`) into the change directory — commit it along
   with the rest of the change (the GUARD paragraph below already requires the
   whole change directory to be committed). CI cannot re-run the real,
   LLM-backed scope check (no agent-CLI credentials are wired into this repo's
   CI, and the cost/flakiness of a live model call on every PR touching
   `tasks.md` was judged not worth it), so it instead re-derives the same
   deterministic fingerprint (`runplan.fingerprint`, no model call) and fails
   the PR if the marker is missing or stale against the current content. This
   makes "step 3 was skipped" or "`tasks.md` was hand-edited after compile
   passed" a structural CI failure instead of relying on this doc being
   followed — closing the datalena PR #2128→#2130 incident class with a
   deterministic rail on the class of failure a fingerprint can actually catch.
   It does **not** replace step 3 itself: CI cannot discover a fresh scope gap
   in content nobody has compiled yet, only detect that this step was skipped
   or invalidated.

**GUARD: all change artifacts are authored inside `$WT/openspec/changes/$CHANGE_ID/`,
never in `$REPO`, and the whole change directory is committed on `spec/$CHANGE_ID`
before the orchestrator forks any task worktree. Never stash or transfer edits from
the base checkout.** The rule is unchanged from the devkit pipeline — only the path
moved. It is what stops a task worktree branching one commit before `tasks.md` landed.

The former stages 1–5 (constitution, brainstorm, spec-check, technical-plan,
spec-to-tasks) collapse into step 1: OpenSpec generates all four artifacts in one
command, so there is no stage-to-stage handoff left to sequence or commit between.

**Stage results:** `../../worktrail-go/references/subagent-prompts.md#stage-result-handling`. **Inline-only:** never delegate
repo resolution, dashboard, worktree creation, routing, AskUserQuestion gates,
orchestrator, sync, or monitoring. Each stage's output is committed — hand later stages pointers.

Route C closeout (routes.md §C, after step 3's scope-check passes): spec PR
plus the implementation-intent transition. Requested intent continues into Route D in the same run;
planning-only intent finishes with `planned_ready_for_implementation`; unknown
intent asks once and records the decision. A requested Route-C brief must not
be closed or handed off at the spec/task boundary.

Route D (and Route C continuing inline) continues:

5a. Run `../../worktrail-go/references/subagent-prompts.md#stale-spec-check` → `../../worktrail-go/references/subagent-prompts.md#precheck-gate` (`SPEC_ROOT=$WT`).

5b. **Base-checkout diff detection** — Check if spec files have been edited in the base checkout; if so, surface an error and stop:

```bash
SPEC_DIFF=$(git -C "$REPO" diff -- docs/specs/ 2>&1)
if [ -n "$SPEC_DIFF" ]; then
  echo "ERROR: Spec files have been edited in the base checkout ($REPO/docs/specs/). All spec-file edits must be made inside the spec worktree ($WT/docs/specs/$SPEC_ID/). Re-apply them in $WT/docs/specs/$SPEC_ID/ and commit there." >&2
  exit 1
fi
```

6. **orchestrator** — invoke per `../../worktrail-go/references/subagent-prompts.md#orchestrator` with
   `SPEC_ROOT=$WT`, `run_in_background: true`. That anchor owns the exact
   command and flags (`--agent`, `--run-budget`, `--smoke-cmd`; the pipelined
   engine is the default) —
   do not re-derive the invocation here.

7. **sync** (mandatory, BEFORE any teardown) — `../../worktrail-go/references/subagent-prompts.md#sync-before-teardown`.

Re-run the dashboard; teardown per `../../worktrail-go/references/subagent-prompts.md#worktree-lifecycle` (spec worktree only
after sync completes).

---

## `implement` pipeline {#implement-pipeline}

Route D, spec already on base branch.

Pick a `ready-to-implement` spec (ask if several; else route to its actual next action).

1a. Run `../../worktrail-go/references/subagent-prompts.md#active-conflicts-scan`
    (`SPEC_ID`=the picked spec's id, `REPO=$REPO`). On a hit, stop per that
    scan's own hard-stop handling — do not proceed to step 1b
    (`#stale-spec-check`/`#precheck-gate`) and do not launch the orchestrator.
    No advisory glob check (`$SIBLING_WT_GLOB`/`$SIBLING_REF_GLOB`) runs here:
    `implement` never creates a `spec/$SPEC_ID` or `chg/$SPEC_ID-*` authoring
    worktree/branch for that check to match against.
1b. Run `../../worktrail-go/references/subagent-prompts.md#stale-spec-check` → `../../worktrail-go/references/subagent-prompts.md#precheck-gate`
    (`SPEC_ROOT=$REPO`). If precheck reports a prior `fanout_failed` sidecar,
    stop and recover the stuck run instead of re-launching the orchestrator.
2. Launch the orchestrator per `../../worktrail-go/references/subagent-prompts.md#orchestrator` with
   `SPEC_ROOT=$REPO`, `run_in_background: true`. PRs into `$BASE`.

3. Sync per `../../worktrail-go/references/subagent-prompts.md#sync-before-teardown`; re-run dashboard. A 1-task spec runs a single worker.

---

## `modify` pipeline {#modify-pipeline}

Route F (defect repair, when a spec owns the behavior) and Route G (specification
change) — a **delta** against a spec already on `$BASE`, authored via
`/opsx:propose`. This is the pipeline `routes.md` §F/§G
refer to; it does not reuse `new`/`implement` because the artifact being fanned
out (a change's `tasks.md`) lives under `openspec/changes/<id>/` and must be
committed on its own worktree branch before the orchestrator forks task worktrees
from it — skipping this is exactly how a task worktree ends up
missing its own task file (found and root-caused directly against repo history:
task worktree branched from the change-spec-authoring commit, one commit before
the tasks artifact landed, because nothing enforced committing that
output before launch).

0. **Change-spec worktree** — `../../worktrail-go/references/subagent-prompts.md#change-spec-worktree-setup`.
   Includes a mandatory sibling-worktree/branch check against `$SPEC_ID` before
   creating `$WT` (advisory, not a hard stop — see that section). Outputs
   `$CHANGE_SLUG`, `$CHANGE_ID`, `$WT`, `$CHANGE_DIR`. Sandbox-denied → surface and stop.

**GUARD: all change artifacts are authored inside `$WT/openspec/changes/$CHANGE_ID/`,
never in `$REPO`. Commit the whole change directory on `chg/$CHANGE_ID` before
launching the orchestrator — never launch with uncommitted output sitting in `$WT`.**

1. **propose** — `/opsx:propose "<request>"` per `../../worktrail-go/references/subagent-prompts.md#openspec-propose`, then commit
   the change directory. Generates proposal, delta specs, design, and tasks in one
   step, so the former two-step author-then-tasks sequence (and the commit between
   them) is gone. If step 0 surfaced sibling change work on this capability, read its
   proposal before authoring and record the reconciliation (adopted, differs, or
   superseded) in this change's own proposal — do not re-derive decisions the sibling
   already made.
2. **validate** — `openspec validate <change-id>` per `../../worktrail-go/references/subagent-prompts.md#openspec-validate`.

**Stage results:** `../../worktrail-go/references/subagent-prompts.md#stage-result-handling`.

3. **scope-check** — run `worktrail-compile` against the change directory *before*
   the pre-launch uncommitted-output guard below, mirroring `#new-pipeline` step 3.
   `#orchestrator` (invoked in step 6 below) runs the same command again immediately
   before `full-real` — a no-op cache hit there, per `compile.py`'s content-fingerprint
   cache — but running it here first, while the change directory is still open for a
   commit, is what gets the resulting `.compile-ok` marker committed at all. Run only
   here, the marker is always written *after* the change directory's last commit (the
   guard below already passed by the time `#orchestrator` reaches its own compile
   call), so every group PR is missing it deterministically — reproduced directly on
   worktrail PR #260 (run go-20260809-021940): `CI: Scope check`
   (`worktrail-check-compile-markers`) failed and the marker had to be hand-committed
   onto the group branch as a `ci_repair` intervention.

   ```bash
   worktrail-compile "$CHANGE_DIR" || {
     echo "ERROR: worktrail-compile found scope gaps in $CHANGE_ID -- fix tasks.md in $WT (add explicit files: scope, a tail kind for investigation/verification-only steps, or a deps edge for an unordered file collision) and re-run before continuing." >&2
     exit 1
   }
   ```

   A passing compile writes `.compile-ok` into `$CHANGE_DIR`, uncommitted — the
   pre-launch uncommitted-output guard immediately below catches it as part of its
   normal `openspec/` diff check and requires it to be committed with the rest of the
   change, closing the loop without any new guard logic.

4. **Pre-launch uncommitted-output guard (mandatory)** — mirrors the `new`
   pipeline's base-checkout diff detection (`#new-pipeline` step 5b), but checks
   the change-spec worktree itself rather than `$REPO`. (Verified: unlike `new`,
   this pipeline has no intermediate docs-only-PR-push-and-merge stage before
   the orchestrator runs — everything stays on `chg/$CHANGE_ID` through sync —
   so it is not exposed to the "merged predecessor squashed away before the
   scope-check ran" incident class step 3 of the `new` pipeline closes above.)

```bash
CHG_DIFF=$(git -C "$WT" status --porcelain -- openspec/ 2>&1)
if [ -n "$CHG_DIFF" ]; then
  echo "ERROR: $WT has uncommitted openspec/ output (proposal/specs/design/tasks, or a freshly-written .compile-ok from step 3). Commit it on chg/$CHANGE_ID before launching the orchestrator — an uncommitted file here will be silently absent from every task worktree the orchestrator forks from \$WT." >&2
  exit 1
fi
```

5. Run `../../worktrail-go/references/subagent-prompts.md#stale-spec-check` → `../../worktrail-go/references/subagent-prompts.md#precheck-gate`
   (`SPEC_ROOT=$WT`, pointed at `$CHANGE_DIR`).
6. **orchestrator** — invoke per `../../worktrail-go/references/subagent-prompts.md#orchestrator` with
   `SPEC_ROOT=$WT`, `run_in_background: true`. Cutting task worktrees from `$WT`
   (not `$REPO`) is what guarantees the just-committed change-spec and task files
   are present in every forked task worktree.
7. **sync** (mandatory, BEFORE any teardown) — `../../worktrail-go/references/subagent-prompts.md#sync-before-teardown`.

Re-run the dashboard; teardown per `../../worktrail-go/references/subagent-prompts.md#worktree-lifecycle` (change-spec
worktree only after sync completes).

Completion: `completed_*` (Route F/G's own completion states — see `routes.md`).
