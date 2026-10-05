## 1. One resolution decides whether a cell is capacity-gated

- [ ] 1.1 In `src/worktrail/orchestrator/agent_capacity.py`, hoist the drain's bare-target-aware
      gate resolution into this module as the single reader, then route the module's own readers
      through it. Add `entry_gated(state, now=None) -> bool`: the gated-entry predicate, moved
      verbatim from `drain._entry_gated` (`src/worktrail/drain/drain.py:484-500`) -- a `dict`
      whose `status` (lowercased) is in the module's existing `_GATED_STATUSES`
      (`unavailable`/`gated`/`blocked`) and whose `retry_after`/`reset_at` window (parsed with
      the existing `_parse_time`) is absent or still in the future. Add
      `gate_entry(query, *, data=None, path=None, now=None) -> tuple[str, dict] | None`: resolve
      the first entry that gates `query`, returning `(entry_key, state)`, mirroring
      `drain.capacity_gated`'s (`drain.py:503-543`) exact branch order -- for a `query`
      containing `:` check the bare `<target>` prefix entry first (a provider-wide gate outranks
      a per-model entry), then the exact `query` entry; for a bare `query` check the bare entry,
      then, when the query has no `:`, gate only if every `<query>:*` entry is gated. Read
      `data` when given, else `load(path)`, and accept both a full cache dict and a bare
      `providers` mapping, exactly as `capacity_gated` does. Rewrite `check()`
      (`agent_capacity.py:219-248`) to take `(entry_key, state) = gate_entry(provider_key(target,
      model), path=path, now=now)`; return when there is none, and keep the existing
      cooldown-probe branch (`_probeable`/`PROBE_INTERVAL_S`, `:233-247`) but keyed to
      `entry_key` -- the probe stamps `probe_at` on the entry that actually gated the cell, which
      may be the bare one -- raising `ProviderUnavailable(key, state)` as before. Rewrite
      `gate_snapshot()` (`:168-205`) to resolve each configured key through `gate_entry` so a
      bare target entry gates every `<target>:*` key the caller passes, keeping its
      `configured`/`gated`/`all_gated`/`retry_after` output shape and its `_safe_identifier`
      sanitization unchanged. Note in `gate_for_agent()`'s docstring that the check it performs
      is the same resolution, so a bare target-wide entry from the drain gates the one resolved
      agent. In `tests/orchestrator/test_agent_capacity.py` cover: `check()` raises for a
      `<target>:<model>` query against an active bare `<target>` entry and returns for an expired
      one; a per-model entry recorded `available` does not un-gate an active bare entry; a bare
      query is gated only when every `<target>:*` entry is active; a cooldown-derived bare gate
      is probed and stamped on the bare entry (not on a model-qualified key that does not exist);
      and `gate_snapshot` fed a `<target>:<model>` key set reports the cell gated from a bare
      entry, with `all_gated`/`retry_after` following. In
      `tests/orchestrator/test_check_agent_contract.py` cover the CLI end of the same
      resolution: an active bare target entry makes `check-agent` report the resolved target
      gated (exit 1) and an expired one report it ungated (exit 0). In
      `tests/router/test_dashboard.py` cover the capacity line reporting a bare-target gated cell
      as gated.
      (Requirement: Capacity gates key on target and model)
      files: src/worktrail/orchestrator/agent_capacity.py, tests/orchestrator/test_agent_capacity.py, tests/orchestrator/test_check_agent_contract.py, tests/router/test_dashboard.py

## 2. The drain reads through the shared resolution and stops counting a capacity block

- [ ] 2.1 In `src/worktrail/drain/drain.py`, replace the drain's private gate lookup with a
      delegation to the hoisted resolution, then exempt a capacity block from the failure
      counter. Delete `_entry_gated()` (`:484-500`) and reduce
      `capacity_gated(cache, agent, now=None)` (`:503-543`) to
      `agent_capacity.gate_entry(agent, data=cache, now=now) is not None`, keeping its
      signature, its `providers`-or-flat-dict tolerance, and its base-`str` handling so every
      existing caller and test is unaffected; keep the two contract sentences it documents that
      do not move with the implementation -- that a model-qualified query also honours a bare
      provider-wide entry, and that the bare gate wins over a contradicting per-model entry --
      as a pointer to `agent_capacity.gate_entry`, and update `record_capacity_gate()`'s
      docstring (`:747-764`) so its claim about the key selection queries names the shared
      resolution. In the loop (`:2780-2783`), keep
      `outcome.kind in ("failed", "blocked") and not decisions_filed` for every non-capacity
      outcome but exclude a capacity block: compute the block from the outcome state's existing
      `blocked_capacity_` prefix (the same test `:2723` already uses) and do not increment
      `state.consecutive_failures` for it -- `failed`, a record-level `blocked`
      (`blocked_external_dependency`, `blocked_security_or_safety`, a decision-less
      `blocked_product_decision`), the decision-filed exemption, and the success reset all stay
      exactly as they are. Update the module docstring's capacity paragraph (`:58-68`) and
      `classify_outcome()`'s `blocked` note (`:2169-2174`) so both state that a capacity block
      does not count toward the circuit breaker and stops the run through the capacity stop, with
      no claim of a counting backstop. In `tests/drain/test_drain.py` cover: `capacity_gated`
      still returning True/False across its existing cases after the delegation (the file's
      existing `test_capacity_gated_*` cases are the regression net -- add one case where the
      bare entry is the only entry and the query is model-qualified, and one where
      `capacity_gated` sees an entry written by `record_capacity_gate`); a run whose iterations
      are repeatedly capacity-blocked while a candidate stays selectable stops on its item
      ceiling rather than the failure breaker, with every iteration recorded `blocked` (patching
      candidate selection the way `test_drain_timeout_after_pr_does_not_trip_circuit_breaker`
      (`:1558-1576`) patches its spawner, so the gated stop cannot mask the counter); two
      capacity-blocked iterations that do gate every candidate still stop with `capacity_gated`;
      a three-candidate run whose first two iterations block still attempts the third and stops
      only once every candidate is gated, as `capacity_gated` rather than as `circuit_breaker`
      after the second block; and consecutive `blocked_external_dependency` iterations still trip
      the breaker at the configured threshold.
      (Requirements: A capacity-blocked iteration does not advance the failure breaker;
      Capacity gates key on target and model)
      depends: 1.1
      files: src/worktrail/drain/drain.py, tests/drain/test_drain.py

