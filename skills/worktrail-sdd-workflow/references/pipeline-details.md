# Pipeline Execution Details

Referenced by sdd-workflow Phase 7 routes C, D, F, and G. Anchors: `#new-pipeline`,
`#implement-pipeline`, `#modify-pipeline`.

Templates, stage-result handling, worktree commands, orchestrator gates: `subagent-prompts.md`
(anchors `#name`). **Never author on the base checkout.**

---

## `new` pipeline {#new-pipeline}

Route C (feature-planning), and Route D when no spec exists.

**Pre-step: Overlap Check** — `../../worktrail-go/references/subagent-prompts.md#overlap-check`.

0. **Id + spec worktree** — `../../worktrail-go/references/subagent-prompts.md#spec-worktree-setup`. Includes a mandatory
   sibling-worktree/branch check against `$SPEC_ID` before creating `$WT` (advisory, not a
   hard stop — `../../worktrail-go/references/subagent-prompts.md#sibling-worktree-check`). Outputs `$SPEC_ID`, `$WT`,
   `$SPEC_DIR`. Sandbox-denied → surface and stop. If later stages need repo-local
   tooling in `$WT`, bootstrap dependencies in that worktree per the repo's documented
   install step before running them. Commit on `spec/$SPEC_ID` after each writing step.
1. **constitution** — only if `$WT/docs/specs/architecture.md` is missing.
2. **brainstorm** — Agent dispatch per `../../worktrail-go/references/subagent-prompts.md#brainstorm-template` (opus for sparse
   seeds, sonnet for constrained).
3. **spec-check** — `Agent(subagent_type: "general-purpose")` per
   `../../worktrail-go/references/subagent-prompts.md#spec-check-template`. Skill names are NOT valid agent types.
4. **technical-plan (optional)** — ask once inline.

**GUARD: All spec-file edits and task frontmatter fixups must be made inside `$WT/docs/specs/$SPEC_ID/`, never in `$REPO`. After spec-to-tasks and throughout implementation, commit fixups on the `spec/$SPEC_ID` branch. Never stash or transfer edits from the base checkout.**

5. **spec-to-tasks** — `Agent(subagent_type: "general-purpose")` per
   `../../worktrail-go/references/subagent-prompts.md#spec-to-tasks-template`.

**Stage results:** `../../worktrail-go/references/subagent-prompts.md#stage-result-handling`. **Inline-only:** never delegate
repo resolution, dashboard, worktree creation, routing, AskUserQuestion gates,
orchestrator, sync, or monitoring. Each stage's output is committed — hand later stages pointers.

Route C closeout (routes.md §C): spec PR plus the implementation-intent
transition. Requested intent continues into Route D in the same run;
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
   command and flags (`--agent`, `--pipeline`, `--run-budget`, `--smoke-cmd`) —
   do not re-derive the invocation here.

7. **sync** (mandatory, BEFORE any teardown) — `../../worktrail-go/references/subagent-prompts.md#sync-before-teardown`.

Re-run the dashboard; teardown per `../../worktrail-go/references/subagent-prompts.md#worktree-lifecycle` (spec worktree only
after sync completes).

---

## `implement` pipeline {#implement-pipeline}

Route D, spec already on base branch.

Pick a `ready-to-implement` spec (ask if several; else route to its actual next action).

1. Run `../../worktrail-go/references/subagent-prompts.md#stale-spec-check` → `../../worktrail-go/references/subagent-prompts.md#precheck-gate`
   (`SPEC_ROOT=$REPO`). If precheck reports a prior `fanout_failed` sidecar,
   stop and recover the stuck run instead of re-launching the orchestrator.
2. Launch the orchestrator per `../../worktrail-go/references/subagent-prompts.md#orchestrator` with
   `SPEC_ROOT=$REPO`, `run_in_background: true`. PRs into `$BASE`.

3. Sync per `../../worktrail-go/references/subagent-prompts.md#sync-before-teardown`; re-run dashboard. A 1-task spec runs a single worker.

---

## `modify` pipeline {#modify-pipeline}

