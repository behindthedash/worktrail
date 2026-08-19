## Purpose

Lets multiple `worktrail-drain` processes run concurrently against the same work queue and
runs directory without corrupting shared state or racing each other's actions, so the personal
queue backlog can drain faster than one iteration at a time.

## ADDED Requirements

### Requirement: Bounded worker-slot locking

`worktrail-drain` SHALL support up to `--max-workers` concurrently running processes, each
holding one exclusive numbered slot lock acquired with the same PID-keyed `O_CREAT|O_EXCL` +
stale-lock-takeover mechanism the single-lock design already uses. Slot 0 SHALL use exactly
the configured `--lock-file` path unchanged, so a single invocation with `--max-workers 1` (or
omitted) behaves identically to today. A process that cannot acquire any slot (all held by
live PIDs) SHALL refuse to start with the existing `lock_held` outcome and exit code 2.

#### Scenario: Single invocation, default max-workers
- **WHEN** one `worktrail-drain` process starts with no `--max-workers` override
- **THEN** it acquires the process's slot lock at exactly the configured `--lock-file` path,
  matching pre-concurrency behavior byte-for-byte

#### Scenario: Two concurrent invocations under the configured limit
- **WHEN** two `worktrail-drain` processes start concurrently with `--max-workers 2`
- **THEN** each acquires a distinct numbered slot and both run their full drain loop to
  completion independently

#### Scenario: Invocation beyond the configured limit
- **WHEN** a third `worktrail-drain` process starts while two processes already hold both
  slots of `--max-workers 2`
- **THEN** it refuses to start with the `lock_held` outcome and exit code 2, without
  interrupting the two already-running processes

#### Scenario: A slot's prior holder died
- **WHEN** a process starts and finds a slot lock file whose recorded PID is no longer alive
- **THEN** it takes over that slot exactly as today's single-lock stale-takeover does

### Requirement: Capacity-cache writes are safe under concurrent workers

Every writer of the shared agent-capacity cache (`agent-capacity.json`), including
`worktrail-drain`'s own capacity-gate recording, SHALL serialize its read-modify-write sequence
against concurrent writers so that two workers persisting a gate for the same or different
providers around the same time never lose one writer's update.

#### Scenario: Two workers persist a capacity gate concurrently
- **WHEN** two concurrently running drain workers each classify an account-level failure and
  attempt to record a capacity gate at nearly the same time
- **THEN** the resulting cache file reflects both writers' gate entries, with neither entry
  silently discarded

### Requirement: Run-record attribution is scoped per worker

When a drain worker's iteration unambiguously claims exactly one brief, the worker SHALL
attribute that iteration's outcome only to a run record produced for that brief's target repo,
not to the newest run record across every repo regardless of which worker produced it.

#### Scenario: Two workers finish overlapping iterations against different repos
- **WHEN** worker A's iteration claims a brief targeting repo X and worker B's overlapping
  iteration claims a brief targeting repo Y, and both produce run records within the same
  window
- **THEN** worker A's outcome is classified from repo X's new run record and worker B's
  outcome is classified from repo Y's new run record — neither worker reads the other's record

#### Scenario: Ambiguous or brief-less iteration
- **WHEN** a worker's iteration claims zero or more than one brief (no single unambiguous
  target repo)
- **THEN** attribution falls back to the existing newest-record-not-previously-known behavior,
  unchanged from today

### Requirement: Per-worker working-directory isolation

Each worker slot SHALL launch its one-shot agent process from a dedicated working directory
distinct from every other concurrently running worker's, rather than every worker inheriting
the launching process's shared current working directory.

#### Scenario: Two workers run their one-shot processes concurrently
- **WHEN** worker slot 1 and worker slot 2 each spawn a one-shot agent process at the same
  time
- **THEN** the two processes run with distinct working directories, and neither worker's
  process-local state (e.g. an agent CLI's per-directory session/data files) is shared with or
  overwritten by the other

### Requirement: Repo-wide sweeps run once per drain pass, not once per worker

The pre-loop and post-loop remediation sweep (across `--repos-root`) and backlog seeding SHALL
run only from the worker holding slot 0. Workers holding any other slot SHALL skip both steps
entirely.

#### Scenario: Multiple workers start together
- **WHEN** three workers start concurrently under `--max-workers 3` and all successfully
  acquire a slot
- **THEN** only the worker holding slot 0 performs the pre-loop remediation sweep and backlog
  seeding; the workers holding slots 1 and 2 proceed directly to claiming and draining briefs

#### Scenario: The slot-0 worker exits before the others
- **WHEN** the worker holding slot 0 finishes its drain pass (queue empty, budget exhausted,
  etc.) while workers on other slots are still running
- **THEN** its post-loop sweep still runs before it releases slot 0, and the still-running
  workers are unaffected