## 3. The exhausted-row diagnostic names the gate key

- [ ] 3.1 In `src/worktrail/runtime/selection.py`, change `NoExecutionTarget.__init__`'s
      quadruple-arity label (`:67-86`) so each attempted cell is named by its gate key
      `f"{target}:{model}"` verbatim, with the harness still named alongside it (for example
      `f"{target}:{model} [{harness}]"`), keeping the gate class and retry time appended in the
      existing `[class, retry at ...]` shape and leaving the legacy triple-arity message
      (`:91-94`) and the empty-attempts message (`:62-65`) untouched. Update the class
      docstring's description of the quadruple message so it names the gate key, and note on
      `select_cell()`'s step 5 (`:363-365`) that the key is the one
      `worktrail-agent-capacity status`/`clear` accepts. In `tests/runtime/test_selection.py`
      cover: an exhausted row raised with a `prefer`-ordered row names each attempted cell's
      `<target>:<model>` key verbatim and its harness, and the `assertIn`-style assertions the
      existing exhausted-row cases already make on target names and gate classes still hold.
      (Requirement: Capacity gates key on target and model)
      files: src/worktrail/runtime/selection.py, tests/runtime/test_selection.py

## 4. Document the capacity key for the routing-config operator

- [x] 4.1 In `skills/worktrail-routing-config/references/gotchas.md`, extend "A stale capacity
      gate can look like a routing bug" (`:113-121`) to state that an entry may be keyed
      `target:model` or bare `target`, that a bare entry gates every model of that target for
      every reader (so look for the bare key too before concluding a routing edit "didn't take",
      and note that a whole target can be skipped for an account-level reason the
      model-qualified key does not show), and that a model-qualified entry never weakens a bare
      one. Touch no other part of this reference and add no new command name.
      (Requirement: Capacity gates key on target and model)
      files: skills/worktrail-routing-config/references/gotchas.md

## 5. Document the capacity-cache command surface's keys

- [ ] 5.1 In `skills/worktrail-go/SKILL.md`'s capacity-cache command block (`:929-941`), state
      that the provider keys `status` prints are exactly the keys `clear` accepts -- including a
      bare target key written by the drain, which clears that target for every model -- and that
      the key a skipped or blocked cell is reported by (the attempt list, and the blocked note
      written when no headless worker launched) is that same string, so either can be pasted into
      `clear`. Touch no other part of the skill and add no new command name: every `worktrail-*`
      identifier named here already exists as a console script.
      (Requirement: Capacity gates key on target and model)
      files: skills/worktrail-go/SKILL.md

## 6. Verification

- [ ] 6.1 [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator/test_agent_capacity.py
      tests/orchestrator/test_check_agent_contract.py tests/router/test_dashboard.py
      tests/drain/test_drain.py tests/runtime/test_selection.py`, then `PYTHONPATH=src pytest
      -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, then
      `python3 scripts/ci/ruff_pinned.py check .`, `python3 scripts/ci/ruff_pinned.py format
      --check .` and `python3 scripts/ci/check_shebang_exec_bits.py`; confirm all pass. Probe by
      hand against a scratch cache passed with `--cache` / `WORKTRAIL_AGENT_CAPACITY_CACHE`:
      write a bare `claude-deepseek` entry with an active `billing` gate and confirm (a)
      `worktrail-agent-capacity check-agent` reports the target gated, (b) a `select_cell` walk
      over a row whose `claude-deepseek` cell declares `deepseek-flash[1m]` skips that cell, (c)
      an exhausted row's raised message names `claude-deepseek:deepseek-flash[1m]`, (d)
      `worktrail-agent-capacity clear claude-deepseek` removes exactly that key, and (e) with the
      entry's `retry_after` in the past every one of those readers treats the cell as available.
      Confirm the drain keeps its stop taxonomy: two `blocked_capacity_billing` iterations stop
      as `capacity_gated` with no `circuit_breaker` stop, a three-candidate run still reaches its
      third candidate before stopping, consecutive plain failures still stop as `circuit_breaker`,
      and a decision-less `blocked_product_decision` iteration still counts while a decision-filed
      one does not. No file changes are expected from this task.
      depends: 1.1, 2.1, 3.1, 4.1, 5.1
