## Context

`src/worktrail/drain/drain.py` is a single-file driver: `acquire_lock()` takes one exclusive
`O_CREAT|O_EXCL` PID-keyed lock (`drain.py:1188-1210`) before `drain()`'s `while True:` loop
(`drain.py:1438`) runs iterations strictly one at a time. Each iteration spawns exactly one
`worktrail-go auto` one-shot via `subprocess.run` with no `cwd=` argument (`run_one_shot`,
`drain.py:1376-1387`), so it inherits the launching process's cwd. `record_capacity_gate()`
(`drain.py:437-454`) does a plain `agent_capacity.load()` → mutate → `agent_capacity.save()`
against the shared `agent-capacity.json`, bypassing `agent_capacity.py`'s own `_write_lock`
context manager (`agent_capacity.py:118-146`) that every other writer in that module
(`record()`, `configure()`, `cmd_clear()`) already uses. `newest_run_record()`
(`drain.py:305-325`) globs `runs_dir.glob("*/*.yaml")` — records live at
`<runs_dir>/<repo-name>/<run-id>.yaml` (confirmed via `router/dashboard.py`, `router/policy.py`,
`router/run_record.py`) — and returns the single mtime-newest file not present in a pre-spawn
"known" snapshot, with no per-repo or per-worker scoping. `sweep_remediations()`
(`drain.py:1131-1164`), invoked pre-loop and post-loop (`drain.py:1420-1435`,
`drain.py:1554-1563`), and `seed_backlog_mod.seed_backlog()` (`drain.py:1424-1435`) both scan
and mutate state across the entirety of `--repos-root`, independent of any single iteration's
claimed brief. `work_queue.py claim()`'s `os.rename` (`work_queue.py:626`) is already the
atomic arbiter for concurrent brief claiming across processes — verified safe, no change
needed. `orchestrator/spawnlib.py`'s `run_worker()` already demonstrates the precedent this
design follows for per-process isolation: it takes an explicit `cwd` and derives an
isolated opencode state directory from it (`spawnlib.py:531-538`, `cwd=str(cwd)` at
`spawnlib.py:871`) specifically because two concurrent workers sharing one directory would
corrupt each other's agent-CLI state.

See proposal.md - Why for the throughput motivation; see
`docs/specs/research/drain-concurrent-workers.md` for the underlying investigation (findings
1-7) this proposal's four requested decisions are drawn from.

## Goals / Non-Goals

**Goals:**
- Make N `worktrail-drain` processes safe to run concurrently against the same queue, runs
  directory, and capacity cache.
- Preserve exact single-process behavior when only one worker is running (byte-for-byte, not
  just "equivalent").
- Reuse existing, already-tested primitives (`acquire_lock`'s stale-PID takeover,
  `agent_capacity._write_lock`, `spawnlib.py`'s cwd-isolation precedent) rather than inventing
  new coordination mechanisms.

**Non-Goals:**
- Actually launching N concurrent `worktrail-drain` processes in production (the devops-repo
  cron wrapper change) — this change only makes it safe to do so.
- Changing the orchestrator's own `max_workers` fan-out (`orchestrator/live.py`,
  `orchestrator/orchestrate.py`) — drain-level concurrency composes with it, doesn't touch it.
- Cross-machine drain coordination — slots are local-machine-only, matching the existing
  lock file's local-machine scope.
- Dynamic worker-count autoscaling based on queue depth (noted as an open question in the
  investigation; not pursued here — `--max-workers` stays a static, operator-set value).

## Decisions

### D1 — Bounded worker-slot locking replaces the single exclusive lock

Extend `acquire_lock(lock_file)` to `acquire_lock_slot(lock_file, max_workers)`, which tries
candidate paths `lock_file` (slot 0), `f"{lock_file}.1"` (slot 1), ... `f"{lock_file}.{N-1}"`
in order, calling the **existing, unmodified** `acquire_lock()` per candidate (same
`O_CREAT|O_EXCL` + stale-PID-takeover logic). The first successfully acquired slot wins; `None`
if all `max_workers` slots are held by live PIDs, which surfaces as today's `lock_held` /
exit-2 refusal.

Why slot 0 == the bare `lock_file` path: a single invocation (`--max-workers` omitted or `1`)
must produce the identical lock filename as today, so existing operator tooling
(`WORKTRAIL_DRAIN_LOCK_FILE` in `worktrail-drain-nightly.sh`) and any monitoring keyed to that
path keep working unmodified.

**Alternative considered**: a single semaphore-style lock directory holding up to N PID files
(one `flock`-based directory, count entries to decide admission). Rejected — it's a new
coordination primitive duplicating what N independent numbered files already give for free,
and the existing `acquire_lock()` stale-takeover logic (dead-PID detection) would need to be
reimplemented for a shared-directory model instead of reused verbatim.

