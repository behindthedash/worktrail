## ADDED Requirements

### Requirement: Opt-in remote claim on `spec_id`
`worktrail-run-record claim RUN --specification SPEC --remote` SHALL, after
successfully acquiring the existing local file lock, attempt to acquire a
git-ref-backed remote claim for the same repo+specification against
`origin` before committing the `specification` write to the run record.
`claim` invoked without `--remote` SHALL behave exactly as before this
change (local lock only, no network access).

#### Scenario: First claimant on a fresh spec_id
- **WHEN** `claim RUN --specification SPEC --remote` runs and no ref exists
  at `refs/worktrail-claims/<slug(SPEC)>` on `origin`
- **THEN** the local lock is acquired, an empty commit carrying
  `{run_id, claimed_at, hostname, ttl_seconds}` is pushed to that ref via
  `git push --force-with-lease=<ref>:` (empty expect), the push is verified
  via a follow-up `git ls-remote`, and the run record's `specification`
  field is set to `SPEC`

#### Scenario: Remote-only claim without --remote
- **WHEN** `claim RUN --specification SPEC` runs without `--remote`
- **THEN** no network call is made and behavior is identical to the
  pre-existing local-only `claim` (unchanged CLI output shape)

### Requirement: Cross-machine conflict detection
A second `claim --remote` call for the same repo+specification from a
different machine, while a non-stale remote claim exists, SHALL fail with
the same `{"status": "already-claimed", ...}` shape the local-lock conflict
path already returns, extended with `"scope": "remote"`, and SHALL release
the local lock it had just acquired before returning.

#### Scenario: Second machine contends while first machine's claim is live
- **WHEN** machine B runs `claim RUN_B --specification SPEC --remote` while
  machine A's non-stale remote claim for `SPEC` still exists on `origin`
- **THEN** machine B's local lock (acquired first, per existing sequencing)
  is released, the call exits non-zero, and the JSON output reports
  `{"status": "already-claimed", "scope": "remote", ...}`

### Requirement: TTL-bounded stale-claim reclaim
A contending `claim --remote` call SHALL read the existing remote claim
ref's commit message and, when `now - claimed_at > ttl_seconds` (from that
same commit message), SHALL attempt to reclaim the ref via
`--force-with-lease=<ref>:<exact-stale-sha>` instead of failing outright.
Default `ttl_seconds` is 86400 (24h) unless overridden per-invocation via
`--remote-ttl-seconds`.

#### Scenario: Reclaim after TTL elapses
- **WHEN** machine B contends on `SPEC` and the existing remote claim's
  `claimed_at` is older than its recorded `ttl_seconds`
- **THEN** machine B reads the stale ref's exact SHA, pushes its own claim
  commit via `--force-with-lease=<ref>:<stale-sha>`, and on success proceeds
  as a first claimant (Scenario: First claimant on a fresh spec_id)

#### Scenario: Reclaim races a third machine
- **WHEN** two machines both attempt to reclaim the same stale ref at
  `--force-with-lease=<ref>:<same-stale-sha>` concurrently
- **THEN** at most one push succeeds (git's compare-and-swap on the exact
  stale SHA rejects the second), and the losing machine's `claim --remote`
  reports `{"status": "already-claimed", "scope": "remote", ...}`

### Requirement: Remote claim release on finish
`worktrail-run-record finish` SHALL delete the run's remote claim ref on
`origin`, best-effort and non-fatal, only when the run's own local lock
recorded this run as a `--remote` claimant. A network failure during this
deletion SHALL NOT prevent `finish` from completing.

#### Scenario: Finish releases both layers
- **WHEN** `finish PATH --status <completion-state>` runs for a run that
  claimed `SPEC` with `--remote`
- **THEN** the local lock file is removed (existing behavior, guarded by
  `run_id`) and `refs/worktrail-claims/<slug(SPEC)>` is deleted on `origin`

#### Scenario: Finish tolerates a remote delete failure
- **WHEN** `finish` attempts to delete the remote claim ref and the push
  fails (e.g. `origin` unreachable)
- **THEN** `finish` still completes and records the run's completion state;
  the stale ref remains reclaimable via TTL by a future contender
