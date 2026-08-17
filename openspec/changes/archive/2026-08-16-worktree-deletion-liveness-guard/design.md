## Context

See `proposal.md` - Why for the incident this closes. Relevant existing primitives:

- `run_record.py` already has a `liveness RUN_PATH [--ttl-seconds N] [--dispatch-id ID]`
  command (shipped in PR #479) that reports `{"fresh": bool, "same_dispatch": bool,
  "age_seconds": ..., "updated_at": ...}` for one run record given its path. It is
  already consumed once, in `worktrail-go/SKILL.md`'s Active-run-resume check.
- Every run record already has a `worktree` field in its schema (`cmd_start`, set to
  `None`), but no call site currently writes a non-`None` value into it, and no
  existing command can look a run record up *by* worktree path — only by the record's
  own file path.
- `active-conflicts` establishes the CLI shape this design follows: `--dir`, `--repo`,
  plus a scan key (`--specification` there), returning JSON, read-only, tolerant of
  malformed record files via `_load_lenient`.
- All three deletion call sites already run in a shell with `$INVOCATION_CONTEXT_DISPATCH_ID`
  available (it is how the existing Active-run-resume liveness check gets its own
  identity) except the `cleanup-worktrees` flow, which has no `$RUN`/dispatch context of
  its own since it is dashboard-picker-invoked, not run-scoped.

## Goals / Non-Goals

**Goals:**
- Make the run record the single source of truth for "which worktree does this run
  own," populated at creation time, so a later deletion can look it up without
  guessing from path naming conventions.
- Reuse the existing `liveness` command's `fresh`/`same_dispatch` semantics unchanged
  — this change adds a lookup step in front of it, not a new liveness model.
- Keep the guard's default-safe behavior identical to today's when nothing has
  changed: no owning record found, or the record is stale, or same dispatch → deletion
  proceeds exactly as it does now.

**Non-Goals:**
- Diagnosing or fixing whatever mechanism actually deleted the worktree in the
  original incident — this is defense-in-depth against the symptom regardless of
  trigger (see proposal.md).
- Changing `liveness`'s TTL, heartbeat semantics, or same-dispatch comparison logic.
- Guarding worktree *creation* paths (the sibling-check and active-conflicts-scan
  procedures already exist for that) — this change is deletion-only.
- A general-purpose "list all worktrees owned by run records" reporting command —
  `find-by-worktree` answers one worktree path at a time, matching how each call site
  already knows the single `$WT` it's about to delete.

## Decisions

**Add `find-by-worktree` as a new read-only `run_record.py` subcommand, not a flag on
`liveness`.** `liveness` takes a run-record path and answers a freshness question about
that one record; the deletion call sites start from a worktree *path*, not a run-record
path — they need a resolution step first. Keeping resolution and liveness as two
composable commands (`find-by-worktree` then `liveness`) mirrors the existing
`active-conflicts` → `claim` two-step pattern rather than growing `liveness` a second,
unrelated input mode.

Signature: `run_record.py find-by-worktree --dir DIR --repo REPO --worktree PATH`,
printing `{"found": bool, "path": str|null, "run_id": str|null}` (empty/null fields
when `found: false`). Scans `<dir>/<repo-name>/*.yaml` the same way
`_active_conflicts()` does, using `_load_lenient` so one malformed file (a hand-edited
or half-written record) is skipped with a warning rather than aborting the whole
lookup — same tolerance policy the codebase already applies everywhere else it scans
the run-records directory. If more than one non-terminal record's `worktree` field
matches (shouldn't happen under normal operation, but two records could both name the
same now-stale path after a crash-without-cleanup), return the most recently started
one — the same "most recent wins" tie-break `active-conflicts` already uses for
overlapping records.

**Write `worktree` on the run record immediately after `git worktree add`, at all three
worktree-creation sites (`#spec-worktree-setup`, `#change-spec-worktree-setup`,
`#fix-branch-worktree-setup`), via the existing generic `run_record.py set "$RUN"
worktree "$WT"`.** No new `set`-adjacent command is needed — `set` already writes an
arbitrary key/value pair to a run record and is exactly what this needs. Doing this at
creation time (not lazily, e.g. inferred from `git worktree list` at deletion time)
keeps `find-by-worktree` a pure lookup with no git dependency of its own, and matches
how every other identifying field on a run record (`repository`, `base_branch`) is
already stamped once, at creation, rather than derived later.

**Add one shared guard procedure to `subagent-prompts.md`, invoked from both teardown
sections there, plus a parallel invocation from `worktree-cleanup.md`.** The guard is:
resolve `$WT` → run record via `find-by-worktree`; if found, call `liveness` on it with
the caller's own `$INVOCATION_CONTEXT_DISPATCH_ID`; if `fresh: true` and
`same_dispatch: false`, refuse and report; otherwise proceed unchanged. This is written
once as a named procedure (`#worktree-deletion-liveness-guard`) and referenced from all
three call sites rather than copy-pasted, following this file's existing pattern of
shared named sections (`#active-conflicts-scan`, `#sibling-worktree-check`).

**The `cleanup-worktrees` flow resolves its run-records directory from
`worktrail-policy --json`'s `run_record_dir`, not from a `$RUN` variable.** That flow is
dashboard-picker-invoked outside any single run's context — it has no `$RUN` and no
`$INVOCATION_CONTEXT_DISPATCH_ID` of its own in the way the other two call sites do. It
already needs to resolve the repo's policy for other reasons (base branch, etc.), so
reading `run_record_dir` from that same policy load is a natural extension, not a new
resolution path. For its own dispatch identity in the `liveness --dispatch-id` call, it
passes whatever `$INVOCATION_CONTEXT_DISPATCH_ID` is set to in its invoking shell (the
same variable every other call site uses) — if unset, `liveness` already treats a
missing `--dispatch-id` as "never same-dispatch," which is the conservative, correct
default for an unscoped cleanup action (never assume ownership you can't prove).

**Alternatives considered:**
- *Infer the owning run record from `git worktree list` + branch-name pattern
  matching instead of a stored field.* Rejected: branch naming already diverges across
  the three creation paths (`spec/$SPEC_ID`, `fix/$SLUG`, `<run-id>/<group>`), so a
  single inference rule would need to know all three conventions and keep them in
  sync by hand. A stored `worktree` field is one flat equality check regardless of
  which path created it.
- *Extend `active-conflicts` to also answer "who owns this worktree" instead of adding
  `find-by-worktree`.* Rejected: `active-conflicts` conflict-scopes on `specification`
  (a spec id or `fix:`-prefixed key), and unspecced/group worktrees don't cleanly map
  onto that key space the way a direct `worktree` field match does. Reusing it would
  require overloading its `--specification` argument with a second, incompatible
  meaning.

## Risks / Trade-offs

- **Existing run records predate the `worktree` field being populated.** →
  `find-by-worktree` reports `found: false` for those (their `worktree` field is
  `None`, same as today), so the guard falls through to "proceed unchanged" — identical
  to current behavior for any worktree created before this change ships. No backfill
  needed; the gap self-heals as old runs finish and new ones stamp the field.
- **A worktree deleted through some path this change doesn't touch (a manual `git
  worktree remove` run by a human, or a future call site that forgets the guard)
  remains unprotected.** → Out of scope per Non-Goals; this is a targeted fix for the
  three documented, discovered call sites, not a git-level enforcement mechanism.
- **The guard adds two subprocess calls (`find-by-worktree`, `liveness`) to every
  deletion, including the common case where nothing is wrong.** → Both are already
  cheap, read-only, local-filesystem operations (the same cost `liveness` already pays
  once per Active-run-resume check); no network or lock contention involved.

## Migration Plan

No data migration — `worktree` on existing run records already defaults to `None`
today (schema unchanged, just newly populated going forward). Rollout is code +
skill-text only:
1. Ship `find-by-worktree` in `run_record.py` with test coverage.
2. Wire `run_record.py set "$RUN" worktree "$WT"` into the three creation sites.
3. Add the shared guard procedure and wire it into the two `subagent-prompts.md`
   teardown sections and the `worktree-cleanup.md` prune step.
4. No flag or opt-out — this guard is unconditional at all three sites, consistent
   with the other hard-stop ownership guards already in `subagent-prompts.md`
   (`#active-conflicts-scan`, the fix-branch atomic ownership guard).

Rollback is a plain revert (skill-text procedure + one new CLI subcommand); nothing
it touches is a compatibility-breaking change to existing record files or commands.
