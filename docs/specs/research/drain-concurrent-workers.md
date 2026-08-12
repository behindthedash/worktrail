# Investigation — concurrent worktree-isolated drain workers for nightly `worktrail-drain`

Route I (investigation) note. Source brief:
`20260812-142016-nightly-worktrail-drain-consumes-the`.

Status: **investigation only — no code changes.**

## Problem, as verified

The nightly drain cron (`~/bin/worktrail-drain-nightly.sh`, `17 2 * * *`) runs a single
`worktrail-drain` process against `$WORK_QUEUE_DIR` with `--max-items 4
--budget-minutes 200` (bumped from `--max-items 2` on 2026-08-12 by devops PR #171 after a
`bridge-health-guard` `queue_growth` alert). The personal work queue currently holds **71**
briefs (`worktrail-work-queue list --json`, checked live during this investigation). At 4
items/night, a queue this size takes on the order of weeks to drain even with zero new
intake, and the brief's premise — that intake (bridge-health-guard findings, cross-session
handoff captures, pullhook webhooks, dependabot dispatch) will keep growing — is consistent
with the observed backlog. The prior fix (raising `MAX_ITEMS`) only delays the next
`queue_growth` alert; it does not change the underlying serial-throughput ceiling.

## Verified observations

1. **`drain()` is a single-writer, serial loop by design.** `acquire_lock()`
   (`src/worktrail/drain/drain.py:1034`) takes an exclusive `O_CREAT|O_EXCL` PID-keyed lock
   on `--lock-file` (default `~/.go/drain.lock`) once at the start of the run. A second
   concurrent `worktrail-drain` invocation observes `lock_held` and exits immediately
   (`drain.py:1244-1245`) — this is an intentional single-instance guard, not an
   implementation accident.
2. **Each iteration is fully sequential.** The loop spawns one headless agent CLI process
   (`claude -p "worktrail-go auto"` by default), blocks on `run_one_shot()` until it exits,
   classifies the outcome from the newest run record, then re-checks the queue before
   deciding whether to launch the next iteration (`decide()`, `drain.py:1105`). There is no
   concurrency primitive (no `ThreadPoolExecutor`, `asyncio`, or `multiprocessing`) anywhere
   in `drain.py` — confirmed by grep, zero matches for `concurrent|parallel|ThreadPool|
   ProcessPool|asyncio`.
3. **`work_queue.py claim` is already safe for concurrent callers.** Per
   `subagent-prompts.md` (`worktrail-go` skill) and the queue's own design, claiming moves a
   brief `queue/` → `picked/` via an atomic POSIX rename, "so two agents never work the same
   brief." Multiple simultaneous drain workers claiming *different* briefs would not race
   each other at this layer — no fix needed here.
4. **The capacity-gate cache is NOT concurrency-safe.** `record_capacity_gate()`
   (`drain.py:429-445`) does a plain read-modify-write against `~/.go/agent-capacity.json`
   (`agent_capacity.load()` → mutate dict → `agent_capacity.save()`) with no file lock. Two
   drain workers hitting a capacity gate on the same agent within the same window could race:
   the second writer's `load()` reads a state that does not yet reflect the first writer's
   `save()`, and one gate write silently clobbers the other. This is a real correctness gap
   for any concurrent-worker design, not a hypothetical one — the file is a shared mutable
   resource with no lock today.
5. **Machine load compounds multiplicatively, not additively.** A drain iteration that
   dispatches routes D/F/G/H does not spawn one agent process — it invokes the orchestrator's
   `full-real`, whose own fan-out defaults to `max_workers=3` (`orchestrator/live.py`,
   `orchestrator/orchestrate.py`, confirmed via grep — three independent call sites all
   default to 3). N concurrent drain workers, each potentially running an orchestrator fan-out
   of up to 3 task workers, means up to `3N` concurrent headless-agent processes at peak, not
   `N`. This machine already runs 4 CI self-hosted runners sharing the same WSL host
   (see workspace memory `feedback_dont_run_heavy_local_suites_during_own_ci`) — 3 drain
   workers at the orchestrator's default fan-out could reach 9 concurrent agent processes,
   which is a materially different resource profile than today's single-process drain.
6. **The parallel SDD orchestrator's worktree-isolation pattern is a proven, reusable model**
   for this: `orchestrator/worktree.py` + `coordinator.py` already give each concurrent task
   its own git worktree and branch so parallel workers never share a mutable working tree
   (per `AGENTS.md`'s "Worktree-first parallel coding isolation" doctrine, already the
   standing default for this workspace). Applying the same pattern at the drain level — one
   worktree-isolated worker per claimed brief — is architecturally consistent with what this
   repo already does one layer down, not a new pattern.
7. **No prior or in-flight work addresses this.** Checked: no worktree, branch (local or
   `origin/*`), OpenSpec change, or `docs/specs/` entry mentions concurrent/parallel drain
   workers. The one in-flight OpenSpec change touching `drain.py`
   (`drain-sync-pending-remediation`) is unrelated — it adds a fourth stall-remediation
   category (`sync-pending`) to `REMEDIATION_TABLE`, not concurrency.

## Unknowns / not investigated here

- Whether the personal work-queue's briefs are independent enough in practice that N
  concurrently claimed briefs would rarely collide on the same target repo (a same-repo
  collision is already guarded by `active-conflicts-scan`/`sibling-worktree-check` at the
  sdd-workflow layer, but that guard's cost — e.g. contention/retry rate — under real
  concurrent claim pressure is not measured).
- Actual per-provider (claude/codex/opencode) rate-limit headroom for 2-3x the current
  concurrent request rate — `agent_capacity`'s existing gate/cooldown logic assumes it is
  reacting to one process's failures, not several racing to update the same cache.
- Whether a worker-pool size should be a fixed nightly config value or dynamically sized to
  current queue depth (e.g. don't spin up 3 workers for a queue of 2 ready briefs).

## Recommendation

**Continue into Route C (feature planning).** This is scoped, real engineering work — not a
one-line config change — with concrete design decisions a spec should pin down before
implementation:

1. Replace the single exclusive `drain.lock` with a bounded worker-slot scheme (e.g. N
   numbered lock files or a semaphore-style lock directory) so up to `--max-workers` drain
   processes can run concurrently instead of the current one-or-none.
2. Fix `record_capacity_gate()`'s (and any other `agent_capacity` read-modify-write caller's)
   race by adding a file lock around the load/mutate/save sequence — this is a prerequisite
   for safe concurrency, not an optional hardening step, per finding 4 above.
3. Decide the default worker count and its interaction with each worker's own orchestrator
   `max_workers` fan-out, given the `3N` compounding effect (finding 5) and this machine's
   shared CI-runner load — a conservative default (e.g. 2 drain workers) is likely warranted
   rather than matching the brief's upper suggestion of 3 outright.
4. Reuse `orchestrator/worktree.py`'s isolation pattern per drain worker (finding 6) rather
   than inventing a new isolation mechanism.
5. Keep `work_queue.py claim`'s existing atomic-rename guarantee (finding 3) as the
   correctness backbone — no change needed there.

Not recommending Route D directly: the worker-slot lock scheme and the capacity-cache lock
fix are both design decisions (lock granularity, default worker count vs. this machine's
resource ceiling) worth a short spec and acceptance criteria before code, not "obviously
correct" one-line fixes.
