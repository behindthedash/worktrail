# Investigation: does a post-integration file-scope check close the recurring dep-inference gap?

Brief: `20260807-103507-design-and-build-a-deterministic`
Run record: `go-20260807-110516`

## Verified Observations

- `src/worktrail/conductor/plan_audit.py` already implements a per-task, declared-vs-actual
  file-scope diff (`audit_plan()`): for each task with declared `files`, it diffs the task's
  branch against `base_ref` and reports `undeclared` (touched, not planned) and `unused`
  (planned, never touched). Its own docstring (lines 1-16) states it is "deliberately **not**
  wired into the live dispatch loop -- a forensic/tuning signal for the compile prompt must
  never gate or slow a run down. Run it by hand, or from a post-run check."
- `src/worktrail/orchestrator/verify.py::_forbidden_paths_touched` (lines 528-581) already
  computes the same touched-vs-declared mismatch **at group granularity**, using
  `coordinator.declared_files_by_group()`, and logs it automatically on every real run
  (`plan-audit: touched-not-declared [...]`, line 573-576). PR #63
  ("fix: log verify.py's touched-vs-declared mismatch as a plan-audit signal") shipped this.
  It is explicitly log-only: `tests/orchestrator/test_verify.py::test_touched_not_declared_is_logged_not_gated`
  asserts the method's *return value* (what actually gates a worker strike) is unaffected by
  the mismatch, only the log line changes.
- `_forbidden_paths_touched` is called from exactly one site: `_spawn_group_worker` (line 468),
  which is invoked only for `resolve`/`ci-fix` workers spawned during `verify.py`'s
  mergeability/CI-fix loop — i.e. it covers incremental edits a resolve/ci-fix worker makes on
  the group branch, not the original fan-out task branches integrated into that group branch
  by `integrate.py`. Those are covered only by the standalone, manual `plan_audit.py` path.
- `coordinator.py::plan_groups()`'s own docstring (lines 238-260) documents the exact incident
  cited in this brief by name: "observed on datalena's embed-widget-auth-hardening run (PRs
  #2138 base, #2139 feature-2, both merged; the migration task's own feature-1 group
  quarantined on an unrelated stale-head assertion and was never merged until the manual
  recovery in #2144, leaving dev broken in between)." The same docstring states plainly:
  "Folding migration tasks into BASE does not fix the missing-deps-edge root cause, but it
  removes the specific failure mode" — i.e. PR #166's fix (this session's earlier work) is
  itself documented as a mitigation, not a root-cause fix, by the person who wrote it.
- `plan_groups()`'s migration-fold is opt-in via `migration_patterns` (repo policy key
  `migration_path_patterns`, default `[]` — no behavior change unless configured).
  `~/projects/datalena/docs/specs/go-policy.yaml` has **no** `migration_path_patterns` key at
  all (`grep -n "migration_path_patterns"` returns nothing). Datalena's migrations live under
  `api/migrations/versions/*.py` (confirmed via `find`). The mitigation that PR #166 shipped
  specifically for this incident class has never been enabled for the repo where the cited
  incident happened.
