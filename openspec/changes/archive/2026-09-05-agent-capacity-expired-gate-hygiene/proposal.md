## Why

`worktrail-drain` persists account-level failures into the shared capacity cache through
`record_capacity_gate()` (`src/worktrail/drain/drain.py:682-700`). Those entries are keyed by
bare agent name (`claude`, `codex`) rather than the `<target>:<model>` keys that
`agent_capacity.record()` writes, because drain has no model concept of its own. Nothing ever
removes them: the read side (`drain.capacity_gated()`, `agent_capacity.check()`,
`gate_snapshot()`) correctly treats an entry whose `retry_after` has passed as not gated, so the
drain does pick the agent back up — but the entry itself stays in `agent-capacity.json`
indefinitely, and every later drain failure for the same agent simply overwrites it.

The operator-facing view makes this worse. `worktrail-agent-capacity status`
(`src/worktrail/orchestrator/agent_capacity.py:423-461`) appends `(active)` only when
`retry_after > now`; an entry whose window has passed prints as a plain
`<key>  unavailable` line with `failure: <class>` and a `retry:` timestamp, with no marker that
the gate is already expired. An operator returning to the workspace reads that as a live block,
and the documented remedy (`worktrail-agent-capacity clear <key> --reason ...`,
`skills/worktrail-go/SKILL.md:879`) is the only way to make the line go away — for a gate that
is no longer doing anything. There is no scope to clear only the expired entries, so the
alternative is `clear --all`, which also drops gates that are still genuinely active.

No active change under `openspec/changes/` touches status rendering or bare-key pruning, and the
most recent commits to `agent_capacity.py` (#886, #827) do not either.

## What Changes

- `worktrail-agent-capacity status` labels every gated entry (`status` of `unavailable`,
  `gated`, or `blocked`) whose `retry_after`/`reset_at` has already passed with `(expired)`,
  alongside the existing `(active)` label for an unexpired window. An entry with a gated status
  and no timestamp at all keeps printing without either label, since the read side treats it as
  gated until cleared.
- `worktrail-agent-capacity clear` gains an `--expired` scope that removes exactly the entries
  `status` would label `(expired)`, records one `clear` audit entry with scope `expired` naming
  the removed keys, and leaves active gates, timestamp-less gates, and `available` entries
  untouched. It requires `--reason` like every other clear and is a no-op (exit 0) when nothing
  is expired.
- `record_capacity_gate()` in `worktrail-drain` prunes, inside the same locked read-modify-write
  that persists a new bare-key gate, any existing entry with `source: drain` whose retry window
  has already passed. Drain-owned entries are the only ones this touches: entries recorded by
  `spawn_agent` (`source: spawn`) are never pruned by the drain, since their lifecycle is owned
  by the selector.

## Capabilities

### Modified Capabilities

- `model-tier-routing`: adds the expired-gate rendering and `clear --expired` requirements to the
  capacity-cache operator surface it already owns (`Capacity gates key on target and model`).
- `drain-concurrent-workers`: adds the drain-side prune of its own expired bare-key gates to the
  existing `Capacity-cache writes are safe under concurrent workers` contract.

## Impact

- **Code**: `src/worktrail/orchestrator/agent_capacity.py` (`cmd_status`, `cmd_clear`, `main`
  argparse); `src/worktrail/drain/drain.py` (`record_capacity_gate`).
- **Docs**: `skills/worktrail-go/SKILL.md` capacity-cache operator command block gains the
  `--expired` form and the `(active)`/`(expired)` label meaning.
- **Tests**: `tests/orchestrator/test_agent_capacity.py` (status label, `clear --expired`
  scope and audit); `tests/drain/test_drain.py` (drain prunes only its own expired entries).
- **Non-goals**: changing how the drain keys its gates (bare keys stay — `capacity_gated()`
  deliberately honors them as provider-wide); pruning on read anywhere (`status`, `check`,
  `gate_snapshot` remain read-only); pruning `spawn`-sourced cells; any change to gating
  semantics — every reader already treats an expired window as not gated, so this change is
  purely about what the cache file retains and what the operator sees.
