## 1. Policy gate

- [x] 1.1 Add `"allow_seeded_implementation": False` to `policy.py`'s `DEFAULTS`, with a comment
      explaining the opt-in/default-off contract and pointing at this capability.
- [x] 1.2 In `load_policy()`, clamp a non-bool `allow_seeded_implementation` value back to
      `False` with a `_meta.warnings` entry, mirroring the existing `automerge.enabled`
      bool-forcing block.
      Implements "Route D implementation seeding is opt-in per repo".

## 2. Ready-to-implement finder

- [x] 2.1 Add `find_ready_specs(repos_root, go_repo=None)` to `seed_backlog.py`: for each repo
      whose `load_policy()` result has `allow_seeded_implementation` truthy, scan via
      `dashboard.scan(repo_path / "docs" / "specs")` (same call `find_needs_tasks_specs` uses)
      and collect every row with `stage == "ready-to-implement"`, sorted by repo then id. Each
      finding carries `kind: "ready-to-implement"`, `repo`, `repo_name`, `id`, `spec_rel`
      (via `resolve_spec_rel`), and `seed_key: f"{name}:impl:{spec_id}"`.
      Implements "Route D implementation seeding is opt-in per repo".
      Implements "Ready-to-implement specs are seeded as Route D implementation briefs".
- [x] 2.2 Add `_ready_brief_kwargs(finding)`: returns `focus`/`context` text directing the
      picking session to run the orchestrator against the spec's existing, complete task DAG
      (do not re-plan), plus `recommended_route="D"`, `implementation_intent="requested"`,
      `target_spec=spec_id` — mirroring `_needs_tasks_brief_kwargs`'s shape.
      Implements "Ready-to-implement specs are seeded as Route D implementation briefs".

## 3. Wire into the seeder

- [x] 3.1 In `seed_backlog()`, call `find_ready_specs(repos_root, go_repo)` and append its
      results to `candidates` after the epic findings (order: needs-tasks, epics,
      ready-to-implement), and extend the `kwargs = (... if finding["kind"] == "needs-tasks"
      else ...)` dispatch with a third branch calling `_ready_brief_kwargs` for
      `finding["kind"] == "ready-to-implement"`.
      Implements "Seeding is bounded, deterministic, and loudly capped" (MODIFIED).
      Implements "Route D seed keys are deduplicated against the whole queue, never re-armed".
- [x] 3.2 [e2e] Confirm (no code change expected) that `existing_seed_keys()` and the shared
      `max_seeds`/cap/deferred-logging path in `seed_backlog()` already apply uniformly across
      all three candidate kinds, and that `worktrail-drain`'s `seeded_backlog` summary key
      surfaces the new finder's entries via the existing merged `seeded` list — no drain code
      changes needed.

## 4. Tests

- [x] 4.1 `tests/router/test_policy.py`: `allow_seeded_implementation` defaults to `False` when
      unset, is `True` when the policy file sets it, and a non-bool value is clamped to `False`
      with a warning.
- [x] 4.2 `tests/workqueue/test_seed_backlog.py`: `find_ready_specs` returns nothing for a repo
      without `allow_seeded_implementation: true` even when a `ready-to-implement` spec exists;
      returns the expected finding shape (seed key, route, implementation-intent, target-spec)
      for an opted-in repo; excludes `stale-bookkeeping` and `orchestrator-stuck` stages;
      dedups against an existing `<repo>:impl:<spec-id>` key in queue/ or picked/ (any status,
      no re-arm); combined cap/order/deferred-logging behavior across all three kinds
      (needs-tasks, epic, ready-to-implement); dry-run and `--repo` restriction cover the new
      finder the same as the existing two.
- [x] 4.3 `tests/drain/test_drain.py`: a drain pass against an opted-in repo with a
      `ready-to-implement` spec seeds a Route D brief and it appears in the run summary's
      `seeded_backlog.seeded`, without any drain.py code changes (regression guard for task 3.2's
      "no drain changes needed" claim).
