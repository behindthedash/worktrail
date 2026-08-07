## Context

`run_record.py cmd_claim` (PR #191) already closes the same-machine TOCTOU
race: it combines the active-conflicts exclusivity check and the
`specification` write into one `O_CREAT|O_EXCL` step on a lock file under
`<run's repo dir>/.claims/<slug>.lock`. That directory lives under
`~/.go/runs` (or policy's `run_record_dir`) — local to the machine running
the claim. A second `/go` session on a *different* machine, with its own
`~/.go/runs`, passes the same `claim` call successfully because it never
sees the first machine's lock file. `#sibling-worktree-check`'s advisory
glob check has the identical blind spot for local refs/worktrees and already
documents it as out of scope; this change closes it for the *hard-stop*
claim path specifically (`#active-conflicts-scan`'s `claim` call), not the
advisory glob check, which stays as-is.

Verified via `git push --help`: `--force-with-lease=<refname>:<expect>`
with an empty `<expect>` requires the named ref not already exist on the
remote — this is git's own atomic create-if-absent primitive, and is
exactly the cross-machine mutex primitive this design needs. `git`'s
receive-pack processes each ref update as a compare-and-swap against the
ref's current server-side value, so two pushes racing to create the same
ref name resolve to exactly one winner — verified in this change's own test
suite against a local bare repo standing in for `origin` (no network
dependency, no reliance on a specific remote's documented behavior).

## Goals / Non-Goals

**Goals:**
- Close the cross-machine race on `spec_id` for the `claim` hard-stop path:
  two different machines calling `run_record.py claim RUN --specification
  SPEC --remote` against the same repo+specification must not both succeed.
- Preserve today's `claim` behavior byte-for-byte when `--remote` is
  omitted — this is an opt-in layer, not a default-on behavior change, since
  not every repo has a writable `origin` reachable from every claiming
  machine (or wants claim state pushed to it at all).
- Make the remote claim's staleness/recovery story explicit and bounded (a
  configurable TTL), since a remote claim cannot lean on reading another
  machine's local run record the way the local-lock reclaim path does.

**Non-Goals:**
- Wiring `--remote` into `#active-conflicts-scan`'s call site in
  `subagent-prompts.md` (the shared procedure both `new`/`modify`'s
  `#sibling-worktree-check` and `implement`'s pipeline call). That is a
  skill-doc + policy-default change with its own decision about whether
  `--remote` becomes the default for repos with an `origin`, and belongs in
  a follow-up once this primitive exists and has been exercised. This
  change ships the primitive and its CLI surface only.
- Making `--remote` the default for `claim` when unspecified. Every repo
  consuming this package would otherwise start pushing claim refs to
  `origin` the next time they upgrade, with no policy opt-in — too large a
  blast radius for a change whose own proposal frames it as additive.
- General-purpose distributed locking unrelated to `spec_id` claims (e.g. a
  reusable git-ref-mutex library). The implementation may be structured so
  the pattern is reusable later, but this change scopes the CLI surface,
  tests, and docs to the `run_record.py claim` use case only.
- Replacing or removing the existing local file lock. The two layers are
  complementary: the local lock is the (cheap, no-network) same-machine
  guarantee; the remote ref is the (network-dependent, opt-in) cross-machine
  guarantee. A repo without a reachable `origin` still gets the same-machine
  protection it has today.

## Decisions

**Ref namespace: `refs/worktrail-claims/<spec-slug>`, not
`refs/heads/*` or `refs/tags/*`.** A claim is operational lock state, not a
branch or a release — it must never show up in `git branch`/`git tag`
listings, PR base/head pickers, or CI branch-trigger globs. Git accepts
pushes to arbitrary custom ref namespaces (the same mechanism `refs/notes/*`
and various CI systems' `refs/pipelines/*`-style metadata refs already rely
on); GitHub accepts and stores them without surfacing them in the branches
UI. `<spec-slug>` reuses `_claim_slug()` (`run_record.py`'s existing
sanitizer for the local lock filename), so both layers key on the exact
same normalized identifier.

**Claim payload: an empty commit, not an annotated tag or a blob-only
ref.** The ref must point at a real object for `git push` to accept it. An
empty commit (`git commit-tree <empty-tree-sha> -m "<json-payload>"`, with
`--allow-empty` semantics via `commit-tree` needing no parent) lets the
commit message carry the same JSON shape the local lock file already uses
(`run_id`, `claimed_at`) plus `hostname` (for operator-facing diagnostics
only — never used as an identity/authority check) and `ttl_seconds`. Reading
it back is a `git fetch` of the one ref plus `git log -1 --format=%B
<sha>`, no working-tree checkout needed.

**Staleness: TTL embedded in the claim payload, not a liveness check.**
The local-lock reclaim path (`_lock_is_stale`) can open the owning run's
record file and check `final_status` because it's on the same filesystem.
A remote claim's owning run's record lives on a different machine entirely
and is not fetchable from here. The only information this machine has is
what the claiming machine chose to publish in the ref's commit message at
claim time: a `ttl_seconds` value (default from a new `--remote-ttl-seconds`
flag, itself defaulted conservatively — see Open Questions) and
`claimed_at`. A contending claim reads the existing ref's commit message,
and if `now - claimed_at > ttl_seconds`, treats it as stale and attempts
the compare-and-swap reclaim (`--force-with-lease=<ref>:<exact-stale-sha>`)
instead of failing outright. This trades perfect liveness detection (not
available cross-machine without a shared coordination service, which is
explicitly out of scope) for a bounded, auditable, self-healing default —
consistent with this workspace's `march of nines` posture of preferring an
imperfect-but-bounded automated recovery over an unbounded manual-only one,
while still keeping today's manual-recovery escape hatch (delete the ref
directly: `git push origin :refs/worktrail-claims/<slug>`) documented for
an operator who needs to intervene before the TTL elapses.

**Claim sequencing: local lock first, then remote ref.** `cmd_claim` already
acquires the local lock before doing anything else. `--remote` adds the
remote-ref attempt *after* the local lock succeeds, and on remote-claim
failure, releases the just-acquired local lock and returns the same
`{"status": "already-claimed", ...}` shape the local-only conflict path
already returns (extended with a `"scope": "remote"` field so a caller can
tell which layer contended, without changing the existing `"scope"`-less
shape for `--remote`-less callers — REQ-NR compatibility, see Non-Goals).
This ordering means a remote conflict never leaves an orphaned local lock
behind, and a machine that only ever contends locally (no `--remote`) never
touches the network.

**Release: `finish` deletes the remote ref only when the run's own claim
recorded `"remote": true`.** `finish` already reads the run record to find
its `specification` and release the matching local lock guarded by
`run_id`. The same guard extends to the remote ref: delete
`refs/worktrail-claims/<slug>` on `origin` only if the local lock file (read
before deletion, same as today) recorded this run as the remote claimant.
Remote deletion is best-effort and non-fatal — a network failure at
`finish` time must not block the run from finishing (the TTL is the
backstop for a ref that a `finish`-time network blip left behind).

## Risks / Trade-offs

- [Risk] A network partition or `origin` outage at claim time makes
  `--remote` claims unavailable exactly when multi-machine coordination
  matters most. → Mitigation: `claim --remote` fails closed (treats a push
  error indistinguishably from a genuine conflict, releasing the local lock
  and returning `already-claimed`) rather than silently falling back to
  local-only — a caller that asked for cross-machine protection and didn't
  get it must not proceed believing it has exclusivity. The exact error is
  still surfaced in the CLI output for operator diagnosis.
- [Risk] TTL-based staleness means a legitimately still-running claim can be
  reclaimed by another machine if it undershoots the TTL on an unusually
  long run. → Mitigation: default TTL is deliberately conservative (see Open
  Questions) and the value is a `--remote-ttl-seconds` CLI flag so an
  operator can raise it per-invocation for known-long routes; this mirrors
  the same trade-off the existing local-lock's `finish`-clears-it model
  already accepts (a crashed session's stale local lock needs the same kind
  of external judgment call today, just via manual inspection instead of a
  timer).
- [Risk] Custom ref namespaces are an unusual git usage pattern; a
  misconfigured remote or a proxy/mirror that strips non-standard refs could
  make claim pushes silently no-op. → Mitigation: `claim --remote` verifies
  its own push by re-running `git ls-remote origin
  refs/worktrail-claims/<slug>` immediately after and confirming the
  returned SHA matches what was just pushed, treating a mismatch as a claim
  failure rather than assuming success from a zero exit code alone.

## Migration Plan

Purely additive CLI surface (`--remote`, `--remote-ttl-seconds` on `claim`;
no change to `finish`'s existing arguments, only its internal behavior when
a claim recorded `"remote": true`). No data migration — existing local lock
files and run records are read exactly as before. No repo needs to change
anything to keep current behavior; adopting cross-machine protection is a
per-invocation (or later, per-policy once the follow-up wires it into
`#active-conflicts-scan`) opt-in.

## Default TTL (resolved from data, not guessed)

Surveyed this machine's own `~/.go/runs/**/*.yaml` (834 completed run
records with both `started_at` and `completed_at`): Route D — the
implementation route that launches the orchestrator and holds the claim for
the run's full duration — has p50 ≈ 1.0h, p90 ≈ 12.5h, max ≈ 37.8h across
44 completed runs. Default `--remote-ttl-seconds=86400` (24h): comfortably
above the observed max for a legitimately long-running claim, while still
bounding a crashed claim's recovery to at most a day instead of requiring
indefinite manual intervention. An operator with a route/repo that
routinely runs longer than 24h can override per-invocation via
`--remote-ttl-seconds`.
