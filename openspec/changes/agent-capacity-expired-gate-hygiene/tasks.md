## 1. Capacity-cache operator surface

- [x] 1.1 In `src/worktrail/orchestrator/agent_capacity.py`: in `cmd_status()`, replace the
      `active` suffix computation so a gated status (`unavailable`/`gated`/`blocked`) with
      `retry_at > now` prints `  (active)` and one with `retry_at <= now` prints `  (expired)`;
      a gated status with no timestamp prints neither. Add a module-level helper (e.g.
      `_expired_keys(providers, now)`) that returns the sorted keys `status` would label
      expired, and use it from a new `cmd_clear()` branch for scope `"--expired"`: delete those
      keys, call `_add_audit(raw, "clear", "expired", keys, reason, now)`, save, and print
      `cleared: <key>` per key; when the list is empty return 0 without saving. Extend
      `main()`'s `clear` subparser with `--expired` (store_true) and reject combining it with a
      positional key or `--all` (exit 1 with a stderr error), mirroring how `--all` is mapped to
      the `"--all"` scope today. In `tests/orchestrator/test_agent_capacity.py` add: a status
      test whose captured stdout shows `(expired)` for a past-window bare `claude` entry and
      `(active)` for a future-window `claude-sub:opus` entry, with the file unchanged afterwards;
      a `clear --expired` test seeding an expired drain gate, an active gate, and an `available`
      entry and asserting only the expired key is removed, the audit entry has scope `expired`,
      and `cleared: claude` is printed; a no-op test asserting exit 0 and byte-identical file
      when nothing is expired; a `main()` test asserting `clear --expired` requires `--reason`
      and rejects `--expired --all`. (Requirements: Status distinguishes an expired gate from an
      active one; Clear supports an expired-only scope)

## 2. Drain-side hygiene

- [x] 2.1 In `src/worktrail/drain/drain.py`'s `record_capacity_gate()`, inside the existing
      `agent_capacity.write_lock()` block after `load()`, iterate the `providers` dict and delete
      every entry whose `source == "drain"` and whose `retry_after`/`reset_at` (parsed via
      `agent_capacity._parse_time`) is not `None` and is `<= now`, before writing the new gate
      and saving. Do not touch entries with any other `source`, entries with no timestamp, or
      drain entries whose window is still active. In `tests/drain/test_drain.py` add: a test
      seeding an expired `codex` drain entry and asserting it is absent after
      `record_capacity_gate(..., "claude", ...)`; a test seeding an expired `spawn`-sourced
      `claude-sub:opus` entry and an active `codex` drain entry and asserting both survive
      alongside the new gate. (Requirements: Drain prunes its own expired gates when it records
      a new one)

## 3. Documentation

- [x] 3.1 In `skills/worktrail-go/SKILL.md`'s "Capacity-cache operator commands" block (around
      line 879), add the `worktrail-agent-capacity clear --expired [--reason TEXT] [--cache PATH]`
      form to the command listing, and extend the `status` bullet to say each gated entry is
      labelled `(active)` or `(expired)` by its retry window and that `--expired` removes only
      the expired ones. Keep every other bullet as-is. (Requirements: Status distinguishes an
      expired gate from an active one; Clear supports an expired-only scope)

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends
      on 1.1, 2.1, 3.1.