Who launches the N processes: **out of scope** here (see proposal.md - Impact / Non-Goals).
Each `worktrail-drain` invocation independently scans for a free slot; N invocations started
by any external launcher (cron, a shell loop, systemd) safely self-arbitrate.

### D2 — Reuse `agent_capacity.py`'s existing write-lock for `record_capacity_gate()`

Promote `agent_capacity._write_lock` to a public `write_lock` (drop the leading underscore;
same `flock`-based context manager, same graceful non-POSIX degradation, no behavior change
for its existing callers). `drain.record_capacity_gate()` wraps its
`load()` → mutate → `save()` sequence in `agent_capacity.write_lock(cache_path)`, matching
exactly how `record()`/`configure()`/`cmd_clear()` already protect the same file.

Per the proposal's framing: this is a **prerequisite**, not an enhancement — with N>1 workers,
two workers persisting a capacity gate around the same time is exactly the interleaving
`_write_lock`'s own docstring (`agent_capacity.py:118-127`) describes as unsafe without it
("the second writer's `os.replace` silently discards the first worker's provider state").

**Alternative considered**: give `record_capacity_gate()` its own separate lock file. Rejected
— it would leave two different lock disciplines protecting the same underlying file depending
on which module wrote last, which is strictly worse than one shared lock, and the shared lock
already exists and is tested.

### D3 — Default worker count: 2, informed by the existing 3x orchestrator fan-out

Default `--max-workers` (and `drain.max_workers` operator-config fallback) is **2**. Each
drain worker's dispatched `worktrail-go auto` one-shot can itself invoke the orchestrator
(routes D/F/G/H), whose own fan-out defaults to `max_workers=3` at multiple call sites
(`orchestrator/live.py:1998,2124,3178,4258`, `orchestrator/orchestrate.py:165`) — confirmed
unchanged by this proposal (Non-Goals). At the default of 2 drain workers, peak concurrent
headless-agent processes is up to `2 × 3 = 6`, on top of this machine's 4 existing self-hosted
CI runners sharing the same WSL host (`~/projects/devops` cron-managed runners) — up to 10
concurrent heavy processes at peak. The investigation explicitly recommended 2 over the
proposal's own upper bound of 3 (`3 × 3 = 9`, plus 4 CI runners = 13) as the conservative
choice; this design adopts that recommendation as the shipped default rather than the upper
bound. `--max-workers` remains fully operator-tunable via CLI or `drain.max_workers` config for
sites with more headroom.

Because slot 0 always maps to the pre-existing lock filename (D1), shipping this default is
inert for the existing single-invocation nightly script — it becomes consequential only once a
second invocation is deliberately started (a separate, devops-repo change).

### D4 — Per-worker cwd isolation, reusing `spawnlib.py`'s pattern

`run_one_shot()` gains an optional `cwd` parameter, threaded from a new per-slot scratch
directory: `worktrail_home() / "drain-workers" / f"worker-{slot}"`, created (`mkdir(parents=True,
exist_ok=True)`) before first use. This is passed as `cwd=str(path)` on the one-shot's
`subprocess.run` call — the same mechanism `spawnlib.run_worker()` already uses
(`spawnlib.py:871`) for orchestrator task workers, including that function's incidental benefit
of automatic per-cwd opencode state isolation (`opencode_data_dir()`, `spawnlib.py:536-538`)
for any drain worker configured with `agent=opencode`.

