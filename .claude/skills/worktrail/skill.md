---
name: worktrail
description: Task orchestration internals — RunPlan safety, AddOn hand-off, task-source abstraction, and frontier scheduling for src/worktrail
triggers:
  files:
    - src/worktrail/conductor/**
    - src/worktrail/orchestrator/**
    - src/worktrail/taskformats/**
    - src/worktrail/addons/**
    - src/worktrail/workqueue/**
    - src/worktrail/router/**
  keywords:
    - RunPlan
    - runnable_frontier
    - TaskSource
    - add_ons
    - AddOn
    - worktree
    - spec_ref
    - merge_method_by_base
    - auto_merge
    - _pipeline_scheduler
    - _slot_refilling_fanout
    - _resolve_max_workers
    - max_parallel_workers
    - FIRST_COMPLETED
    - QUARANTINED
    - _salvage_uncommitted
    - pre_commit_cmd
    - missing_context
    - SKIPPED-SMALL-DIFF
    - build_worker_prompt
    - _unrelated_test_failure
    - _rerun_failed_checks
    - rerun_attempted
    - worktree_guard_hook
    - worker_guard_settings_json
    - PreToolUse
---

You are working on **worktrail's task-orchestration core**: compiling specs/changes into a
schedulable plan, fanning work out across git worktrees, and handing finished work off to a PR.

## Business rules / invariants

- **RunPlan edge-dropping invariant** (`conductor/runplan.py` `apply_to_tasks`): a dependency
  edge may only be dropped if BOTH endpoints carry a non-empty `files` scope. This falls
  directly out of `runnable_frontier` (`orchestrator/coordinator.py`), which treats an empty
  file set as "collides with nothing" — decoupling a scope-less task from its predecessor would
  let it race with unbounded blast radius. Never relax this to "drop if either side has scope."
- **File collision detection normalizes paths** (`coordinator._norm_files`) via
  `os.path.normpath` — `./src/a.ts` and `src/a.ts` must compare equal or two tasks that declare
  the same file run in parallel and collide at integration.
- **Task status vocab is not symmetric**: `"done"` = worker completed in the *current* run
  (branch exists, deliverable); `"completed"` = already integrated in a *prior* run (never
  re-merge). Conflating them re-merges dead branches.
- **`TAIL_KINDS = {"e2e", "cleanup"}`** are always held out of the parallel fan-out and run
  serialized last — a RunPlan can add fields but can never "un-hold-out" a tail task (`kind` is
  sticky in the safe direction).
- **Both fan-outs refill a freed slot immediately, never per tick**, through the one shared
  helper `live._slot_refilling_fanout` (called by `live_run_real` and `_pipeline_scheduler`):
  tasks run in one long-lived `ThreadPoolExecutor(max_workers)`
  and the loop `wait(..., return_when=FIRST_COMPLETED)`s, re-running `runnable_frontier` after
  every completion. The old loop blocked on the whole tick's futures, so a fast task's slot
  idled behind the slowest task's review/fix strikes (2 of 3 slots idle 35+ min with 4 ready
  tasks, run orchestrator-throughput, 2026-09-02). Re-running the frontier while tasks are in
  flight is only safe because a dispatched task is flipped `pending -> "claimed"` under
  `state_lock` *before* the next frontier pass — `coordinator.IN_FLIGHT` includes `claimed`, so
  the frontier neither re-dispatches it nor hands out its files. Never submit a task to the
  pool without that transition, and never compute the frontier outside the lock. Do not
  reintroduce a private fan-out loop in either engine; extend the helper instead.
- **Journal-replayed mid-flight tasks resume inside the pool, concurrently** — passed to the
  helper as `initial_in_flight` and submitted before the first frontier pass, alongside
  newly-runnable pending tasks. They used to be driven one at a time before fan-out began.
- **A run-budget break does not abandon mid-flight tasks**: the budget check is the helper's
  `should_stop(tick)` callback (`_budget_stop` in each engine) — True only stops *new* dispatch;
  the fan-out pool is shut down with
  `wait=True` in a `finally`, so running tasks reach a terminal state (journaled as they go)
  before `_dispatch_terminal_groups()` runs a final time and integrate/verify proceeds.
  `tests/orchestrator/test_slot_refill_scheduler.py` pins both the refill and the
  `max_workers` cap / dependency ordering, plus the concurrent in-flight resume and the
  legacy-engine refill.
- **Fan-out width is resolved in code, not fixed at 3** (`live._resolve_max_workers`, applied in
  `_pipeline_scheduler` right after `apply_run_plan`): an explicit `--max-workers` wins; else the
  repo policy's `max_workers`; else the plan's own width (`conductor.parallelism.profile`) capped
  by policy `max_parallel_workers` (default 6). `--max-workers` therefore defaults to `None` and
  `full_real`/`_full_real_inner`/`_pipeline_scheduler` accept `int | None`. The fixed default of
  3 silently ran a width-7 plan as three serial ticks (2026-09-02); the effective value is printed
  next to the plan so the cap is visible.
- **A group entering QUARANTINED prints `!! QUARANTINED [<name>] <reason>` the moment it
  happens** (`_pipeline_scheduler`'s group-state recorder). Three groups sat quarantined for over
  an hour while the log showed only ticks and CI polls (2026-09-02); do not rely on the journal
  alone to surface it.
- **`precheck` treats "every listed file already exists" as INFO, not WARN, for OpenSpec tasks**
  (task `path` ends in `tasks.md`): an OpenSpec `files:` line is a shared-checklist scope with no
  per-task create/modify split, so all files existing is the normal state of a task editing
  existing modules. Devkit `TASK-NNN.md` tasks keep the "possibly already implemented" WARN. The
  WARN false-fired (exit 1) on every unattended launch touching existing files on 2026-09-02.
- **A timed-out review/ci-fix worker's uncommitted work is salvaged, not discarded**
  (`verify.Verifier._salvage_uncommitted`): tracked modifications only (`git add -u`), committed
  and pushed to the group branch before the strike is counted; a salvage failure never masks the
  strike. And in `wait_and_fix_ci`, a failed or timed-out ci-fix attempt is *one strike*, not the
  end of the loop — it re-polls CI (a salvaged commit may already be green) and spends the
  remaining strikes before returning "CI fix loop exhausted".
- **A red check tries one free `gh run rerun --failed` before any ci-fix worker spawns**
  (`verify.Verifier._unrelated_test_failure` / `_rerun_failed_checks`, gated by a per-call
  `rerun_attempted` flag in `wait_and_fix_ci`): a failing test whose path never appears in
  `gh pr diff --name-only` cannot be fixed by a ci-fix worker editing this PR's own diff no
  matter how many strikes are spent on it. `_unrelated_test_failure` regex-matches `tests/...py`
  paths out of the failed run's log and checks them against the diff's changed files
  (disjoint-and-nonempty → True); a positive match issues `gh run rerun --failed` for every
  failed workflow run at the branch's current head SHA (`_rerun_failed_checks`) and
  `wait_and_fix_ci` re-polls once. The probe fires before the strike loop's first spawn and
  costs it nothing — `rerun_attempted` ensures it tries at most once per `wait_and_fix_ci` call,
  so a still-red rerun falls through to the unchanged spawn-and-strike path with the full
  strike budget intact. Conservative by construction: no recognizable test path in the log, no
  diff to compare, or any overlap between failing paths and changed files → False, leaving the
  existing loop as the only path for a failure this heuristic cannot confidently classify.
- **`review_status: SKIPPED-SMALL-DIFF` is a passed review** (`dispatch.transition`): it routes
  to `cleaning` like `PASSED` but leaves `retry_count` untouched. Any other value outside
  `PASSED|FAILED|SKIPPED-SMALL-DIFF` on a successful review report still raises `ValueError`
  (malformed, re-dispatch review). This is the report shape `live.drive()` synthesizes when
  policy `review_skip_max_diff_lines` > 0 skips a small verified implement diff's first review.
- **Out-of-scope review findings travel in `missing_context`, never only in `notes`**
  (`dispatch._ROLE_ACTION` review and fix text): a reviewer lists untouchable files as
  repo-relative paths in the report-back's `missing_context` field; a fix worker declines such a
  finding with `status: failed` plus the paths. Keep both action strings in sync — the
  `_UNCHANGED_ROLE_FIX` pin in `tests/orchestrator/test_dispatch.py` fails on drift.
- **`pre_commit_cmd` reaches workers as a hard rule, gated by role** (`WorkerPromptCtx.pre_commit_cmd`,
  `build_worker_prompt`): only implement and fix prompts (and `build_group_prompt`'s ci-fix
  prompt, via `ctx["pre_commit_cmd"]`) get `Run \`<cmd>\` before every commit.`; review and
  cleanup prompts never carry it, and an unset/`None` command emits no line at all.
- **`LiveSpawn.pre_commit_cmd` is a lazy property, not resolved in `__init__`**: the policy
  file is read via `router/policy.load_policy(self.repo)` only on first access (first spawn),
  and the setter marks it loaded so tests can inject a value without touching a policy file. A
  fan-out that never spawns, or a test driving `live_run_real` with a stub repo, must never read
  the policy — do not move the load back into the constructor.
- **Every `claude` worker carries a PreToolUse worktree guard, injected via `--settings`**
  (`spawnlib._with_default_setting_sources` → `worker_guard_settings_json`, hook module
  `orchestrator/worktree_guard_hook.py`). The brief's `Worktree (operate ONLY here)` line is
  advisory: on 2026-09-05 (run full-1788634519, task 2.1) an implement worker wrote into the
  canonical `~/projects/worktrail` checkout, and the operator's own user-level guard hook never
  fired because workers run with `--setting-sources project,local` (which drops user settings).
  `--settings` is an additional source that survives that flag, so the guard travels with the
  package. Rules: `Write`/`Edit`/`NotebookEdit` targets must resolve inside the worker's `cwd`
  (sibling-prefix paths like `<wt>-other/` are outside); `Bash` is denied only when the command
  spells out the canonical checkout path (`git rev-parse --git-common-dir` parent) and that
  differs from `cwd`. The hook runs as `sys.executable -m worktrail.orchestrator.worktree_guard_hook`
  (no PATH dependency), fails open on any exception (a broken guard must never stall a run), and
  its `deny` is honored under `--permission-mode bypassPermissions` (live-verified 2026-09-05).
  An explicit `--settings` in `extra_args` is respected and not overridden; non-`claude` agents
  get no guard. `tests/orchestrator/test_worktree_guard_hook.py` pins the decisions and the
  injection.
- **AddOns run after a task's own work, before commit** (`addons/runner.run_addons`,
  called from `orchestrator/integrate.py` and `router/preflight.py`). `install`/`configure`
  failures are swallowed (best-effort priming); `run()` failures propagate. An add-on config'd
  `required: true` turns a `run()` failure into `AddOnFailure`, which blocks/quarantines the PR
  the same way a failed drift/smoke gate does; a non-required failure is logged `WARN` and
  skipped. Never call an `AddOn`'s methods directly outside `addons/runner.py`.
- **An add-on's self-reported `changed` is not trusted for committing** — `_stage_and_commit`
  always re-checks `git diff --cached --quiet` after staging, so an add-on claiming a change
  that turns out identical to HEAD produces no empty commit.
- **`policy["add_ons"]` needs real YAML, not `parse_policy_yaml`'s one-level-nesting subset**
  (`router/policy._resolve_add_ons`) — per-add-on config is itself a nested dict
  (`add_ons: {aspens: {enabled, target, required}}`), and the one-level parser flattens those
  keys up as siblings of the add-on name instead of nesting them. A malformed top-level shape
  falls back to `{}`, never widening the zero-add-ons default.
- **`AspensAddOn.install` never overwrites an `aspens` CLI already on PATH** (`addons/aspens.py`):
  it only runs `npm install -g aspens` when `shutil.which("aspens")` is empty, and still refreshes
  the machine-local freshness marker when the CLI is present. An unconditional install replaced an
  operator's `npm link`ed fork with the registry build (2026-09-01) and reintroduced a destructive
  doc-sync rewrite — keep the PATH check.
- **Policy-sourced defaults for `full-real` flags live in code, not in agent prose.**
  `live._default_merge_method` (sibling of `_default_post_merge_smoke_cmd`) resolves
  `merge_method_by_base[--base]` via `router/policy.merge_method_for_branch` whenever
  `--merge-method` is omitted; an explicit flag always wins, and a branch with no override
  resolves `None` so `verify._detect_merge_method`'s repo-wide GitHub-settings query still
  applies. Before this default existed the flag was only ever sourced from policy by the calling
  agent, and a launch that forgot it merged datalena group PRs with `--merge` against a
  squash-only `dev` and quarantined each on a green tree (2026-09-01). Never add a new
  policy-backed `full-real` flag that relies on the agent remembering to pass it.
  `_resolve_max_workers` is the same pattern for `--max-workers`.
- **`auto_merge()` treats a GitHub METHOD rejection as retryable, not a quarantine.** When the
  direct `gh pr merge` fails with an `_AUTO_MERGE_METHOD_SIGNALS` match (e.g. "Merge commits are
  not allowed on this repository"), `_retry_auto_merge_methods(..., auto=False)` retries the
  direct merge with each remaining method (squash, rebase, merge) before anything else and caches
  the working method on `self._merge_method` for later groups. A direct retry that hits a
  `_BRANCH_PROTECTION_SIGNALS` block returns `(False, method, err)` immediately so the existing
  `--auto` arming fallback arms with the method GitHub accepted, not the rejected one — do not
  keep iterating methods past a protection block or you overwrite that signal.

## Critical files
- `conductor/runplan.py` — RunPlan safety rule and `unordered_file_collisions()` assertion
- `orchestrator/coordinator.py` — `runnable_frontier`/`plan_groups`, pure/side-effect-free;
  `IN_FLIGHT` is the status set the slot-refilling scheduler relies on
- `taskformats/base.py` — `TaskSource` protocol; `orchestrator/` must never construct a
  format-specific task path itself, always go through the active `TaskSource`
- `addons/runner.py` — the only caller of `AddOn.install`/`configure`/`run`
- `orchestrator/dispatch.py` — `_ROLE_ACTION` per-role action text (review/fix `missing_context`
  rule), `build_worker_prompt`/`build_group_prompt` hard rules (`pre_commit_cmd`), and
  `transition`'s review-status routing (`PASSED|FAILED|SKIPPED-SMALL-DIFF`)
- `orchestrator/live.py` — `_default_merge_method`/`_default_post_merge_smoke_cmd`/
  `_resolve_max_workers`: the code-level policy defaults `main()` applies to `full-real` when a
  flag is omitted; `_slot_refilling_fanout`: the one slot-refilling fan-out loop both
  `live_run_real` and `_pipeline_scheduler` drive, with its `on_completion` hand-off to
  `_dispatch_terminal_groups` and the integrate+verify pool; `precheck`: the OpenSpec-vs-devkit
  "all files exist" INFO/WARN split; `LiveSpawn.pre_commit_cmd`: the lazy policy-backed
  property threaded into the worker ctx
- `orchestrator/spawnlib.py` — `_with_default_setting_sources`/`worker_guard_settings_json`:
  the per-`claude`-spawn defaults (`--setting-sources project,local` plus the `--settings`
  guard-hook JSON) that `build_cmd` applies unless the caller passed them explicitly
- `orchestrator/worktree_guard_hook.py` — the PreToolUse guard itself (`decide`,
  `_canonical_root`, `HOOK_MATCHER`); runnable as `python -m`, reads the hook payload on stdin,
  prints a `deny` decision or nothing, always exits 0
- `orchestrator/verify.py` — `auto_merge()` and `_retry_auto_merge_methods`; the only place a
  merge-method rejection is turned into a method retry; `_salvage_uncommitted` and the
  per-strike `continue` in `wait_and_fix_ci`; `_unrelated_test_failure`/`_rerun_failed_checks`,
  the free-rerun probe `wait_and_fix_ci` tries once before spawning a ci-fix worker

---
**Last Updated:** 2026-09-05
