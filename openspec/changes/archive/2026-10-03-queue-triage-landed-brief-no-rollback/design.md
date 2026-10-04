## Context

`_worktree_pr_close()` owns the whole fold/propose apply sequence: claim the brief, create the
worktree off the fetched remote base, bootstrap, author, validate, compile, `land_pr`, close the
brief with `triaged_to`, and clean up. Its failure discipline is explicit in the code: every
pre-PR failure returns `status="error"` from inside the `try` and the shared cleanup `finally`
releases the claimed brief back to `queue/` **under `if not pr_url:`** (`queue_triage.py:3304`).
Past that `finally`, exactly one path is reachable — a successful `land_pr` with a truthy
`pr_url` — because the `refused` and empty-`pr_url` outcomes both return early
(`queue_triage.py:3250`, `3260`).

The `done()` call that closes the brief sits on that reachable-only-with-a-PR path
(`queue_triage.py:3323`), but its rejection branch releases unconditionally
(`queue_triage.py:3326`). So a brief whose PR exists — for `landed` +
`completed_and_merged`, one already squash-merged onto base — is returned to `queue/` and
re-triaged as fresh work. The bootstrap requirement and the merged-landing requirement both
scope release-back-to-`queue/` to failures with no PR URL, so the rollback contradicts the
spec, not just the intent.

## Goals / Non-Goals

- Goals: a rejection from the closure step can never un-claim a brief whose PR exists; the brief
  stays visible and closable against the real PR; no-PR failures keep today's release behavior,
  once per attempt.
- Non-Goals: changing `done()`'s evidence gate or its rejection set; changing the closure step's
  arguments; changing worktree/branch teardown; touching the `stale-close`/`duplicate-of` path.

## Decisions

- **Remove the release rather than guard it with a second `if not pr_url:`.** The guard already
  exists at `queue_triage.py:3304`; the rejection branch is unreachable without a truthy
  `pr_url`, so a second guard would be dead code that also *permits* a double release if a later
  edit ever let a no-PR path fall through to it. Removing the call leaves exactly one release
  site — the guarded one in the cleanup `finally` — which makes "released at most once, and
  only when no PR exists" structural rather than conditional. The entry keeps its `rolled_back`
  key, now reporting `false` on this branch.
- **The rejected closure is carried, not forced.** The reported rejection is the
  closure-evidence gate (`unverified_reverification_claim`), which exists to demand real
  evidence. This triage path composes its note from the verdict's own evidence and has no re-run
  transcript of its own, so the only honest outcomes are: leave the brief claimed until it is
  closed against the PR that exists, or fabricate evidence. Fabricating (a fenced transcript or
  any equivalent structured note) would defeat the gate and is explicitly rejected.
- **`picked/` with `status: picked` is the recovery surface.** `router/dashboard.py`'s
  `inflight_briefs()` already scans `picked/` for `status: picked` briefs past the staleness
  window and surfaces them on the resume dashboard, so a brief left claimed here is discovered
  by an existing path — no new resume mechanism, no new frontmatter field. The rejection message
  in the entry is what the resuming session reads.
- **`_apply_close()` (`queue_triage.py:2248`, the `stale-close`/`duplicate-of` claim+done path,
  release at line 2285) is deliberately unchanged.** Its `done()`-reject-then-release shape is
  superficially identical, but it opens no PR at all (no `land_pr` call in the function) and its
  docstring documents the release as deliberate: the brief must not sit stranded in `picked/`
  under a `queue-triage` claim nobody will release, and a failed claim+done leaves nothing on
  disk to resume. The invariant this change installs is precisely "release is wrong once a PR
  exists" — a condition that path never meets. A later reader should not "fix" it for symmetry.

## Risks / Trade-offs

- A rejection that is not transient and not about the missing transcript (e.g. an ownership
  mismatch in `done()`) now leaves the brief in `picked/` until a session resolves it, where
  before it silently returned to the queue. That is the intended fail-closed direction: the
  observable alternatives are "duplicate PR for merged work" or "human resolves a visible
  stalled brief". The dashboard's stalled-in-flight listing already covers discovery.
- The entry's `rolled_back` field is now always `false` on this branch. Callers reading it as
  "did we undo the claim" keep working; any caller treating `rolled_back: false` as "the brief
  is still claimed and needs attention" gets the honest answer.

## Migration

No data, CLI, or frontmatter migration. Behavior change is limited to the rejection branch of
the post-PR closure; no existing brief needs repair for the fix to take effect.
