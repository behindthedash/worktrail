## 1. The exclusion list: the key and automatic selection

- [ ] 1.1 [impl] Teach the routing policy and the automatic pick about
      `routing.drain.exclude_repos`.
      (a) In `src/worktrail/router/policy.py`, extend the `drain:` block's validator
      (`_validate_routing_drain`, `:750`) with the new `exclude_repos` key: absent/`None`
      resolves to `[]`; otherwise the value must be a `list` whose every entry is a `str` with
      a non-empty stripped form (each entry normalized to its stripped value), else raise
      `OperatorConfigError("routing.drain.exclude_repos must be a list of non-empty repo
      names")` -- the same loud-failure treatment `max_workers` gets, because a malformed
      drain block is stated operator intent, not a value to drop-and-warn about. Carry the
      normalized list into the mapping the validator returns, and include it in the `drain`
      mapping `resolve_routing()` (`:1304`) builds; keep the `"drain": {}` fallback (`:1362`)
      for the no-routing case, and note in the docstring's returned-shape comment that a
      missing `exclude_repos` key means the empty list. Add a machine-wide read helper beside
      `resolved_routing_file_path()` (`:1261`) -- `machine_wide_exclude_repos(meta: dict |
      None = None)` -- that reads `resolved_routing_file_path()` directly (never
      `_resolve_routing()`'s repo-local branch, so a governed repo can neither shadow nor
      extend the operator's list) and returns `[]` for an absent, unreadable, or non-mapping
      file, while letting `OperatorConfigError` propagate for a malformed `drain:` section.
      In `_resolve_routing()` (`:1273`), when the mapping it returns came from a non-empty
      repo-local `routing:` block that declares a non-empty `drain.exclude_repos`, append a
      warning to `meta["warnings"]` naming the key as machine-wide only and stating the copy
      is not honored.
      (b) In `src/worktrail/router/dashboard.py`, give `auto_pick_brief()` (`:2282`) an
      `exclude_repos: Collection[str] = ()` parameter and, in the per-brief gates beside the
      `repo_filter` check (`:2374`) and the `release-gate` check (`:2380`), skip a brief whose
      repo basename is excluded with the structured reason `repo-excluded` -- a colon-free
      reason, so `log_auto_pick_miss()` aggregates it as its own coarse bucket -- and never
      return it as the pick. Honor the explicit-scope rule in the same place: when
      `repo_filter` is passed and names an excluded repo, discard it from the exclusion set
      for this call, so `--auto-repo R` still picks `R`'s briefs. In `main()` (`:3722`), read
      the list once through `policy.machine_wide_exclude_repos()` and pass it into the
      `auto_pick_brief()` call (`:3980`); the read is best-effort -- catch any exception the
      way `_load_dashboard_policy()` (`:439`) does, print the warning to stderr, and proceed
      with an empty list -- because the drain's own startup is the loud gate for a malformed
      routing file. Update the module's skip-reason docstrings to carry the new reason.
      (c) In `tests/router/test_policy.py`, cover with `$WORKTRAIL_HOME`/`$WORKTRAIL_ROUTING_FILE`
      fixtures: an absent key and an absent `drain:` block both resolve as empty; a declared
      list is carried in order; a bare string, a list containing a non-string, and a list
      containing an empty or whitespace-only entry each raise `OperatorConfigError` naming
      `routing.drain.exclude_repos`; a repo-local `routing:` block declaring the key produces
      the machine-wide-only warning while excluding nothing; and `machine_wide_exclude_repos()`
      returns the machine-wide list, returns `[]` when no routing file exists, and raises for
      a malformed `drain:` section. In `tests/router/test_dashboard.py`, cover: an excluded
      brief is skipped with reason `repo-excluded` and is never the pick; absolute-path,
      bare-name, and `owner/name` repo values all match by basename; a non-excluded brief in
      the same queue is still picked; `repo_filter` naming the excluded repo picks it and
      records no `repo-excluded` skip; and a malformed machine-wide drain section yields the
      warning plus an unfiltered pick rather than a crash.
      (Requirements: The machine-wide routing file declares repos excluded from unattended draining; A brief for an excluded repo is not ready and is never automatically picked; An explicit repo scope overrides the exclusion list)
      files: src/worktrail/router/policy.py src/worktrail/router/dashboard.py tests/router/test_policy.py tests/router/test_dashboard.py

## 2. The drain honors the exclusion list end to end

- [ ] 2.1 [impl] Make every unattended path the drain drives drop excluded repos, from
      discovery through the ready count and the pre-passes.
      (a) In `src/worktrail/workqueue/seed_backlog.py`, give the three finders --
      `find_needs_tasks_specs` (`:84`), `find_ready_specs` (`:121`), `find_epic_gaps`
      (`:162`) -- an `exclude_repos: Collection[str] = ()` keyword after `go_repo`, dropping
      excluded names from `names` where the existing `go_repo` filter runs (`:92`, `:131`,
      `:187`), so an excluded repo is never scanned and none of its specs or epics can become
      a brief; `seed_backlog()` (`:341`) takes the same keyword and forwards it to all three.
      Its CLI (`main()`, `:442`) is unchanged -- the list is operator policy resolved by the
      caller, not a new per-invocation flag.
      (b) In `src/worktrail/workqueue/queue_triage.py`, add an
      `exclude_repos: Collection[str] = ()` parameter to `inventory()` (`:848`) and drop
      every group whose repo key matches an excluded name by basename (`Path(key).name`;
      `NO_REPO_KEY` and every other group key behave exactly as before, and a group left
      empty is already dropped). Add a repeatable `--exclude-repo NAME` argument to the
      `evaluate` subparser in the CLI wiring around `cmd_evaluate` (`:4173`) and pass the
      collected names into `inventory()`, so the drain's pre-pass can scope the evaluation
      without re-implementing the grouping.
      (c) In `src/worktrail/drain/drain.py`, add
      `exclude_repos: list[str] = field(default_factory=list)` to `DrainConfig` (`:2231`) and
      resolve it in `main()` from the already-resolved routing table
      (`resolved_routing["drain"].get("exclude_repos") or []`, beside the `max_workers` read
      at `:3129`). Add a `scoped_repo_names(repos_root, go_repo=None, exclude_repos=())`
      helper beside `repo_sandbox_roots()` (`:299`) that wraps `discover_repo_names()`,
      applies the existing `go_repo` restriction, then drops excluded names; adopt it at
      `repo_sandbox_roots()` (`:304`) and at the six finders' discovery sites (`:819`, `:855`,
      `:890`, `:926`, `:967`, `:1336`), each of which gains an `exclude_repos:
      Collection[str] = ()` parameter. Thread the list through the sweep engine:
      `StageRemediation.finder`'s type (`:1814`) and its call (`:1937`) in
      `sweep_remediations()` (`:1897`), which itself takes `exclude_repos` and receives
      `config.exclude_repos` at `drain()`'s pre-loop (`:2508`) and post-loop (`:2840`) calls.
      At the top of `drain()` (`:2423`), normalize the explicit-scope rule once -- drop
      `config.go_repo` from the run's list when it is present there and log that the explicit
      `--go-repo` overrides `routing.drain.exclude_repos` for this run -- and report what was
      applied before the first iteration (so `--dry-run` shows it too): one log line naming
      the resolved list when it is non-empty, plus, only when `config.repos_root` is an
      existing directory, one line naming entries that match no repo there. An unmatched
      entry is inert and never changes the exit status; a missing `--repos-root` stays the
      existing no-op with no such line. Extend `count_ready_briefs()` (`:343`) with
      `exclude_repos: Collection[str] = ()`: a brief whose `repo` value's basename
      (`Path(str(repo)).name`, so an absolute path, a bare name, and an `owner/name` form all
      match) is excluded no longer counts as ready, and `drain()`'s per-iteration call
      (`:2610`) passes the normalized list -- so a queue holding only excluded briefs stops
      the loop at the top with `queue_empty` and spawns nothing. Thread the list into the two
      pre-passes: `run_intake_triage_prepass()` (`:2321`) takes `exclude_repos` and passes
      repeated `--exclude-repo NAME` in the `evaluate` argv (`:2375`) and the list to its
      dry-run `inventory(within_days=25)` call (`:2363`); both `seed_backlog()` call sites
      (`:2534` and the `--seed-backlog --dry-run` branch at `:2561`) forward it.
      (d) In `tests/workqueue/test_seed_backlog.py`, cover: a needs-tasks spec in an excluded
      repo produces no finding and no brief while a non-excluded repo's spec still seeds; an
      epic under an excluded repo produces no epic-gap finding; `find_ready_specs` skips an
      excluded repo even when it has opted into seeded implementation; and the default empty
      list leaves every existing assertion's behavior untouched. In
      `tests/workqueue/test_queue_triage_inventory.py`, cover: an excluded repo's group is
      absent from the evaluation set while a non-excluded repo's group and the `__none__`
      group are unchanged, matching an absolute-path repo value by basename as well as a bare
      name. In `tests/workqueue/test_queue_triage.py`, cover the `evaluate` CLI accepting
      repeated `--exclude-repo` flags and threading them into `inventory()`. In
      `tests/drain/test_drain.py`, cover: each finder and `repo_sandbox_roots()` skips an
      excluded repo while returning every non-excluded repo's finding/roots; `go_repo` still
      composes with the list; a `--go-repo` naming an excluded repo leaves that repo in scope
      and logs the override; `main()` populates `DrainConfig.exclude_repos` from a
      `$WORKTRAIL_ROUTING_FILE` fixture and exits 2 on a malformed `drain:` section; the
      startup report names the list and any unmatched entry; the ready count ignores excluded
      briefs in all three repo shapes while still counting non-excluded ones; a loop whose
      only briefs are excluded stops `queue_empty` with the fake spawner recording zero
      calls; the intake-triage pre-pass argv carries one `--exclude-repo` per entry and its
      dry-run inventory call receives the list; and both seed-backlog call sites forward it.
      (Requirements: Drain repo sweeps skip excluded repos; The drain's seeding and triage pre-passes skip excluded repos; A brief for an excluded repo is not ready and is never automatically picked; An explicit repo scope overrides the exclusion list; The drain reports the applied exclusion list)
      depends: 1.1
      files: src/worktrail/drain/drain.py src/worktrail/workqueue/seed_backlog.py src/worktrail/workqueue/queue_triage.py tests/drain/test_drain.py tests/workqueue/test_seed_backlog.py tests/workqueue/test_queue_triage.py tests/workqueue/test_queue_triage_inventory.py

## 3. Document the exclusion list

- [x] 3.1 [docs] Update the operator-facing docs for the new key and its reach.
      In `docs/config/routing.yaml.example`, extend the `routing.drain:` block's example
      (`:237`-`:242`) with `exclude_repos` and prose covering: entries are repo directory
      names as `discover_repo_names()` reports them; the list is machine-wide only; it
      governs the drain's repo sweeps (including the codex sandbox's writable roots), the
      seed-backlog and intake-triage pre-passes, the drain's ready count, and automatic
      brief selection (skip reason `repo-excluded`); an explicit `--go-repo`/`--auto-repo`
      beats it; and an entry matching no repo is inert and reported. In
      `skills/worktrail-routing-config/SKILL.md`, update the `drain` row (`:53`) so it no
      longer says "currently just `max_workers`", describing `exclude_repos` and its reach
      into automatic selection. In `skills/worktrail-go/references/drain.md`, add a "Repo
      exclusion" section stating the key, what it keeps a repo out of, the explicit-scope
      override, and that unmatched entries are reported rather than fatal. In
      `skills/worktrail-go/references/auto-mode.md`'s "Skip reasons" list, add the
      `repo-excluded` entry beside `release-gate:<name>`, stating that an explicit
      `--auto-repo` for that repo still picks it.
      (Requirements: The machine-wide routing file declares repos excluded from unattended draining; A brief for an excluded repo is not ready and is never automatically picked; The drain reports the applied exclusion list)
      files: docs/config/routing.yaml.example skills/worktrail-routing-config/SKILL.md skills/worktrail-go/references/drain.md skills/worktrail-go/references/auto-mode.md

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, `python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .`, and
      `python3 scripts/ci/check_shebang_exec_bits.py`, then `openspec validate
      auto-drain-repo-exclusion --strict` and `worktrail-compile
      openspec/changes/auto-drain-repo-exclusion`. Also run the plugin-surface test
      explicitly (`PYTHONPATH=src pytest -q tests/test_plugin_surface.py`) since the docs
      task edits shipped skill text.
