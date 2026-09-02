## Context

See proposal.md — Why. This section records only what the code looks like today, verified
by reading it in this worktree on 2026-09-02.

### Inventory: every current PR-opening call site

Python call sites (the AST walk in
`tests/router/test_pr_creation_callsite_enforcement_coverage.py` finds exactly these four
files; `KNOWN_CALLSITES` matches):

| Site | Compile marker | Labels | Pre-PR gate | CI watch | Review threads | Run record |
|---|---|---|---|---|---|---|
| `src/worktrail/workqueue/queue_triage.py:1454` `_worktree_pr_close()` — shared by `_apply_fold_into_change` (:1545) and `_apply_propose_change` (:1618) | none (PR #902 failed `Scope check` on commit `63e20a72`; hand fix `454574d2`) | `integrate._refresh_pr_labels` → `pre_pr_gate --labels-only` | none | none | none | none |
| `src/worktrail/drain/drain.py:1004` `_open_sync_pending_pr` | none | `_refresh_pr_labels` | none | none ("does not wait for merge") | none | none |
| `src/worktrail/drain/drain.py:1426` `_open_stale_bookkeeping_pr` | none | `_refresh_pr_labels` | none | none | none | none |
| `src/worktrail/drain/drain.py:1642` `_open_openspec_archive_pr` | none | `_refresh_pr_labels` | none | none | none | none |
| `src/worktrail/orchestrator/integrate.py:1517` group-PR creation (after operator-PR discovery) | n/a (marker committed by pipeline-details step 3 before fan-out) | `_refresh_pr_labels` per group | `--smoke-cmd` | `verify.py` `wait_and_fix_ci` | `verify.py` `resolve_review_threads` | journal, not run record |
| `src/worktrail/orchestrator/live.py:3202` `full()` sandbox cassette | exempt: `--repo sandbox`, dev tooling only | — | — | — | — | — |

Agent-executed (prose) call sites — issued through the Bash tool per skill text:

- `skills/worktrail-sdd-workflow/SKILL.md` Phase 8 (lines 161–296): scope-review gate →
  `worktrail-preflight run` → `gh pr create --label …` → `ci-watch-loop.md` →
  `worktrail-run-record finish --pr`.
- `skills/worktrail-go/references/routes.md` §C (lines 89–140): docs-only spec PR, then
  `ci-watch-loop.md`'s intermediate-checkpoint variant.
- `skills/worktrail-go/SKILL.md` close-stale row (line 294): "Land the same PR through the
  normal Phase 8 flow"; Phase 3 CI-watch paragraph (lines 885–899); Phase 2 intake gate
  step 3 (lines 268–282): "Report the resulting action-log entry … and STOP".
- `skills/worktrail-go/references/subagent-prompts.md` sync PR (line 996): `gh pr create`
  with `worktrail-pre-pr-gate --labels-only` labels, then Step 4b `gh pr checks --watch` and
  `gh pr merge`.
- `skills/worktrail-repo-init/SKILL.md:109` mentions `gh pr create` for a human-run
  onboarding PR; informational, not migrated.

Named in the request but **not** PR-opening (premise corrected):

- `src/worktrail/router/consolidate_cluster.py` — `execute_consolidation()` claims member
  briefs through `work_queue.py` and writes one consolidated brief into `queue_dir`. Zero
  `gh` or `git` invocations anywhere in the module. Nothing to migrate.
- `src/worktrail/router/close_stale_openspec.py` — module docstring: "deliberately does NOT
  commit, push, open a PR, or run `gh` at all" because a bespoke `gh pr create` would
  bypass the label hook. That reason disappears once a shared enforced pipeline exists, so
  this module *becomes* a caller (Decision 5).
- `src/worktrail/onboarding/repo_init.py:465` — `gh label create`, not a PR.

### Existing building blocks the pipeline composes (no new mechanisms)

- Compile: `conductor/compile.py` `main()` writes `.compile-ok` (`runplan.COMPILE_MARKER_NAME`)
  only when scope/ordering/coverage gaps are all empty; `router/check_compile_markers.py`
  `changed_change_dirs(repo, base_ref)` + `check_marker(change_dir)` is exactly what CI's
  `Scope check` runs.
- Labels + gate + marker: `router/preflight.py` `run` (in-process `pre_pr_gate`, refuses on a
  dirty tree, records the pass marker with the label set from
  `pre_pr_gate.resolve_pr_labels`); `router/pr_labels.py` `ensure_pr_risk_label` /
  `ensure_pr_no_automerge_label` (additive, REST endpoint).
- CI watch primitives: `gh pr checks --watch --fail-fast`, `gh pr checks --json
  name,bucket,workflowRunId`, `gh run rerun --failed`, `gh run view --log-failed`, `gh pr
  view --json state,mergedAt,autoMergeRequest,headRefOid,mergeStateStatus,statusCheckRollup`
  — all specified in `skills/worktrail-go/references/ci-watch-loop.md`.
- Review threads: `router/check_review_threads.py` `check(repo, pr_number, run_record_path,
  owner, name)`; `orchestrator/verify.py` `resolve_review_threads` already calls it from
  Python.
- Run record: `router/run_record.py` `cmd_start`/`cmd_set`/`cmd_append`/`cmd_finish`;
  `finish` already code-enforces the merge-state gate, the review-thread gate, and the
  `go:risk-*` correction whenever `pull_request` is set.

## Goals / Non-Goals

**Goals:**

- One function, `land_pr()`, whose docstring is the authoritative ordered step list, and one
  console script, `worktrail-land-pr`, so agent-driven and Python-driven callers run the
  identical code.
- Fail-closed before the network: nothing is pushed unless the compile marker is current
  and the preflight gate passed against the committed head.
- Idempotent re-invocation: an agent repairing a CI code defect re-runs the same command;
  the pipeline finds the existing PR, skips create, ensures labels, and re-enters the watch
  with the patch-iteration count carried on the run record.
- The call-site enforcement test's `KNOWN_CALLSITES` collapses to `router/land_pr.py` plus
  the exempt `orchestrator/live.py`.

**Non-Goals:**

- Re-implementing the orchestrator's group-PR merge loop: `verify.py` keeps `wait_and_fix_ci`,
  `resolve_review_threads`, and `auto_merge`; `integrate.py` adopts only the pipeline's PR
  open/update step.
- Making code decide `ci-watch-loop.md` case 4 (product/design decision). The pipeline
  reports a code defect; whether it is really a product decision stays with the agent,
  who finishes the run record `blocked_product_decision` directly (already gated by
  `finish`).
- Changing `.github/workflows/*` or auto-merge policy.
- Merging PRs. The pipeline leaves merge to the repo's auto-merge automation exactly as
  Phase 8 does today; the sync-PR prompt's explicit `gh pr merge` is dropped in favour of
  the same posture (Decision 8).

## Decisions

### D1. One module, one function, one CLI: `src/worktrail/router/land_pr.py`

`land_pr(request: LandRequest) -> LandOutcome` where `LandRequest` is a frozen dataclass:
`repo` (worktree root to land from), `base_branch`, `title`, `summary` (caller's PR body
text), `route` (A–J), `risk` (default `"low"`), `gates` (list, default empty),
`run` (run-record path or `None`), `request_summary` (for a started run record),
`commit_message` (used only when the tree is dirty — see D3), `checkpoint: bool`,
`watch_timeout_s` (default 600, the Bash-tool ceiling ci-watch-loop.md already assumes),
`runner` (injectable `subprocess.run`-shaped callable, defaulting to `subprocess.run`, the
same seam `check_review_threads.check` and `pr_labels` expose so tests use `FakeRun`/
`fake_gh.py` rather than ad hoc mocks).

`LandOutcome` carries `outcome` ∈ {`landed`, `code_defect`, `review_threads_blocking`,
`ceiling`, `refused`}, `pr_url`, `pr_number`, `labels`, `run` (record path),
`final_status` (the `finish` state when finished), `merge_result`, `failing_checks`,
`log_excerpt`, `patch_iteration`, `refused_step`, `detail`. `main()` prints it as JSON and
maps `outcome` to exit codes: 0 landed, 2 refused, 3 code_defect / review_threads_blocking
(PR open, run record unfinished — the caller's turn), 4 ceiling.

Alternative considered: a class with one method per step. Rejected — the request asks for
"a single Python function" and the enforcement test's proof callables already work at
function granularity. Steps are private module functions so `integrate.py` can call the
open/update step alone (D6) without a class hierarchy.

### D2. Step order — commit precedes the preflight run

The request lists: (1) compile + commit marker, (2) labels, (3) commit + push, (4) PR.
`worktrail-preflight run` refuses on a dirty tree (`DIRTY_TREE_EXIT`) because its pass
marker is keyed to tree state, and the marker `.compile-ok` is itself an uncommitted file
after step 1. So the executable order is:

1. **Commit pending work** — if `git status --porcelain` is non-empty, `git add -A && git
   commit -m <commit_message>`; if it is non-empty and no `commit_message` was supplied,
   refuse (`refused_step="dirty_tree"`). Never touches a clean tree.
2. **Compile marker gate** — `check_compile_markers.changed_change_dirs(repo,
   f"origin/{base}")` (after `git fetch origin <base>`); for each dir run
   `conductor_compile.main([str(dir)])` in-process; then `check_marker(dir)["status"]`
   must be `"ok"` for every dir, else refuse (`refused_step="compile_marker"`, `detail` =
   gap report or `missing`/`stale`). A new marker is committed as
   `chore(<change>): record compile marker`. Skipped entirely when the list is empty.
3. **Preflight run → labels** — `preflight.run_gate(...)` in-process (the function behind
   `worktrail-preflight run`) with `--risk/--gates/--target-branch/--route/--run`; non-zero
   → refuse (`refused_step="preflight"`). Labels are read back from the pass marker
   (`preflight.read_marker(repo)["labels"]`) so they are byte-identical to what the
   PreToolUse hook will check.
4. **Push** — `git push -u origin <branch>` (or plain `git push` when upstream exists).
5. **Create or update** — `gh pr view <branch> --json url,number,state,labels`; if an OPEN PR
   exists: `ensure_pr_risk_label` + `ensure_pr_no_automerge_label(eligible=<from labels>)`
   and continue; else `gh pr create --base --head --label… --title --body <render_pr_body>`
   through `pr_labels._run_gh_cmd` (transient-TLS retry). A non-`http` last line is a hard
   failure (integrate.py's existing guard), reported as `refused_step="pr_create"` even
   though the push happened — the branch name is in `detail` for recovery.
6. **Run record** — `run` or a fresh `run_record` started with the caller's `repo`, `route`,
   `request_summary` (D4). `run_record set <run> pull_request <url>` immediately after
   step 5 so a crash mid-watch still leaves the PR recorded.
7. **CI watch** (D7) → **merge-state guard** → **review-thread gate** → **finish** or
   checkpoint `append decisions`.

This reorder is a routine sequencing fix, not a scope change: the request's step (3)
"commit + push" is satisfied by steps 1/2 (commit) and 4 (push).

### D3. Compile-marker discovery reuses CI's own function

`changed_change_dirs` is the exact function `worktrail-check-compile-markers` runs in the
`Scope check` job, so the pipeline's notion of "which change dirs need a marker" cannot
drift from CI's (including how archive moves are handled). The compile is run in-process
through `conductor_compile.main` so it honours the same tier/agent resolution as the
`worktrail-compile` console script; the LLM inference pass it may spawn for an OpenSpec
change without `files:` scope is the same cost CI already forces the author to pay by hand
today (PR #902's fix commit).

Alternative: write the marker directly from `runplan.fingerprint` without compiling.
Rejected — that is exactly the "marker present, scope never checked" hole the marker
exists to close.

### D4. A caller without a run record gets one started for it

Step 6 needs a record; `queue_triage` (Phase 2 runs before Phase 6's `run_record start`)
and drain's remediations have none. `land_pr` starts one via `run_record.cmd_start`
semantics (`--repo`, `--route`, `--request`) when `run is None` and returns its path in
`LandOutcome.run`. Routes: queue-triage fold/propose pass `"C"` (spec artifacts);
`close_stale_openspec` receives the caller's `--run` (the worktrail-go session already has
one); drain remediations pass `"E"` (continuing already-shipped work). This makes every
landing observable by `reconcile_pr_labels`, `poll_run`, and the dashboard, which all key
off run records.

### D5. `close_stale_openspec.py` lands the PR itself

Its docstring's only reason for stopping before the PR boundary was that a bespoke
`gh pr create` would bypass enforcement. With `land_pr` that is inverted: calling the
pipeline *is* the enforced path. `main()` gains `--land` behaviour by default after a
successful `flip_and_archive` — `--base`, `--run`, `--route E`, `--risk low` — and the
SKILL.md close-stale row shrinks to "run `worktrail-close-stale-openspec … --json`; it
lands the PR through the shared pipeline". `--no-land` is not added (no current caller
needs the old stop-before-PR shape; adding a flag would recreate the skip path).

### D6. `integrate.py` adopts the open/update step only

`verify.py` already owns CI wait (`wait_and_fix_ci`, spawning `ROLE_CI_FIX` workers),
review threads, and auto-merge for group PRs, with journal-based resume. Routing groups
through the full `land_pr` would run two watch loops against one PR. `integrate.py`
replaces its inline `["gh","pr","create",…]` and `_refresh_pr_labels` call with
`land_pr.open_or_update_pull_request(repo, base, head, title, body, risk, gates, route,
runner)` — the same private step `land_pr()` itself calls, so the label computation and
create/update guard are one implementation. `_refresh_pr_labels` and `_pr_label_args` are
deleted from `integrate.py` once no caller remains (queue_triage and drain currently import
`_refresh_pr_labels`; both migrate in this change). This keeps the enforcement test's AST
walk finding the `gh pr create` literal only in `land_pr.py` (+ `live.py`).

### D7. The CI watch loop in code implements ci-watch-loop.md cases 1, 2, 3 (report), 5

- Wait: `gh pr checks <n> --repo <owner/name> --watch --fail-fast` with
  `timeout=watch_timeout_s`, re-issued up to 3 times on timeout (the doc's discipline),
  then `gh pr checks --json name,bucket,workflowRunId` for the settled state.
- Case 2 (transient): any failing check whose `name` contains `Initialize containers` /
  `Set up job`, or whose `--log-failed` contains `Error response from daemon` → `gh run
  rerun <workflowRunId> --failed`, re-enter; bounded to 3 reruns; not a patch iteration.
- Case 3 (code defect): everything else failing → `gh run view <id> --log-failed` excerpt
  (last 200 lines), `run_record set <run> ci_patch_iterations <n+1>`, return
  `outcome="code_defect"`. The caller (agent via CLI, or a future Python fixer) patches,
  commits, and re-invokes; the pipeline reads `ci_patch_iterations` back from the record.
- Case 5 (ceiling): `ci_patch_iterations` already 5 on a new code defect → `finish
  --status failed_recoverable --merge-result "<iterations summary>"`,
  `outcome="ceiling"`. Watch budget exhausted (3 re-issues still pending) → same state with
  "checks still pending at watch budget".
- Case 1 (all pass): `gh pr view --json state,mergedAt,autoMergeRequest,headRefOid,
  mergeStateStatus,statusCheckRollup`. `MERGED` → `finish completed_and_merged
  --merge-result "merged externally"` (stale-head guard: if this invocation pushed a
  fixup and `headRefOid` predates it, the merge result says `STALE-HEAD MERGE …`).
  Otherwise merge-state guard (`BLOCKED` + CANCELLED/SUCCESS same-name pair → `gh run
  rerun <databaseId>` ≤ 2, re-query), then `check_review_threads.check(repo, n, run,
  owner, name, runner=…)`: `blocking` → `outcome="review_threads_blocking"` (the tool has
  already stamped `go:no-automerge`); `checked: false` → note in merge result and proceed;
  still `BLOCKED` after both → `finish blocked_product_decision`. Clear →
  `finish completed_pr_open` with `--merge-result` naming `autoMergeRequest`'s mechanism
  when armed. Checkpoint mode substitutes `append <run> decisions "<same text>"` for
  each `finish` in case 1 only, exactly as the doc's intermediate-checkpoint variant.
- Case 4 stays with the agent: `ci-watch-loop.md` keeps its case-4 text, now framed as
  "when `worktrail-land-pr` reports `code_defect` and the fix needs a product decision".

The GraphQL-outage REST fallback in the doc is **not** ported: it is a manual degradation
path; the pipeline's bounded retries already end in `failed_recoverable` with the
outage text in the merge result, which the doc says is the correct terminal state.

### D8. Prose becomes "call the pipeline"

- `worktrail-sdd-workflow/SKILL.md` Phase 8 keeps the scope-completeness gate paragraph
  (it is a run-record write the agent must author) and replaces everything from
  "Mandatory pre-PR test gate" through item 4 with one command block:
  `worktrail-land-pr --repo "$PWD" --base "$BASE" --run "$RUN" --route "$ROUTE" --risk
  "$RISK_LEVEL" --gates "$GATES" --title … --summary-file … --json`, the exit-code table,
  and "on `code_defect`, repair per `ci-watch-loop.md` case 3 and re-run the same
  command; on `review_threads_blocking`, act per its review-thread gate and re-run".
- `routes.md` §C: the spec PR is `worktrail-land-pr … --checkpoint`.
- `worktrail-go/SKILL.md`: close-stale row (D5); Phase 3 CI-watch paragraph points at the
  command; Phase 2 step 3 replaces "report … and STOP" with "report the entry's `pr_url`
  and `landing.outcome`; on `code_defect`/`review_threads_blocking` continue with
  `worktrail-land-pr` against `landing.run` from `landing.worktree` until terminal, then
  stop" — the apply CLI must therefore be invoked with the Bash `timeout` parameter at
  600000 (it now blocks for the watch).
- `subagent-prompts.md` sync step: replace the `gh pr create` block and Step 4b with
  `worktrail-land-pr … --route E --risk low --checkpoint` (the run continues to teardown);
  the explicit `gh pr merge` is dropped — merge belongs to the repo's automation, as on
  every other route.
- `pipeline-details.md` step 3: the marker note says the pipeline commits the marker on
  landing; the pre-launch guard text is unchanged (the orchestrator path still needs the
  marker committed before fan-out).
- `ci-watch-loop.md` gains a top note: "Implemented in code by `worktrail-land-pr`
  (`router/land_pr.py`); this reference documents the classification the code applies and
  the two steps that remain the agent's: repairing a reported code defect and deciding
  case 4." Its body is otherwise unchanged in this change (a later docs-only change may
  shrink it once the code has soaked).

### D9. Tests

- **Unit (`tests/router/test_land_pr.py`)**: `FakeRun`-style injected `runner` scripting
  each `gh`/`git` reply; one test per step boundary (dirty tree without message → refused;
  compile gaps → refused, no push call recorded; preflight deny → refused; existing OPEN PR
  → no create call, labels ensured; transient name → rerun, iteration unchanged; code
  defect → iteration incremented, no finish; fifth defect → `failed_recoverable`; MERGED →
  `completed_and_merged`; blocking threads → no finish; checkpoint → `append decisions`).
- **Integration (`tests/router/test_land_pr_integration.py`)**: real `git` with a bare
  remote (reusing `tests/orchestrator/lifecycle/fake_gh.py` first on `PATH`), an OpenSpec
  change dir whose `tasks.md` differs from `origin/main`; (a) no marker + compile forced to
  `--no-llm`-equivalent gaps → asserts `git ls-remote` shows no head branch and no PR in
  `$GH_FAKE_STATE`; (b) stale marker → same; (c) marker made current → pushed, PR present
  in fake state with the labels the fake preflight recorded.
- **Per-caller proof**: `mock.patch("worktrail.router.land_pr.land_pr")` (or the
  open-step for integrate) asserting one call with the expected `LandRequest` fields, in
  each caller's existing test module; `test_pr_creation_callsite_enforcement_coverage.py`
  `CALLSITE_CONSUMERS` becomes `{"router/land_pr.py": <proves labels come from preflight
  marker>, "orchestrator/live.py": <existing sandbox proof>}`.
- `tests/test_plugin_surface.py` already enforces that `worktrail-land-pr`, once named in
  a SKILL.md, is a real entry point; `test_skill_prose_enforcement_coverage.py`'s
  `FILE_CONSUMERS` is re-run unchanged (the label-family markers still appear in the
  edited files and the code-enforcement proof still holds).

## Risks / Trade-offs

- [Intake-triage apply and drain remediations now block for a CI watch (minutes per PR)]
  → callers pass their existing `timeout` as `watch_timeout_s`; drain's per-finding
  try/except and existing-PR re-detection make a budget-exhausted `failed_recoverable`
  self-healing on the next sweep; the skill text sets the Bash `timeout` to 600000 for
  the apply call, the same value it already uses for `gh pr checks --watch`.
- [In-process compile may spawn an LLM from inside `queue_triage`/drain] → it already
  spawns one for `propose-change` authoring; the compile honours `COMPILE_TIMEOUT_DEFAULT`
  and the same tier resolution; failure is a refusal before push, never a half-landed PR.
- [`queue_triage` tears its worktree down in `finally`; a code defect needs the worktree] →
  on `code_defect`/`review_threads_blocking` the worktree is kept and its path returned in
  the apply result (`landing.worktree`); it is removed only on `landed`/`refused`/`ceiling`.
- [Two watch loops if `integrate.py` ever called `land_pr()` in full] → D6 restricts it to
  the open/update step; a unit test asserts `integrate` never imports `land_pr.land_pr`.
- [Run records started by the pipeline for drain could accumulate] → they finish in the
  same invocation; `run_record prune`/`sweep-orphans` already cover abandoned ones.
- [Deleting `integrate._refresh_pr_labels` breaks an out-of-tree import] → grep from the
  repo root and GitNexus impact both show only `queue_triage.py` and `drain.py` import it;
  both migrate here.

## Migration Plan

1. Land `land_pr.py` + entry point + its own tests first (group 1 in tasks.md).
2. Migrate the four Python callers in parallel (group 2); each PR-open block is deleted in
   the same task that adds the call.
3. Reduce skill prose in parallel (group 3); `test_plugin_surface` gates the command name.
4. Collapse `KNOWN_CALLSITES`; run the full suite and the golden orchestrate check.

Rollback: revert the change as a unit. Callers' old inline blocks are deleted rather than
kept behind a flag (a flag would be a new skip path), so partial rollback is not offered.

## Open Questions

None that affect specs, approach, or task breakdown. Two deferrable follow-ups, noted so
they are not lost: whether `ci-watch-loop.md` should later be shrunk to only the agent-side
steps once the code path has soaked; and whether drain should stop passing `agent`/`spawner`
to remediation actions now that none spawns for PR opening (pre-existing shape, untouched
here).