This is explicitly **not** a literal `git worktree add` via `orchestrator/worktree.py`'s
`WorktreeManager` — that class is keyed to one fixed `repo_root` + `spec_id`/`task_id` and
creates an actual git worktree of that one repo. A drain worker has no single fixed target repo
(a claimed brief can point at any repo under `--repos-root`), so there is nothing to `git
worktree add` at the drain-worker level; the orchestrator/fix-branch layers *downstream* of the
one-shot (already worktree-isolated per spec/task, per `worktree.py` and
`close_stale_bookkeeping`'s `_reset_stale_bookkeeping_worktree`/`repo.parent /
f"{repo.name}-worktrees"` pattern) remain unchanged and continue to do that isolation
themselves. What this decision reuses is the **cwd-isolation principle and naming discipline**
(deterministic per-worker directory, created up front, never shared) — the same idea
`worktree.py`'s sibling-directory convention encodes, generalized from "one git worktree per
repo+task" to "one scratch directory per drain worker slot."

**Alternative considered**: instantiate `WorktreeManager(repo_root=<some anchor repo>,
spec_id="drain", ...)` with `task_id=f"worker-{slot}"` to get a literal git worktree. Rejected
— there's no principled choice of anchor repo (drain workers aren't bound to one), and a git
worktree's guarantees (isolated `.git` index/HEAD) aren't the property we need here — we need
an isolated *cwd*, not isolated *git state*, which is exactly what `spawnlib.py`'s existing
precedent already provides more directly.

### D5 — Run-record attribution scoped to the claiming worker's own brief (discovered during
this investigation, beyond the proposal's four listed decisions)

`newest_run_record()`'s current "single global newest, not previously known" heuristic is safe
under one process (exactly one new record can appear per iteration) but actively unsafe under
N concurrent workers: two workers' iterations can produce new records within each other's
windows, and each worker's post-spawn lookup returns whichever record is newest **across every
repo**, regardless of which worker produced it — silently misattributing outcome (state, PR
url, brief) and corrupting that worker's circuit-breaker/pending-approval accounting.

Fix: when an iteration's `claimed_briefs` is unambiguous (`len == 1`, the existing
single-claim path — `drain.py:1485`, `classify_outcome`'s `brief` variable), resolve that
brief's `repo` field from the pre-iteration `list_queue()` snapshot (already present in the
JSON: `work_queue.py:458`, `"repo": fm.get("repo")`) and restrict `newest_run_record`'s glob to
`runs_dir/<repo>/*.yaml` only. When the claim is ambiguous or empty (today's existing
`brief=None` path), attribution keeps the current global-newest fallback — same residual risk
as today, but now exercised only for the rarer unattributed case rather than every iteration.

This decision is necessary for D1-D4 to actually deliver correct concurrent drains; without it,
concurrency would be structurally *safe* (no crashes, no corrupted files) but *incorrect*
(wrong outcomes silently recorded).

### D6 — Repo-wide sweeps and backlog seeding run once per pass (leader-only)

`sweep_remediations()` (pre-loop and post-loop) and `seed_backlog()` scan and mutate state
across the whole `--repos-root`, independent of any one worker's claimed brief — the
`_existing_stale_bookkeeping_pr`/`_existing_..._pr` open-PR checks (`drain.py:805-817`) make
concurrent duplicate attempts fail safe (a second `gh pr create` for the same head branch
errors, caught and logged by `sweep_remediations`' per-finding `try/except`) but still
duplicate work and log noise at N workers. Restrict both to the worker holding slot 0 (`slot ==
0`, the deterministic "leader"); other slots skip straight to the claim loop. No new
coordination primitive — slot 0 is already a unique, deterministic winner once acquired (D1).

**Alternative considered**: let every worker sweep, relying on the existing idempotency checks
to absorb duplication. Rejected as needlessly wasteful (N-fold repo scanning cost) and noisier
than necessary for a change whose whole point is efficient resource use on a
CI-runner-constrained host (D3).

## Risks / Trade-offs

- **[Risk]** A leader (slot 0) that dies mid-sweep leaves its post-loop sweep un-run for that
  pass. → **Mitigation**: sweeps are idempotent (each finding checks for an already-open PR /
  already-resumed state before acting), so a skipped pass is caught by the *next* drain
  invocation's pre-loop sweep, whichever worker becomes the next slot-0 leader. No data loss,
  only a delayed remediation.
- **[Risk]** Raising real concurrency from 1 to `3N` peak agent processes (D3) increases load on
  a host already running 4 CI self-hosted runners. → **Mitigation**: default of 2 (not the
  proposal's upper bound of 3) is the deliberately conservative choice; `--max-workers` and
  `drain.max_workers` remain fully operator-tunable per-machine.
- **[Trade-off]** D5's repo-scoped attribution only covers the unambiguous single-claim case;
  a multi-claim or claim-less iteration still uses the pre-existing (imperfect-under-
  concurrency) global-newest fallback. → Accepted: this matches the existing philosophy already
  encoded in `classify_outcome` ("an ambiguous multi-claim iteration is left unattributed rather
  than guessed at") — extending the same reasoning rather than solving every case up front.
- **[Risk]** A brief's `repo:` field is empty/missing (older or malformed briefs). → D5's scoped
  lookup degrades to the existing global-newest fallback in that case (never raises), same
  fail-open posture as the rest of `drain.py`'s best-effort classification code.

## Migration Plan

- Fully backward compatible at `--max-workers 1` (the default `drain.max_workers` of 2 only
  becomes consequential once a second concurrent invocation is actually started — see D1/D3).
- No data migration; `agent-capacity.json` and run-record layouts are unchanged, only their
  write/read discipline.
- Rollback: revert the PR. No persisted state format changes to unwind — a reverted `drain.py`
  reads/writes the same file shapes as before.
- Deploying actual N-way concurrency in production (updating
  `~/bin/worktrail-drain-nightly.sh` to launch N processes) is a separate, later devops-repo
  change, sequenced after this one merges and its tests are green.

## Open Questions

- Whether `drain.max_workers` should eventually scale dynamically with live queue depth instead
  of staying a static operator-set value (raised, not resolved, by the investigation) — deferred
  as a possible follow-up; does not change this change's specs, approach, or tasks.
