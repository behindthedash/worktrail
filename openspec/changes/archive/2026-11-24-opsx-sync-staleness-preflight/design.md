## Context

See proposal.md for the motivating incident. `/opsx:sync` is agent-driven: its paired command and
skill tell an agent to read a change delta and write canonical specs. A sync worktree can be behind
the target repository's remote base even when it is clean, so its local canonical spec is not safe
input for either deciding whether work remains or merging the delta. The active
`sync-draft-converge-main-delta` change addresses heading shape and draft landing; it does not make
the sync operation's input authoritative.

## Goals / Non-Goals

**Goals:**

- Make a fetched `origin/<base>` the authoritative canonical-spec baseline for every manual or
  drain-dispatched sync.
- Make an already-converged remote result an explicit, edit-free terminal outcome.
- Keep the two bundled instruction surfaces behaviorally identical and regression-tested.

**Non-Goals:**

- Updating the caller's local base branch, rebasing the sync worktree, or changing drain's
  worktree-creation policy.
- Replacing the agent's semantic delta merge with a new Python merge engine.
- Treating mere commit ancestry as proof that all delta operations are reconciled.

## Decisions

### D1 — Fetch first and read canonical content from `origin/<base>`

The procedure fetches `origin` before it reads canonical specs for merge or convergence decisions,
then resolves the target base (the dispatch-provided base when available, otherwise `main`). It
reads each canonical capability spec with Git from `origin/<base>` and bases any write on that
content. `git fetch` refreshes the one authority that can contain a landing absent from the
worktree without mutating a local branch. Reading the worktree then comparing afterward would
still let a stale input drive destructive edits.

### D2 — A no-op requires content evidence, not ancestry alone

Before editing, the procedure compares each delta's declared requirement blocks with the matching
remote canonical requirement blocks, ignoring only trailing whitespace. It reports `already
synced` and stops without edits only when every affected capability is already reflected at the
remote base; missing, different, or unmatchable remote content proceeds to normal merge. A landing
commit's ancestry alone is insufficient because a later change could have altered the same
requirement.

### D3 — Preserve one source of behavior across command and skill

`commands/opsx/sync.md` and `skills/openspec-sync-specs/SKILL.md` receive the same preflight and
outcome rules. Plugin-surface tests assert the key instructions in both files so a later edit of
one surface cannot silently reintroduce stale-local merging. Duplicating concise procedural text
is preferable to redirecting one user-facing surface through an unavailable runtime-specific
mechanism.

## Risks / Trade-offs

- A fetch failure prevents synchronization → fail before any edit and report the Git failure; an
  operator can retry once remote access is restored.
- Semantic partial modifications cannot be declared byte-equal → conservatively perform the
  existing intelligent merge against remote content rather than claim a no-op.
- The remote advances after the fetch → the sync uses the consistent snapshot it fetched; PR
  landing remains responsible for later integration concurrency.
