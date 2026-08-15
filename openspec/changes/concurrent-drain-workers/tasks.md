## 1. Worker-slot locking (design.md D1, Requirement: Bounded worker-slot locking)

- [x] 1.1 In `src/worktrail/drain/drain.py`, add `slot_lock_path(lock_file: Path, slot: int) ->
      Path` (slot 0 returns `lock_file` unchanged; slot N>0 returns
      `lock_file.with_name(f"{lock_file.name}.{slot}")`) and `acquire_lock_slot(lock_file: Path,
      max_workers: int) -> Optional[int]`, which tries slots `0..max_workers-1` in order,
      calling the existing unmodified `acquire_lock()` (`drain.py:1188-1210`) against each
      candidate path, returning the first successfully acquired slot index or `None` if every
      slot is held by a live PID.
- [x] 1.2 Add `release_lock_slot(lock_file: Path, slot: int) -> None` calling the existing
      `release_lock()` (`drain.py:1223-1224`) against `slot_lock_path(lock_file, slot)`.
- [x] 1.3 Add `max_workers: int = 1` to `DrainConfig` (`drain.py:1341-1360`, default preserves
      today's single-lock behavior for any existing caller that constructs `DrainConfig`
      directly, e.g. tests).
- [x] 1.4 In `drain()` (`drain.py:1390`), replace the `acquire_lock(config.lock_file)` call and
      its `lock_held` early return with `acquire_lock_slot(config.lock_file,
      config.max_workers)`; store the returned slot on `LoopState` (or a new local) for D4/D6 to
      consume; release via `release_lock_slot` in the existing `finally:` block
      (`drain.py:1564-1565`).
- [ ] 1.5 Add `--max-workers` to the CLI parser (`drain.py:main`, near the existing
      `--consecutive-failures`/`--iteration-timeout-minutes` block), resolved per D3/section 6
      below rather than a bare `default=1`.
- [ ] 1.6 Tests in `tests/drain/test_drain.py`: two sequential `acquire_lock_slot` calls with
      `max_workers=2` against the same `lock_file` return distinct slots (0 then 1) without
      releasing between calls; a third call with the same args returns `None`; a single call
      with `max_workers=1` (or omitted) acquires exactly `lock_file` unchanged (assert the slot
      lock's own path equals the configured `--lock-file`, matching spec
      `drain-concurrent-workers` scenario "Single invocation, default max-workers"); a stale
      slot (dead PID written to a slot-1 lock file) is taken over exactly like today's
      single-lock stale-takeover test already covers for slot 0.

## 2. Capacity-cache write-lock fix (design.md D2, Requirement: Capacity-cache writes are safe under concurrent workers)

- [x] 2.1 In `src/worktrail/orchestrator/agent_capacity.py`, rename `_write_lock` to
      `write_lock` (drop the leading underscore; no signature or behavior change) and update its
      three existing internal callers (`configure`, `record`, `cmd_clear`,
      `agent_capacity.py:118-146` and call sites) to the new public name.
- [ ] 2.2 In `src/worktrail/drain/drain.py`, wrap `record_capacity_gate()`'s
      `agent_capacity.load(cache_path)` → mutate → `agent_capacity.save(data, cache_path)`
      sequence (`drain.py:437-454`) in `agent_capacity.write_lock(cache_path)`.
