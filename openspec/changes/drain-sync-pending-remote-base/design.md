## Context

The sync-pending action owns an isolated, short-lived worktree so its format-native sync never writes into the target repository's canonical checkout. It currently resolves a policy base name (falling back to `dev`) and passes that local name directly to `git worktree add`. The action already uses `origin` for the fix-branch push, while its re-entrant open-PR check runs before any worktree lifecycle operation.

## Goals / Non-Goals

**Goals:**

- Make each newly created sync-pending remediation worktree start at the current remote base tip.
- Retain the existing failure-isolation and existing-open-PR behavior.
- Cover the stale-local-base case with hermetic real-git tests.

**Non-Goals:**

- Changing the base-branch policy lookup, push remote, PR landing, or other drain remediations.
- Updating a canonical checkout's local base branch as a side effect of the sweep.

## Decisions

- **Fetch `origin <base>` immediately before worktree creation, then use `origin/<base>` as the worktree start point.** This refreshes only the remote-tracking ref needed by the action and does not mutate the canonical local base branch. Using the local branch after a fetch would retain the bug because `git fetch` does not fast-forward that branch. Fetching after `git worktree add` is too late because the worker has already inherited the stale snapshot.
- **Leave the open-PR fast path before the fetch.** An already-open remediation branch needs no new worktree and has no reason to perform a network operation; this preserves the current no-op behavior.
- **Use the existing `_run_git` seam.** A fetch failure raises through the existing action boundary, where the generic sweep logs it and continues to other findings. No parallel error-handling path is needed.
- **Test with an actual bare `origin`.** Advance `origin/dev` from a second clone after the fixture repository's local `dev` becomes stale; assert the sync branch contains that remote-only commit. Command-list assertions alone would not prove the worktree was based on the fetched ref.

## Risks / Trade-offs

- **Remote fetch fails during a sweep** → the existing per-finding isolation reports the failure and continues; no worktree or PR is created for that finding.
- **The remote advances again after fetch** → the remediation is correctly based on the current snapshot available at its start; handling concurrent base advancement during PR landing remains the shared landing pipeline's responsibility.
