## Context

`_active_conflicts()` in `src/worktrail/router/run_record.py` scans a repo's run-record
directory (`<dir>/<repo-name>/*.yaml`) for non-terminal records (`final_status is None`)
whose `specification` field matches the one being checked. Every match is currently
treated as a live conflict — used as a hard stop by `#active-conflicts-scan`
(`new`/`modify`/`implement` pipelines) and as the exclusivity check inside `claim`.

There is no notion of staleness in this scan today. `claim`'s own `_lock_is_stale()`
helper checks something narrower and unrelated: whether the *lock file's owning run
record* still exists and is non-terminal — it never inspects the worktree or the
files the run touched. A run record can be non-terminal, have a real path on disk,
and still be describing work that finished and merged weeks ago (confirmed via the
`go-20260717-085119` incident: its `worktree` field pointed at a deleted directory
and every path in its `files_changed` was already present on `dev`).

## Goals / Non-Goals

**Goals:**
- Let the active-conflicts scan tell "genuinely still running" apart from "orphaned
  bookkeeping" without a human reading git log by hand.
- Keep the hard-stop behavior for real conflicts completely unchanged — this is a
  narrowing of false positives, not a loosening of the guard.
- Make the reconciliation itself auditable: a record closed by staleness detection
  must say so in its own `merge_result`, the same way the manual `go-20260717-085119`
  closeout did.

**Non-Goals:**
- Detecting staleness for a run whose worktree lived on a different machine (no local
  path to check). Cross-machine reconciliation is deferred per the proposal.
- Changing `claim`'s `_lock_is_stale()` lock-file staleness check — different failure
  mode (a dead claim-lock, not an orphaned run record), already handled correctly.
- Any change to what counts as "terminal" (`final_status is not None`) — staleness
  reclassifies a *non-terminal* record as reconcilable; it does not add a new
  terminal state.

## Decisions

**Staleness test: worktree-gone AND files-merged, both required.**
Either signal alone is a false-positive risk: a worktree can be legitimately absent
mid-run (not yet created, or torn down between orchestrator phases) while work is
still owned by a live session elsewhere; a `files_changed` entry can be present on
base by coincidence (another change touched the same path) while the run's own work
is still in flight. Requiring both mirrors the proposal's own reproduction case and
keeps the check conservative — a live run is never misclassified as stale, at the
cost of occasionally leaving a genuinely-stale-but-only-half-evidenced record for
manual reconciliation (acceptable: that is today's status quo for every case).

**"Files merged" test: `git cat-file -e <base_branch>:<path>` per `files_changed`
entry, not a diff/log heuristic.** `files_changed` entries are free-text sometimes
(`docs/specs/.../customerx-mvp-frontend-core/ (data-model, contracts, KG, 28
tasks)` from the real incident record) — take the leading whitespace-delimited
token as the path candidate and check it exists as a blob/tree at
`<base_branch>` via `git cat-file -e`. A record is "files-merged" only if it has
at least one `files_changed` entry AND every extracted path candidate resolves.
An empty or unparseable `files_changed` list fails this check (never merged by
default) rather than being treated as vacuously true — a record with no
recorded files must not be auto-closed on the worktree-gone signal alone.

**Partition the scan's return shape instead of adding a second endpoint.**
`_active_conflicts()` returns `{"live": [...], "stale": [...]}` instead of a flat
list; `cmd_active_conflicts` prints that object. This is a breaking change to the
JSON shape the module docstring documents, but `active-conflicts` has exactly two
callers in this repo (`#active-conflicts-scan`, and `claim`'s internal re-check) —
both are updated in this same change, and no other consumer of this CLI is known.
Alternative considered: keep the flat list and add a new `--partition-stale` flag.
Rejected — it would leave the unflagged default call site (the one every existing
skill anchor already cites) silently blind to staleness, defeating the point.

**`reconcile` is a separate explicit subcommand, not automatic inside the scan.**
`active-conflicts` stays read-only (matches its own docstring: "read-only scan").
A new `reconcile RUN_PATH --note "..."` subcommand does the actual `finish
--status completed_and_merged` write, re-verifying the same staleness test at
write time (never trust a caller's earlier read). `#active-conflicts-scan`
calls `reconcile` on every entry in the `stale` partition before evaluating
`live` for the hard stop, so from the pipeline's perspective this is automatic —
but the CLI itself keeps read and write on separate commands, consistent with
every other pair in this module (`active-conflicts` vs `claim`/`finish`).

## Risks / Trade-offs

[Risk] A record is reconciled based on a `files_changed` list that was hand-written
loosely (prose fragments, not real paths) → its path-candidate extraction never
resolves via `cat-file`, so the record simply stays unreconciled (falls through to
the existing manual path). Mitigation: none needed — this is a false negative
(under-reconciliation), not a false positive; it degrades to today's behavior for
that record.

[Risk] `git cat-file -e` on a base branch far behind local `HEAD` (stale local
fetch) could under- or over-report merge state. Mitigation: this scan is already
scoped to whatever `git` state the local checkout has (identical exposure to every
other run-record git check in this codebase); not a new risk this change
introduces.

[Trade-off] Reconciliation happens opportunistically, only when a later
`#active-conflicts-scan` call touches the same `specification` — a stale record with
no future collision is never proactively swept. Accepted: matches the proposal's
scope (unblock new work on the same spec_id), and a periodic sweep is a natural
follow-up (`prune` already exists for a related but distinct retention concern) if
this proves insufficient.

## Migration Plan

No data migration — existing run records need no field changes; the staleness test
reads fields (`worktree`, `files_changed`, `final_status`) that already exist on
every record. Deploy as a normal PR merge to `main`; no flag or rollout gate needed
since the change only narrows what already-non-terminal records get treated as
blocking. Rollback is a plain revert — no state to unwind.

## Open Questions

None outstanding — scope is fully bounded by the proposal's reproduction case and
the existing `claim`/`_lock_is_stale` precedent for how this module already
distinguishes "record exists" from "record is meaningfully live".
