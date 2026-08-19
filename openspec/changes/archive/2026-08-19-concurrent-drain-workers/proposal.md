## Why

The personal work queue (71 briefs at investigation time) drains at one iteration at a time
because `worktrail-drain` holds a single exclusive lock and runs one `worktrail-go auto`
one-shot to completion before starting the next. At the nightly cron's current
`--max-items 4`, a queue this size takes weeks to clear even with zero new intake, and intake
(bridge-health-guard findings, cross-session handoffs, pullhook webhooks, dependabot dispatch)
keeps growing. Raising `--max-items` again only delays the next `queue_growth` alert — it does
not change the serial-throughput ceiling. Investigation
(`docs/specs/research/drain-concurrent-workers.md`) confirmed `work_queue.py claim`'s atomic
rename already makes concurrent brief claiming safe; the blocker is `drain.py` itself, plus one
unlocked shared-state writer it depends on.

## What Changes

- Replace `drain.py`'s single exclusive `drain.lock` with a bounded worker-slot scheme: up to
  `--max-workers` independent `worktrail-drain` processes can each hold one numbered slot lock
  and run their own full drain loop concurrently, using the existing PID-keyed
  `O_CREAT|O_EXCL` + stale-lock-takeover mechanism per slot (slot 0 keeps today's exact
  `drain.lock` filename, so a lone invocation is byte-for-byte unchanged).
- Fix `record_capacity_gate()`'s unlocked read-modify-write against
  `agent-capacity.json` by reusing `agent_capacity.py`'s own existing (but currently
  private-to-that-module) file-lock primitive, which every other writer in that module already
  uses. **Prerequisite for safe concurrency, not optional hardening** — see design.md D2.
- Fix a second, previously undiscovered concurrency gap: `newest_run_record()`'s run-record
  attribution picks the single newest `*.yaml` across every repo's run directory with no
  per-worker scoping. Two concurrent workers can misattribute each other's outcome (wrong PR,
  wrong success/failure), silently corrupting circuit-breaker and pending-approval state. Scope
  attribution to the claiming worker's own claimed brief's target repo when unambiguous.
- Give each worker slot a dedicated, isolated working directory (mirroring
  `orchestrator/spawnlib.py`'s existing per-`cwd` state isolation for orchestrator task
  workers) instead of every worker inheriting the launching process's shared cwd.
- Restrict the pre-loop/post-loop remediation sweep (`sweep_remediations`) and backlog seeding
  (`seed_backlog`) to the worker holding slot 0 ("leader"), since both scan and mutate state
  across the entire `--repos-root` independent of any one worker's claimed brief; running them
  from every worker would duplicate PR-opening/resume actions concurrently.
- Add a `drain.max_workers` operator config key (`worktrail_home()/config.json`), following the
  existing CLI > config > built-in precedence already used for `drain.agent` /
  `drain.fallback_agents`. Built-in default is `2`.
- `work_queue.py claim`'s atomic-rename guarantee needs no change — already safe for concurrent
  callers.

Not changed: the devops-repo nightly cron wrapper (`~/bin/worktrail-drain-nightly.sh`) that
actually launches N concurrent `worktrail-drain` invocations. That is a follow-up change in the
`devops` repo, out of scope here — this change only makes concurrent invocations *safe*.

## Capabilities

### New Capabilities
- `drain-concurrent-workers`: bounded multi-worker drain execution — worker-slot locking,
  the capacity-cache write-lock fix, per-worker run-record attribution scoping, per-worker cwd
  isolation, and leader-only remediation-sweep/backlog-seed gating.

### Modified Capabilities
- `drain-operator-config`: add a `drain.max_workers` key to the existing CLI-over-config-over-
  built-in resolution requirement, alongside `drain.agent` / `drain.fallback_agents`.

## Impact

- `src/worktrail/drain/drain.py`: `acquire_lock`/`release_lock` become slot-aware; `drain()`
  gains worker-slot/cwd/leader parameters; `newest_run_record()` gains repo-scoped attribution;
  new `--max-workers` CLI flag.
- `src/worktrail/orchestrator/agent_capacity.py`: `_write_lock` becomes a public, documented
  entry point usable by `drain.py`'s `record_capacity_gate()`.
- `src/worktrail/shared/operator_config.py` (or wherever `drain_config()` lives): resolve
  `drain.max_workers` alongside the existing agent/fallback keys.
- `tests/` mirrors: new/updated coverage under `tests/drain/` and
  `tests/orchestrator/test_agent_capacity.py`.
- No change to `work_queue.py`, `worktree.py`, or the orchestrator's own `max_workers` fan-out
  (`orchestrator/live.py`, `orchestrator/orchestrate.py`) — this change composes with that
  existing 3x fan-out rather than modifying it; see design.md D3 for the resulting concurrency
  budget.
- Out of scope: the devops-repo nightly cron wrapper that would actually launch N concurrent
  `worktrail-drain` processes in production.
