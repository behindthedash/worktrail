## 1. One validated preference resolution shared by both sites

- [ ] 1.1 In `src/worktrail/runtime/selection.py`, add the shared validated preference
      resolution and the harness-hint adapter. Add `class UndeclaredPrefer(SelectionError)`
      whose docstring says it is raised when a `prefer` names no declared `routing.targets`
      entry (a typo, a renamed or dropped target, or a bare harness name where `prefer` takes
      a target name). Add `resolve_prefer(routing, prefer) -> str | None`: `None` passes
      through; a `prefer` that is a key of `routing["targets"]` is returned unchanged; any
      other value raises `UndeclaredPrefer` naming the offending value and the declared target
      names (`"none"` when the table is empty). Add `first_target_for_harness(routing,
      harness) -> str | None` returning the first declared target in file order whose
      `harness` matches, else `None` -- the documented adapter for a caller holding a bare
      harness name, which is a name `prefer` can never match. In `select_cell`, resolve
      `prefer = resolve_prefer(routing, prefer)` before building the order, so the existing
      promotion guard only ever sees a declared target name; leave the row-absent behavior
      byte-for-byte as is (a declared target with no cell in the row is dropped, never
      promoted -- the pinned soft no-op). Update `select_cell`'s docstring step 1 and its
      raise list, and note on `resolve_prefer` and `first_target_for_harness` that a
      harness-level hint is resolved with `first_target_for_harness`, never passed as
      `prefer`. In `src/worktrail/orchestrator/spawnlib.py`, resolve `prefer` through
      `runtime.selection.resolve_prefer` at the top of `_preflight_primary_target()` (import it
      beside `select_cell`) so the preflight can neither silently drop an undeclared
      preference nor disagree with the cell `select_cell` serves; keep the rest of that
      function unchanged. Add `prefer_target_for_harness(harness: str) -> str | None`, which
      resolves routing fresh (`resolve_routing(load_policy(worktrail_home()))`, the same
      per-call contract as the model-default lookups) and returns
      `runtime.selection.first_target_for_harness(routing, harness)`: the first declared target
      for that harness, or `None` when the routing declares none, so a caller's best-effort
      harness hint degrades to the row's own order instead of raising. Update `spawn_agent`'s
      docstring to state that `prefer` must name a declared `routing.targets` entry and that an
      undeclared name raises `runtime.selection.UndeclaredPrefer` before any subprocess starts.
      (Requirements: A single selector walks a tier row across targets in preference order;
      A harness-level preference resolves to a declared target before selection;
      Primary-cell preflight shares the selector's preference resolution)
      In `tests/runtime/test_selection.py`, extend `TestPreferReorder` (keep the three
      existing cases, in particular `test_prefer_naming_target_without_cell_in_row_is_a_no_op`
      unchanged): an undeclared `prefer` raises `UndeclaredPrefer` whose message names the
      offending value and the declared targets; a bare harness name (`"claude"` against
      `claude-sub`/`codex-sub` targets) raises instead of silently serving the row order; and
      `prefer=None` is unaffected. Add coverage for `first_target_for_harness`: the first
      declared target in file order when two targets share a harness, and `None` for an
      undeclared harness. In `tests/orchestrator/test_spawnlib.py`, add:
      `spawn_agent(..., prefer=<undeclared>)` raises `UndeclaredPrefer` with no subprocess run
      (patch `subprocess.run` to fail loudly if called); `prefer_target_for_harness` maps a
      declared harness to its first target and returns `None` for a harness no target declares
      (point routing via the file's existing routing-patch fixture); and a `prefer` naming a
      declared target absent from the tier row still spawns on the row's order without raising
      (the soft no-op through the spawn path).
      files: src/worktrail/runtime/selection.py, src/worktrail/orchestrator/spawnlib.py, tests/runtime/test_selection.py, tests/orchestrator/test_spawnlib.py

## 2. Harness-hint callers resolve through the adapter

- [ ] 2.1 Replace the bare-harness `prefer=` arguments (which the new resolution raises on)
      with the resolved target. In `src/worktrail/orchestrator/verify.py`'s
      `_make_live_spawn`'s inner `spawn`, pass `prefer=spawnlib.prefer_target_for_harness(agent)`
      and update the docstring that calls `agent` a soft `prefer` hint to say it is resolved to
      the first declared target for that harness (no preference when the routing declares
      none). In `src/worktrail/workqueue/queue_triage.py`, do the same at both evaluator spawn
      sites (the evaluate spawn and `_apply_propose_change`'s spawn). In
      `src/worktrail/learning/retro.py`, resolve the `prefer="claude"` literals at both sites
      (`_gate`'s `select` call and `_curate`'s `spawn_agent` call) through
      `spawnlib.prefer_target_for_harness("claude")`, so retro's documented semantics hold:
      with a declared claude target the preference is honored; with none it is `None` and the
      existing `claude_harness_unavailable` skip still governs.
      (Requirements: A harness-level preference resolves to a declared target before selection;
      Retro Runs As A Memory-Enabled Claude Agent Outside Any Repository)
      In `tests/learning/test_retro.py`, capture the `prefer` kwarg the injected `select` and
      `spawn` receive: with a routing file declaring `claude-sub` (in `REVIEW_DEFAULT_TIER`)
      it is `claude-sub` at both call sites; with no claude target declared it is `None` and
      the run still skips with `claude_harness_unavailable` rather than failing. In
      `tests/workqueue/test_queue_triage.py` and `tests/orchestrator/test_verify.py`, pin that
      a harness-agent call resolves through `prefer_target_for_harness` (patch
      `spawnlib.spawn_agent` and assert the captured `prefer` is the declared target, or
      `None` when undeclared).
      files: src/worktrail/orchestrator/verify.py, src/worktrail/workqueue/queue_triage.py, src/worktrail/learning/retro.py, tests/orchestrator/test_verify.py, tests/workqueue/test_queue_triage.py, tests/learning/test_retro.py
      depends: 1.1

## 3. Operator-facing routing-config docs

- [ ] 3.1 In `skills/worktrail-routing-config/SKILL.md` and its
      `skills/worktrail-routing-config/references/gotchas.md`, state the validated `prefer`
      contract: `prefer` must name a declared `routing.targets` entry -- a typo, a renamed or
      dropped target, or a bare harness name fails the spawn loudly (`UndeclaredPrefer`) with
      the declared targets listed, instead of being silently ignored; a declared target with
      no cell in the chosen tier row is still the documented soft no-op (the whole row in file
      order remains the fallback chain); and a harness-level best-effort preference is
      expressed by resolving the harness to its first declared target (spawn callers use
      `prefer_target_for_harness`) rather than by passing the harness name. Update the
      `targets`/`roles` table rows and the two existing `prefer` gotchas to match.
      (Requirements: A single selector walks a tier row across targets in preference order)
      files: skills/worktrail-routing-config/SKILL.md, skills/worktrail-routing-config/references/gotchas.md

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q tests/runtime/test_selection.py
      tests/orchestrator/test_spawnlib.py tests/orchestrator/test_verify.py
      tests/workqueue/test_queue_triage.py tests/learning/test_retro.py`, then `PYTHONPATH=src
      pytest -q`, `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`,
      `python3 scripts/ci/ruff_pinned.py check .`, `python3 scripts/ci/ruff_pinned.py format
      --check .`, and `python3 scripts/ci/check_shebang_exec_bits.py`. Probe the raise by hand:
      `select_cell` with `prefer="claude"` against a routing whose targets are
      `claude-sub`/`codex-sub` raises `UndeclaredPrefer` naming the value and the declared
      targets, and the same value through `spawn_agent` starts no subprocess. Confirm the soft
      paths still hold: a declared target absent from the row serves the row order without
      raising, and `prefer_target_for_harness` returns `None` on a routing with no claude
      target. Run `openspec validate unmatched-prefer-drop-diagnostic --strict` and
      `worktrail-compile openspec/changes/unmatched-prefer-drop-diagnostic`.
      depends: 1.1, 2.1, 3.1
