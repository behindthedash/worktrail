## 1. No-op spawn detection in the spawn layer

- [x] 1.1 Detect the zero-API-call result shape and classify it as an infra
      failure, mapping its exhausted gate to a short-cooldown class. In
      `src/worktrail/orchestrator/spawnlib.py`:
      - retain `duration_api_ms` from the claude `result` event in
        `_parse_stream_json` (a diagnostic field alongside `subtype`/`is_error`/
        `stop_reason`/`num_turns`) and add it to the module docstring's list of
        lifted fields;
      - add `_zero_api_call_result(usage)` next to `is_infra_failure` — True only
        when `duration_api_ms` is present and 0, `num_turns` is 0, and all four
        token counts are 0 — and extend `is_infra_failure` to return True for it
        after the existing opencode-error clause, so the existing retry-then-hop
        path runs before any capacity gate or success is recorded;
      - map an exhausted zero-API-call shape to the short-cooldown `startup` class
        in `spawn_agent`'s exhausted-budget class resolution, ahead of
        `classify_failure`'s text fallthrough, with a comment naming why `auth`
        and `model_unavailable` are barred (24 h cooldowns; auth gates without
        retry; model_unavailable is never probed) and why the fallthrough
        (`transport`) is not the chosen label.
      Tests in `tests/orchestrator/test_spawnlib.py`, using the module's existing
      `_routing()`/`_patch_routing`/`FakeRun`/capacity-cache isolation fixtures:
      the parsed usage dict retains `duration_api_ms` while an opencode-synthesized
      usage dict still carries no such key; the synthesized no-op result event
      (`duration_api_ms: 0`, `num_turns: 0`, all-zero usage, `subtype: success`,
      `stop_reason: stop_sequence`, `is_error` false) makes `is_infra_failure(0,
      ...)` True; a real completed turn (non-zero `duration_api_ms`, `num_turns >=
      1`, non-zero tokens) stays False; a `num_turns: 1` zero-token turn stays
      False; an opencode permission-denial stream and plain text stay False; a
      non-final no-op attempt is retried and never records `available`; a no-op
      stream exhausting cell A's budget with cell B healthy completes on B and
      records A's gate `failure_class: startup`; a row whose only cell no-ops
      returns `exhausted=True` with `failure_class: startup` rather than a
      successful empty run. The no-op fixture's docstring SHALL state it is
      synthesized to the documented measured shape — no live recording of a real
      no-op run exists on this machine. Confirm the no-op cases fail against the
      pre-change code and pass after.
      (Requirement: A zero-API-call result is classified as an infra failure, not a completed run)
      (Requirement: An exhausted zero-API-call spawn gates its cell with a short-cooldown class)
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py

## 2. Repair the live.py spawn-layer defects

- [x] 2.1 Repair `live.py`'s spawn layer: the two call sites that pass kwargs
      `spawn_agent` does not accept, and the worker-model resolution that blocks
      `precheck` on a routing table with no target for the invocation host.
      In `src/worktrail/orchestrator/live.py`:
      - add module-level `_first_target_for_harness(routing, harness)` (the first
        declared `routing.targets` entry whose harness matches — the target-name
        form `select_cell()`'s `prefer` takes) and `_default_tier_and_prefer(
        agent)` (the routing file's `default_tier` row plus that lookup, with the
        routing resolved fresh per call, matching `_default_model_for_agent()`'s
        contract); replace `LiveSpawn.__call__`'s local `_target_for_harness`
        closure with the shared helper, keeping the resolve-routing-once-per-
        instance behavior and the existing LiveSpawn tier-routing tests green;
      - rewrite `run_research_session`'s spawn as `spawn_agent(prompt,
        spec_folder.parent.parent, tier=tier, prefer=prefer, timeout=timeout,
        extra_args=extra_args, log=print)` and `smoke`'s as `spawn_agent("Reply
        with exactly: PONG", Path.cwd(), tier=tier, prefer=prefer, timeout=120,
        retries=0)`, dropping the `agent=`/`model=`/`effort=` kwargs; leave each
        `model = model or _default_model_for_agent(agent)` line in place (its
        fail-fast value is preserved) and comment that `model`/`effort` are now
        compatibility-only, matching `LiveSpawn.__init__`'s documented contract
        for its own `model`;
      - in `main()`, add a module-level constant naming the subcommands whose
        tail-resolved values are consumed downstream (`smoke`, `live-run`, `full`,
        `live-run-real`, `full-real`) and guard both the `args.model =
        _default_model_for_agent(...)` line and the `role_models =
        _effective_role_models(...)` line behind membership, with a comment
        recording the reproduced failure this fixes (`worktrail-live precheck`
        raising `OperatorConfigError: no default model configured for agent
        'claude'` before its DAG check on a routing table with no claude target;
        the role-model line crashes the same way with a codex host). Keep the
        resolution intact for every subcommand that does spawn.
      Tests in `tests/orchestrator/test_live_extras.py`: end-to-end tests that
      drive the REAL `spawn_agent` through both repaired call sites — patching
      only `spawnlib.resolve_routing` and `spawnlib.subprocess.run` plus the
      capacity-cache env isolation, the hermetic pattern `test_spawnlib.py` uses
      — asserting `run_research_session` returns the scripted stream's session id
      and `smoke()` returns True on a PONG stream resolved from the `default_tier`
      row for the requested harness. Both must fail with `TypeError` against the
      pre-change code (a MagicMock patch cannot catch this class). Tests in
      `tests/orchestrator/test_precheck.py`, reusing `_make_spec_dir`: with a temp
      codex-only routing file via `WORKTRAIL_ROUTING_FILE` and `live.DEFAULT_AGENT`
      patched to `"claude"`, `live.main(["precheck", "--repo", <tmp>,
      "specs/001-test"])` returns its normal exit code (pre-change: raises
      `OperatorConfigError`); contrast on the same table: `live.main(["smoke",
      ...])` still raises, pinning that spawning subcommands keep the resolution.
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_live_extras.py, tests/orchestrator/test_precheck.py

## 3. Verification

- [ ] 3.1 [e2e] Run the full suite (`PYTHONPATH=src pytest -q`), the golden
      regression (`PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate
      check`), and the repo lint gates (`python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .`,
      `python3 scripts/ci/check_shebang_exec_bits.py`). Run `openspec validate
      model-tier-routing-zero-usage-spawn-detection --strict` and `worktrail-compile
      openspec/changes/model-tier-routing-zero-usage-spawn-detection`, confirming the
      change is valid and its task plan has no file-scope or requirement-coverage
      violations. Replay the three reproductions from design.md against the implemented
      tree and confirm each flipped: `is_infra_failure(0, <no-op event>)` is True; the
      `run_research_session` and `smoke` call shapes reach the real `spawn_agent` (no
      TypeError); `worktrail-live precheck` runs green on a routing table with no
      host-harness target. Confirm no `agent_capacity.DEFAULT_COOLDOWNS` value changed
      and no routing cassette / route-classification file is touched. Verification-only,
      no file changes expected.
      depends: 1.1, 2.1
