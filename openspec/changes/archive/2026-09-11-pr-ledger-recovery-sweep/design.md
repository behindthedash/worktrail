## Context

`land_pr()` has a bounded inline CI watcher, but its state is only available to
the active caller. The preflight PreToolUse hook protects agent-typed PR
creation, but has no persistent recovery record. A recovery feature needs one
source of truth that is safe for concurrent landing calls and can be queried by
both a periodic process and a session Stop hook.

## Decisions

### D1. Store one JSON ledger below `worktrail_home()`

The ledger lives at `worktrail_home() / "pr-ledger.json"`, not in a repository
or a run record. A PR may be opened outside a run record and a scheduled sweep
must survive removal of the opening worktree. Entries are keyed by canonical PR
URL and include repository path/identity, opener (`land_pr` or `preflight`),
optional run and brief IDs, session ID when available, `opened_at`, and recovery
state. Writes use a same-directory temporary file followed by atomic replace so
concurrent readers never consume partial JSON. A malformed/unreadable ledger is
reported without deleting it; sweep does not guess or overwrite operator data.

### D2. Registration occurs only after a PR is known to exist

`land_pr()` registers after its find-or-create step returns a URL, including an
already-open PR found for the head branch. The preflight path registers only
after the guarded `gh pr create` command succeeds; a `check` verdict alone
cannot prove a PR was created. Both callers use the same upsert helper, making
retries idempotent and preserving the original opening timestamp/session while
filling missing provenance.

### D3. Sweep classifies live state and files one durable recovery request

`worktrail-pr-ledger sweep` uses `gh pr view` for each ledger entry and records
the observed state. A merged PR is dropped. A green open PR with auto-merge
armed is retained but needs no intervention. A red, `BLOCKED`, or stale-open PR
without a current watcher produces a single deduplicated work-queue brief with
the canonical `pr fix` request, PR URL, repository, and observed evidence.
The entry records the created brief ID and observation time before the command
returns, so a later five-minute sweep does not create another brief. Failure to
query GitHub is non-destructive and reported for a later retry.

“Live watcher” is a ledger heartbeat written by `land_pr()` around its watch
loop. Its freshness window and the stale-open pacing threshold are explicit CLI
options with conservative defaults; the sweep never assumes a missing
heartbeat is a crashed watcher until the grace window has elapsed.

### D4. Stop hook asks the ledger rather than parsing the transcript

The Stop hook passes the platform session ID to a read-only ledger query. If it
finds a non-terminal entry opened by that session, it emits a blocking response
that names the PR and tells the agent to resume its CI/recovery work. It runs
before the ordinary once-per-session suggestion sentinel is written, so an
unresolved PR cannot be bypassed by consuming that sentinel first. Missing CLI,
timeout, malformed output, and headless mode all fail open, preserving the
hook's existing reliability boundary.

## Alternatives Considered

- **Run records as the ledger:** rejected because direct `gh pr create` has no
  required run record and a run record may be archived or live in a torn-down
  worktree.
- **Have the cron invoke `land_pr()` again:** rejected because it would need
  the original branch/worktree and risks commits or pushes during recovery. A
  sweep is read-mostly and hands repair to the established queue/drain flow.
- **Stop-hook transcript matching:** rejected because it cannot tell whether a
  PR was actually created, whether it is terminal, or whether an earlier
  session owns it.

## Risks / Trade-offs

- The external five-minute cron configuration is outside this package. The
  console command and JSON output make that deployment change small and
  observable, but this PR cannot itself prove the host schedule was updated.
- GitHub outages leave entries intact and may delay repair; they must never be
  interpreted as a terminal state.
- A Stop hook is deliberately fail-open, so a missing local installation does
  not strand an interactive session. The periodic sweep remains the durable
  safety net in that case.
