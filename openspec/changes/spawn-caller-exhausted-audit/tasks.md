## 1. The shared fail-closed boundary

- [ ] 1.1 In `src/worktrail/orchestrator/spawnlib.py`: add
      `class SpawnExhausted(NoExecutionTarget)` (importing `NoExecutionTarget` from
      `worktrail.runtime.selection` the same way the module already reaches selection) carrying
      `failure_class` and a `context` string, and a module-level
      `raise_if_exhausted(result, *, context) -> SpawnResult` that raises it when
      `result.exhausted` is set and otherwise returns `result` unchanged (design D1, D2). Do not
      change `spawn_agent`/`spawn_claude_p` themselves -- they keep returning a `SpawnResult`, so
      every existing caller is unaffected until it opts in.
      In a new `tests/orchestrator/test_spawn_exhausted_raise.py`, assert: `raise_if_exhausted`
      on an exhausted result raises `SpawnExhausted` whose message names the context and whose
      `failure_class` matches the result's; the same result is caught by an
      `except NoExecutionTarget` handler; a non-exhausted result is returned unchanged (identity)
      and raises nothing; and a result object lacking the attribute entirely (a test double)
      is returned unchanged rather than raising `AttributeError`
      (Requirements: Every spawn call site handles or declares exemption from exhaustion).
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawn_exhausted_raise.py

## 2. Fix the three deciding callers

- [ ] 2.1 In `src/worktrail/conductor/compile.py`: wrap both compile spawns --
      `_default_spawn()`'s `spawnlib.spawn_agent(...)` (~line 444) and
      `_spawn_with_explicit_cell()`'s (~line 471) -- in `spawnlib.raise_if_exhausted(...,
      context="compile")` before `.text` is read, so no payload is ever extracted from a
      provider error stream. In `compile_run_plan()`, keep the existing catch-all (a failed
      compile must not fail the run) but special-case `SpawnExhausted` to a distinct
      capacity-naming `give_up()` note instead of the generic
      `compile failed (<type>: <msg>)` one, still uncached (design D3). In `main()`, catch
      `SpawnExhausted` around `compile_run_plan(...)` is not sufficient -- the plan degrades
      rather than raising -- so detect the capacity note on the returned plan, print
      `blocked_no_capacity: <detail>` on stderr and return 2, leaving every existing return-1
      path (scope gaps, collisions, uncovered requirements, `PlanShapeError`) untouched.
      In a new `tests/conductor/test_compile_spawn_exhausted.py`, assert: an exhausted compile
      spawn yields a baseline-source plan whose note names the capacity block and not
      "returned no JSON object"; `_extract_json` is never called with the provider text; nothing
      is written to the plan cache directory; `worktrail-compile`'s `main()` exits 2 with a
      `blocked_no_capacity:` stderr line for that run; a compile whose worker answers with an
      unusable payload still exits on its existing path with its existing note; and a successful
      compile is unchanged; depends on 1.1
      (Requirements: A capacity-blocked compile is reported as capacity, not as a bad answer).
      files: src/worktrail/conductor/compile.py, tests/conductor/test_compile_spawn_exhausted.py

- [ ] 2.2 In `src/worktrail/orchestrator/verify.py`: in `_make_live_spawn()`'s inner `spawn()`
      (~line 239), pass the result through `spawnlib.raise_if_exhausted(..., context="<role>
      group worker")` before returning `.text`. In `_spawn_group_worker()`, let `SpawnExhausted`
      propagate (documenting it beside the existing `subprocess.TimeoutExpired` handling, which
      stays a strike). In `ensure_mergeable()`'s resolve loop and `wait_and_fix_ci()`'s
      resolve/ci-fix loop, catch it: log the capacity block naming the group and failure class,
      break out of the strike loop without incrementing the strike counter, and return the
      existing "not mergeable" / "CI not fixed" outcome with a capacity-blocked reason rather
      than a worker-attributed quarantine reason (design D4).
      In a new `tests/orchestrator/test_verify_group_worker_exhausted.py`, assert: an exhausted
      resolve spawn spawns exactly one worker (not `max_strikes`), leaves the strike count
      unchanged, records a capacity-blocked reason rather than a quarantine reason blaming a
      worker, and makes no `git push` or `gh pr` mutation on the group branch; the same for the
      ci-fix loop; a worker that returns unparseable text while NOT exhausted still consumes a
      strike exactly as today; and a successful worker still returns True; depends on 1.1
      (Requirements: A capacity block costs no group-worker strike).
      files: src/worktrail/orchestrator/verify.py, tests/orchestrator/test_verify_group_worker_exhausted.py

- [ ] 2.3 In `src/worktrail/orchestrator/live.py`: at the end of `LiveSpawn.__call__` (~line
      2986), after the existing `served_harness` label correction and before `return result`,
      pass the result through `spawnlib.raise_if_exhausted(result, context=f"{role} worker
      {task_id}")` so an exhausted spawn raises instead of handing the provider's error stream
      to the drive loop (design D5). Cover all four spawn branches (`:2954`, `:2958`, `:2967`,
      `:2976`) by placing the check on the single shared return path, not per branch. Leave
      `_research_preload`'s spawn (~line 2635) and `smoke()` (~line 6482) unchanged -- they read
      only `session_id` and report their own unexpected output respectively (design D6).
      In a new `tests/orchestrator/test_live_spawn_exhausted.py`, assert: an exhausted worker
      spawn makes `LiveSpawn.__call__` raise `SpawnExhausted`; `dispatch.parse_report_back` is
      never called with the provider text; the exception is caught by an
      `except NoExecutionTarget` handler (the shape `_safe_drive`'s classifier relies on); the
      `served_harness` label correction has already been applied when it raises; and a
      non-exhausted spawn returns the result unchanged for all four branches; depends on 1.1
      (Requirements: A capacity-blocked task worker raises instead of reporting back).
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_live_spawn_exhausted.py

## 3. Keep the audit closed

- [ ] 3.1 Add `tests/orchestrator/test_spawn_exhausted_callers.py`: an AST walk over every
      `.py` file under `src/worktrail/` collecting every call to `spawn_agent` or
      `spawn_claude_p`, asserting each one's enclosing function either references `exhausted` or
      calls `raise_if_exhausted`, or that the `<module>:<qualname>` site appears in an in-test
      `EXEMPT` mapping whose values are non-empty rationale strings; the failure message names
      the offending file and line. Seed `EXEMPT` with exactly the two sites justified in design
      D6 (the research pre-load, which consumes only `session_id`; `smoke()`, which reports its
      own unexpected output) and assert that every `EXEMPT` key still resolves to a real call
      site, so a removed or renamed caller cannot leave a stale exemption behind. Depends on
      2.1, 2.2, and 2.3
      (Requirements: Every spawn call site handles or declares exemption from exhaustion).
      files: tests/orchestrator/test_spawn_exhausted_callers.py

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, and confirm both
      repository gates pass; depends on 3.1. Verification-only, no file changes expected.