Route F (defect repair, when a spec owns the behavior) and Route G (specification
change) — a **delta** against a spec already on `$BASE`, authored via
`specs.change-spec`. This is the pipeline `routes.md` §F/§G
refer to; it does not reuse `new`/`implement` because the artifact being fanned
out (a change's `tasks/TASK-CHG-*.md`) lives under a `changes/<slug>/` subtree
that must be committed on its own worktree branch before the orchestrator forks
task worktrees from it — skipping this is exactly how a task worktree ends up
missing its own task file (found and root-caused directly against repo history:
task worktree branched from the change-spec-authoring commit, one commit before
spec-to-tasks committed `tasks/*.md`, because nothing enforced committing that
output before launch).

0. **Change-spec worktree** — `../../worktrail-go/references/subagent-prompts.md#change-spec-worktree-setup`.
   Includes a mandatory sibling-worktree/branch check against `$SPEC_ID` before
   creating `$WT` (advisory, not a hard stop — see that section). Outputs
   `$CHANGE_SLUG`, `$WT`, `$CHANGE_DIR`. Sandbox-denied → surface and stop.

**GUARD: all change-spec and task-frontmatter edits happen inside
`$WT/docs/specs/$SPEC_ID/changes/$CHANGE_SLUG/`, never in `$REPO`. Commit on
`chg/$SPEC_ID-$CHANGE_SLUG` after every writing step below — never launch the
orchestrator with uncommitted output sitting in `$WT`.**

1. **specs.change-spec** — `--type=bugfix` (Route F) or `--type=delta` (Route G);
   exact id per the conventions block. Author inside `$CHANGE_DIR`, then commit.
   If step 0 surfaced sibling change-spec work on this `$SPEC_ID`, read its
   Summary/Decisions before authoring and record the reconciliation (adopted,
   differs, or superseded) in this change-spec's own Decisions section — do not
   re-derive decisions the sibling already made.
2. **spec-to-tasks (delta)** — `Agent(subagent_type: "general-purpose")` per
   `../../worktrail-go/references/subagent-prompts.md#spec-to-tasks-template`, `spec_dir=$CHANGE_DIR`. Author
   `tasks/TASK-CHG-*.md`, `data-model.md`, `contracts/`, `knowledge-graph.json`
   inside `$CHANGE_DIR`, then commit immediately — do not defer this commit or
   batch it with a later step.

**Stage results:** `../../worktrail-go/references/subagent-prompts.md#stage-result-handling`.

3. **Pre-launch uncommitted-output guard (mandatory)** — mirrors the `new`
   pipeline's base-checkout diff detection (`#new-pipeline` step 5b), but checks
   the change-spec worktree itself rather than `$REPO`:

```bash
CHG_DIFF=$(git -C "$WT" status --porcelain -- docs/specs/ 2>&1)
if [ -n "$CHG_DIFF" ]; then
  echo "ERROR: $WT has uncommitted docs/specs/ output (tasks/data-model/contracts/knowledge-graph). Commit it on chg/$SPEC_ID-$CHANGE_SLUG before launching the orchestrator — an uncommitted file here will be silently absent from every task worktree the orchestrator forks from \$WT." >&2
  exit 1
fi
```

4. Run `../../worktrail-go/references/subagent-prompts.md#stale-spec-check` → `../../worktrail-go/references/subagent-prompts.md#precheck-gate`
   (`SPEC_ROOT=$WT`, pointed at `$CHANGE_DIR`).
5. **orchestrator** — invoke per `../../worktrail-go/references/subagent-prompts.md#orchestrator` with
   `SPEC_ROOT=$WT`, `run_in_background: true`. Cutting task worktrees from `$WT`
   (not `$REPO`) is what guarantees the just-committed change-spec and task files
   are present in every forked task worktree.
6. **sync** (mandatory, BEFORE any teardown) — `../../worktrail-go/references/subagent-prompts.md#sync-before-teardown`.

Re-run the dashboard; teardown per `../../worktrail-go/references/subagent-prompts.md#worktree-lifecycle` (change-spec
worktree only after sync completes).

Completion: `completed_*` (Route F/G's own completion states — see `routes.md`).
