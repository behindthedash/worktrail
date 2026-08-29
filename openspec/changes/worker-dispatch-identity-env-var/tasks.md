## 1. spawnlib: env plumbing

- [x] 1.1 Add an optional `dispatch_id: Optional[str] = None` parameter to
      `spawnlib.spawn_agent()`.
- [x] 1.2 In `spawn_agent()`'s nested `build_child_env()`, when `dispatch_id` is not
      `None`, set `env["WORKTRAIL_DISPATCH_ID"] = dispatch_id` on the built child
      env dict (same conditional-passthrough shape as the existing
      `WORKTRAIL_SKILL_DISPATCH_DEPTH` handling immediately below it). Leave the key
      absent when `dispatch_id` is `None`. (Requirement: Worker environment carries
      the run's dispatch identity)
- [x] 1.3 Add a matching optional `dispatch_id: Optional[str] = None` parameter to
      `spawnlib.spawn_claude_p()` and pass it straight through to its `spawn_agent()`
      call.

## 2. live.py: LiveSpawn + full-real plumbing

- [x] 2.1 Add an optional `dispatch_id: str | None = None` constructor parameter to
      `LiveSpawn.__init__`, stored as `self.dispatch_id` (mirrors how `effort` is
      stored).
- [ ] 2.2 Thread `self.dispatch_id` into the `spawnlib.spawn_agent`/
      `spawnlib.spawn_claude_p` call(s) inside `LiveSpawn.__call__`.
- [ ] 2.3 Add a `--dispatch-id` argument (default `None`) to the `full-real`
      argparse subparser (`fr = sub.add_parser("full-real", ...)`).
- [ ] 2.4 Add a `dispatch_id: str | None = None` parameter to `full_real()`, passed
      through to the `LiveSpawn` it constructs, and pass `args.dispatch_id` to it
      from `main()`'s `full-real` dispatch branch.

## 3. Tests

- [x] 3.1 In `tests/orchestrator/test_spawnlib.py`, add a test that calling
      `spawn_agent(..., dispatch_id="go-abc123")` (with the subprocess launch mocked,
      following this file's existing pattern for asserting on the child `env=`
      passed to the process launcher) results in `WORKTRAIL_DISPATCH_ID=go-abc123` in
      that env. (Requirement: Worker environment carries the run's dispatch identity)
- [x] 3.2 In the same file, add a test that omitting `dispatch_id` (or passing
      `None`) results in `WORKTRAIL_DISPATCH_ID` being absent from the child env.
      (Requirement: No dispatch identity is invented when none is supplied)
- [x] 3.3 In `tests/orchestrator/test_live_extras.py` (or wherever `LiveSpawn`
      construction is already covered), add a test that constructing `LiveSpawn`
      with `dispatch_id="go-abc123"` and invoking `__call__` reaches `spawn_agent`
      with `dispatch_id="go-abc123"`.
- [ ] 3.4 Add a test covering `full-real`'s `--dispatch-id` argparse wiring: passing
      `--dispatch-id go-abc123` results in `full_real()` being called with
      `dispatch_id="go-abc123"` (mock `full_real` the way this module's existing
      CLI-dispatch tests already do for other `full-real` flags).

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm the full suite is green.
- [ ] 4.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
      (golden record/replay regression) and confirm it is unaffected.