- [ ] 2.3 Test in `tests/orchestrator/test_agent_capacity.py`: two threads (or two sequential
      calls with an injected delay between `load()` and `save()` on one of them, matching this
      test file's existing lock-contention test style if present) call `record_capacity_gate`
      for different provider keys against the same cache path; both entries are present in the
      final file — neither is lost. If a concurrency test already exists for `record()`/
      `configure()` in this file, mirror its pattern for `record_capacity_gate` rather than
      inventing a new harness.

## 3. Run-record attribution scoping (design.md D5, Requirement: Run-record attribution is scoped per worker)

- [ ] 3.1 In `src/worktrail/drain/drain.py`, extend `newest_run_record()`
      (`drain.py:305-325`) with an optional `repo_filter: Optional[str] = None` parameter: when
      given, restrict the glob to `runs_dir.glob(f"{repo_filter}/*.yaml")` instead of
      `runs_dir.glob("*/*.yaml")`; behavior with `repo_filter=None` is unchanged.
- [ ] 3.2 In `drain()`'s main loop (`drain.py:1438` onward), when
      `claimed_briefs` (from `claimed_brief_ids`, `drain.py:256-267`) has exactly one entry,
      look up that brief's `repo` field from the pre-iteration `queue` snapshot already captured
      (`list_queue`'s JSON, matching `work_queue.py:458`'s `"repo"` key) and pass it as
      `newest_run_record`'s `repo_filter`; otherwise call `newest_run_record` exactly as today
      (no filter).
- [ ] 3.3 Tests in `tests/drain/test_drain.py`: `newest_run_record` with `repo_filter` set
      ignores a newer file in a different repo's subdirectory and returns the newest file within
      the filtered repo's subdirectory; with `repo_filter=None` behavior is byte-for-byte
      unchanged from the existing test coverage. Add a `drain()`-level test simulating two
      overlapping iterations (two repos' run directories both gain a new record between one
      iteration's pre-spawn snapshot and its post-spawn read) asserting the iteration's outcome
      is classified from its own claimed brief's repo, not the other repo's newer record —
      covers spec `drain-concurrent-workers` scenario "Two workers finish overlapping iterations
      against different repos."

## 4. Per-worker cwd isolation (design.md D4, Requirement: Per-worker working-directory isolation)

- [ ] 4.1 In `src/worktrail/drain/drain.py`, add `worker_scratch_dir(slot: int, home: Optional[Path]
      = None) -> Path` returning `(home or worktrail_home()) / "drain-workers" /
      f"worker-{slot}"`.
- [ ] 4.2 Add `cwd: Optional[Path] = None` to `run_one_shot()`'s signature (`drain.py:1376-1387`)
      and pass it through to `subprocess.run(..., cwd=str(cwd) if cwd else None, ...)`.
- [ ] 4.3 In `drain()`, after acquiring a slot (task 1.4), call `worker_scratch_dir(slot).mkdir
      (parents=True, exist_ok=True)` once, and pass that path as `cwd` on every `spawner(cmd,
      config.iteration_timeout)` call in the main loop (`drain.py:1473`) — via
      `functools.partial` on the built-in spawner (mirroring the existing
      `functools.partial(run_one_shot, env=agent_env)` at `drain.py:1398`) so injected test
      spawners are unaffected.
- [ ] 4.4 Test in `tests/drain/test_drain.py`: `drain()` with `max_workers=2` and an injected
      spawner records the `cwd` each call received (via a fake spawner capturing kwargs) and
      asserts two concurrently-configured slots resolve to distinct `worker_scratch_dir` paths.

## 5. Leader-only remediation sweep and backlog seeding (design.md D6, Requirement: Repo-wide sweeps run once per drain pass, not once per worker)

- [ ] 5.1 In `drain()` (`drain.py:1420-1435` pre-loop, `drain.py:1554-1563` post-loop), guard
      both the `sweep_remediations(...)` calls and the `seed_backlog_mod.seed_backlog(...)` call
      with `slot == 0` (the value returned by `acquire_lock_slot` in task 1.4), in addition to
      the existing `config.repos_root is not None and not config.dry_run` guards. Workers on any
      other slot skip both blocks entirely and proceed straight to the queue-check loop.
- [ ] 5.2 Test in `tests/drain/test_drain.py`: `drain()` invoked with a slot other than 0 (inject
      or stub `acquire_lock_slot` to return `1`) never calls the `sweep_remediations`/
      `seed_backlog` fakes even when `--repos-root` is set and the queue has ready briefs; a
      slot-0 invocation calls them exactly as today's existing pre/post-sweep tests already
      cover.

## 6. `drain.max_workers` operator-config wiring (design.md D3, spec `drain-operator-config`, Requirement: Drain worker count resolves CLI over config over built-in)

- [x] 6.1 In `src/worktrail/shared/operator_config.py`, extend `drain_config()`'s returned dict
      with a `max_workers` key: read `section.get("max_workers")`, default `2` when absent,
      raise `OperatorConfigError` naming `config_path()` when present but not a positive int
      (mirrors the existing `agent`/`fallback_agents` shape checks in the same function,
      `operator_config.py:62-79`).
- [ ] 6.2 In `drain.py:main()`, resolve `max_workers` as: `args.max_workers` if passed, else
      `operator_drain["max_workers"]`, else `2` — same precedence chain already used for `agent`/
      `fallback_agents` a few lines above (`drain.py:1670-1677`). Reject a resolved value that
      isn't a positive int the same way the existing invalid-agent branch does (exit 2, error
      naming the config path).
- [ ] 6.3 Update `docstring`/`--help` text for `--max-workers` in `drain.py:main()` to state the
      CLI > config > built-in precedence, matching the existing `--fallback-agent` help text
      style.
- [ ] 6.4 Tests in `tests/shared/test_operator_config.py`: `drain_config()` returns
      `max_workers: 2` when the config has no `drain.max_workers` key; returns the configured
      int when present and valid; raises `OperatorConfigError` naming the config path when
      `drain.max_workers` is present but not a positive integer (0, negative, or non-int).
      Tests in `tests/drain/test_drain.py` (or wherever the existing agent-precedence CLI tests
      for `main()` live): `--max-workers` flag overrides config; config value is used when the
      flag is omitted; built-in default of 2 applies with neither present — covers spec
      `drain-operator-config`'s four scenarios directly.

## 7. Verification

- [ ] 7.1 [cleanup] Update `drain.py`'s module docstring (the `Usage:` block,
      `drain.py:79-88`) to include `--max-workers` alongside the existing documented flags.
- [ ] 7.2 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm it is green, including every new
      test from sections 1-6. Verification-only — no production file changes expected.
- [ ] 7.3 [cleanup] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
      (golden record/replay regression) and confirm it is green. Verification-only — no file
      changes expected.