- `src/worktrail/orchestrator/integrate.py` (lines 676-678, `_run_integration_smoke`) runs each
  group's `integrate_smoke_cmd` against **that group's own integration worktree** (base +
  that group's tasks merged), independently and — for non-stacking FEATURE groups — in
  parallel with sibling groups. A FEATURE group that does not declare a `deps` edge or a
  shared-file edge to another FEATURE group is never tested against that sibling's merged
  state before its own PR is created; `plan_groups()` only forces sequencing (`depends_on:
  ["base"]`) when a stacking condition (dep edge or shared file with BASE) is detected.
- The earlier duplicate-brief-detection incident (memory `project-worktrail-openspec-compile-dep-inference-gap`,
  worktrail PR #105) has the same shape as the migration incident: task 3.3 was compiled with
  `deps=2.3` only, omitting the real dependency on task 2.2. Both 2.2 and 3.3 were correctly
  scoped to their own declared `files` — the gap was a missing *edge* between two correctly-scoped
  tasks, not either task touching something outside its own declared scope.

## Unknowns / Missing Evidence

- Whether datalena's `integrate_smoke_cmd` (if configured) runs a migration-dependent test
  suite, and whether such a suite would have caught the embed-widget-auth-hardening break had
  it run against the fully-merged `dev` HEAD after feature-2 merged — not verified here (would
  require reading datalena's CI logs from that incident, out of scope for a worktrail-repo
  investigation).
- Whether `compile.py`'s `PROMPT` could be strengthened to catch "task touches a table/route a
  migration creates" as a class of implicit dependency, versus requiring a structural
  (non-LLM) backstop — not evaluated; would need a review of `compile.PROMPT` and a sample of
  its false-negative rate, which is outside Route I's diagnostics-only scope.

## Hypotheses

- **H1 (root cause, high confidence given the two independent occurrences and the code's own
  docstring):** the recurring failure class is *missing dependency edges* between two
  correctly-scoped tasks, produced by `compile.py`'s LLM inference pass, not *file-scope
  under-declaration* by any single task. Both cited incidents show each task touching exactly
  its own declared files; nothing was undeclared or unused at the individual-task level.
- **H2:** a file-scope "touched vs. declared" post-integration check — the mechanism this
  brief's Focus describes, and the one `plan_audit.py`/`_forbidden_paths_touched` already
  implement in log-only form — would **not** have caught either cited incident, because there
  is no file-scope mismatch to detect in either one. Generalizing that specific mechanism
  (e.g. making it gate instead of log) would add a real safety net for a *different* bug class
  (a task quietly touching more than it declared) without addressing the class this brief is
  actually motivated by.
- **H3:** the concrete gap that let the migration incident reach `dev` is that independent
  FEATURE groups are validated in isolation against `base` (per-group `integrate_smoke_cmd`,
  parallel, no cross-group state), so a semantic dependency between two groups that never
  produced a `deps` edge or shared-file edge is structurally invisible to any per-group check,
  file-scope or otherwise — only a check against the *cumulative* merged state (after each
  group lands) could have caught it.
- **H4:** the specific mitigation already shipped for this exact incident class
  (`migration_path_patterns` → migration-fold into BASE) was available before the cited
  incident's repo (datalena) needed it and is still unconfigured there today — the smallest,
  lowest-risk immediate action is enabling it, independent of any new structural work here.

## Validation Steps

- For H2: re-derive `plan_groups()`/`declared_files_by_group()` output for the
  embed-widget-auth-hardening run's actual compiled RunPlan (if the cache under
  `datalena-worktrees/runplans/` still holds it) and confirm feature-2's touched files were a
  subset of its declared files — would make H1/H2 a **Confirmed** rather than inferred finding.
  Not performed here (cross-repo, cache may have been pruned since 2026-08-0x).
- For H3: prototype a cumulative post-merge smoke re-run (run `integrate_smoke_cmd` again
  against the actual updated `base` HEAD immediately after each group merge, before the next
  merge is allowed to proceed) on a synthetic two-group fixture with a deliberately-omitted
  cross-group dependency, and confirm it fails where the current per-group-isolated check
  passes.
- For H4: confirm with datalena's own team/policy owner that `api/migrations/versions/**` is a
  correct and complete migration-path glob (worktrail's `_touches_migration` uses
  `fnmatch.fnmatch`, so the pattern must match relative paths as `git diff --name-only`
  reports them) before enabling `migration_path_patterns` in datalena's `go-policy.yaml`.

## Confirmed Root Cause

Not confirmed to the standard this repo's own no-guessing rule requires for a code change —
H1 is well-evidenced (two independent incidents, both matching the same shape, one documented
in the shipped fix's own commit message) but not proven via direct reproduction in this
session. Treat H1 as the working hypothesis for any follow-up design.

## Recommendation

Do **not** build the mechanism as the brief's Focus originally framed it (a generalized
touched-vs-declared file-scope gate). That mechanism already exists twice in this codebase
(`plan_audit.py`, `verify.py::_forbidden_paths_touched`) and, per H2, would not have caught
either incident that motivated this brief — it targets a different, real, but distinct bug
class (scope over-reach by a single task) from the one actually observed (missing
cross-group dependency edges between correctly-scoped tasks).

Two follow-ups, deliberately split by purpose/repo per this workspace's PR-scope doctrine:

1. **Immediate, zero-code, different repo:** configure
   `migration_path_patterns: ["api/migrations/versions/**"]` in datalena's
   `docs/specs/go-policy.yaml` to turn on the mitigation PR #166 already shipped for exactly
   this incident class. Captured as a separate handoff brief (datalena repo, docs-only PR) —
   out of scope for this worktrail-repo run.
2. **Structural, needs design + cassette coverage, Route J:** a cumulative post-merge
   integration check in `integrate.py`/`verify.py` — re-run `integrate_smoke_cmd` (or a
   cheaper subset) against the actual updated `base` HEAD after each group merges, before the
   next independent group is allowed to merge, so a semantic cross-group dependency that
   produced no file-scope or `deps`-graph signal still gets one real chance to fail loudly
   before it reaches a human. This is production orchestrator code (`routing_cassette_required`
   gate) and needs its own `/go` dispatch, scenario design (what "cheaper subset" means so this
   doesn't serialize every run), and cassette scenarios — not attempted in this Route I run.

Completion: `investigation_complete`.
