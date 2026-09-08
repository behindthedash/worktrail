## Context

`run_record._save()` stamps `updated_at` on every mutation, and
`_run_liveness()` currently maps its age directly to `fresh`. This works as a
heartbeat: a recent write is useful evidence that a different dispatch is
active. It is not a process probe. The workflow's long-running orchestrator
is deliberately launched through `worktrail-detach`, whose status contract
already distinguishes `running`, `exited`, `gone`, and `unknown` using its
pid and exit sentinel. That evidence is not currently associated with the
run record, so `liveness` and `sweep-orphans` cannot use it.

The prior investigation documents two separate historical causes: records
predating `updated_at`, and native-skill double-starts. The latter was fixed by
PR #591 / `9dc9daaa`; this change must not reopen the dispatch contract or
reinterpret the historical backlog as proof of a current defect.

## Goals / Non-Goals

**Goals:**

- Preserve heartbeat freshness as useful, low-cost activity evidence.
- Persist enough information to query the existing detached-process status for
  runs that have a detached orchestrator owner.
- Make automatic orphan closure require affirmative dead-owner evidence, not
  merely the absence of a recent record write.
- Keep records created by older callers and native skills safe: unavailable
  process evidence is unknown, never dead.

**Non-Goals:**

- Add a universal process probe for native Skill sessions or remote machines.
- Add a periodic heartbeat process, alter the default TTL, or make a stale
  heartbeat terminal by itself.
- Clear, migrate, or assign terminal statuses to prior run records.
- Change `worktrail-detach`'s state-file protocol or the fixed native-dispatch
  `run:$RUN` reuse contract.

## Decisions

### D1: Bind the existing detached handle; do not invent a second PID protocol

Add a run-record command that stores a validated detached launch identity on
the already-open record: the detach name and, when non-default, its state
directory. The command must reject malformed names and must not synthesize a
pid. Immediately after `worktrail-detach launch` returns a successful handle,
the orchestrator workflow records this identity against `$RUN` before it begins
monitoring.

The liveness reader then calls the existing detach status API using this stored
identity. It does not parse pid files itself, repeat `os.kill`, or infer a
process from a command line. This keeps the owner-state definition in
`runtime.detach`, including its exit-sentinel precedence and its conservative
`unknown` state.

**Alternative rejected:** storing the launch PID directly and calling
`os.kill(pid, 0)` in `run_record.py`. PID reuse and a missing exit sentinel
would make this less trustworthy than `worktrail-detach status`, while also
duplicating its tested state protocol.

### D2: Return independent evidence and one conservative reconciliation class

`liveness` retains its existing `fresh`, `age_seconds`, `updated_at`, and
`same_dispatch` fields for callers that use heartbeat collision evidence. It
adds a detached-process state and a reconciliation classification:

| Detached owner evidence | Heartbeat | Reconciliation class | Meaning |
|---|---|---|---|
| `running` | any age | `active_process` | The owner is observably alive; an old heartbeat is diagnostic only. |
| `exited` or `gone` | stale | `confirmed_orphan` | The owner ended without a terminal run record. |
| `exited` or `gone` | fresh | `recently_updated_after_owner_exit` | Do not close automatically; the record may be completing. |
| absent, malformed, or `unknown` | any age | `unknown_owner` | No affirmative process result; retain for Route E/operator review. |

Terminal records remain non-live as today and receive a terminal
classification. A `same_dispatch` result remains an ownership identity hint,
not proof that a detached child is alive; it does not override the table.

This is intentionally additive for the liveness JSON shape. Existing callers
can continue to read `fresh`, while new consumers must use the reconciliation
class where an automatic write is contemplated.

### D3: `sweep-orphans` only acts on `confirmed_orphan`

The sweep remains an explicitly destructive command with its current
`--status`, `--note`, and `--dry-run` interface. It only calls `finish` when
the record has a stale heartbeat and the bound detached owner reports `exited`
or `gone`. A bound running owner goes into `skipped_active_process`; stale
records lacking a usable owner probe go into `skipped_unknown_owner`. Fresh
records remain in `skipped_live` for compatibility with the current summary.

The output lists each category and includes the liveness reason/class for a
closed record's auto-generated note. This makes dry runs useful for health
triage and prevents a false terminal status from destroying the very evidence
needed to resume a long-running process.

### D4: Update the operational prose and pin it structurally

The full-real orchestration instructions own both `$RUN` and the
`DETACH_JSON` returned by the launch. They will extract the handle's name and
state directory and bind it only after confirming launch success. The
plugin-surface test will require that launch-to-bind sequence, so a future
prose edit cannot silently restore the heartbeat-only blind spot.

## Risks / Trade-offs

- A detached process that is alive but wedged remains non-terminal. This is a
  deliberate false-negative bias: Route E can reconstruct it, whereas an
  automatic `failed_terminal` is irreversible audit damage.
- Older and native-session records will commonly be `unknown_owner`. The
  health guard can now label them accurately, but cannot auto-close them. A
  future native-session liveness contract needs separate evidence and is not
  guessed here.
- A process can exit between liveness read and `finish`. That only changes an
  already-confirmed-orphan result toward more certainty; the sweep still
  re-reads the record immediately before writing through `cmd_finish`.

## Migration Plan

No migration is required. Existing YAML records omit detached-owner metadata
and are classified `unknown_owner`; their fields and serialized format remain
readable. New launches begin recording the handle after this change ships.
Rollback is a normal code revert; persisted metadata is ignored by older
readers.

## Open Questions

None. The intentionally conservative behavior is bounded by the evidence
available on this host and leaves unproven cases to the existing recovery path.
