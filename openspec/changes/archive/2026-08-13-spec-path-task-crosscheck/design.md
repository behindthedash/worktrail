## Context

`journal_path_for(repo, spec_rel)` (`live.py`) keys the run-journal file path
only by `Path(spec_rel.rstrip('/')).name` — the trailing path component of
`--spec`. For a base spec (`docs/specs/<id>`) that trailing name is `<id>`;
for a change (`docs/specs/<id>/changes/<slug>`) it is `<slug>`. Devkit task
ids are per-spec sequential (`TASK-001`, `TASK-002`, ...), not globally
unique — every spec/change independently starts its own `TASK-001`. Two
unrelated `--spec` paths can therefore collide on both axes at once: same
trailing name (so `journal_path_for` resolves to the identical journal file)
and overlapping task ids (so `reconcile_from_journal`'s `task = next(t for t
in tasks if t["id"] == task_id)` lookup finds a match in the *current* task
list even though the journal entry was written by a different run). This is
the actual mechanism behind the reported incident: a `full-real --spec
docs/specs/<id>` invocation resumed a 14-task, already-terminal journal
belonging to an unrelated `changes/<slug>` run. It went unnoticed only
because every foreign entry happened to be for a task id not present in the
current task set that run, so `reconcile_from_journal` silently no-op'd on
each one — no error, no warning, and no guarantee that would hold on a repo
where the ids do collide.

## Goals / Non-Goals

**Goals:**
- Detect, at resume time, when a journal contains task-transition entries
  that do not belong to the task set actually declared under the `--spec`
  path just requested.
- Fail loud (hard error) rather than silently reconciling foreign entries,
  since a foreign "done" entry can mark a currently-pending task complete
  and skip real work against the wrong branch.
- Report the specific offending task ids and both spec paths in the error so
  the operator can immediately tell a genuine resume from a path/journal
  mismatch.

**Non-Goals:**
- Not fixing `journal_path_for`'s collision-prone naming scheme itself
  (renaming journal files to a collision-proof key, e.g. a hash of the full
  `spec_rel`, is a larger, separately-scoped change with its own migration
  concerns for journals already on disk — out of scope for this
  brief-requested guard).
- Not adding a general content-addressed or spec-identity-stamped journal
  format. The check below works entirely from information already available
  (the journal's existing entries, the currently loaded task list) without
  changing what a journal records.
- Not touching `precheck`'s or `stale-spec-check`'s existing WARN-only
  semantics for unrelated conditions (files already present, stuck
  `fanout_failed` runs, etc.).

## Decisions

**1. Cross-check by task-id membership, not by adding a stored spec identity
to the journal.** The brief's own suggested approach — cross-check the
journal's task ids against the task ids actually declared under the given
`--spec` path — is sufficient and matches this incident exactly: the
currently loaded `tasks` list (`taskformats.load_spec(str(repo / spec_rel))`)
is already correctly scoped to `--spec` by construction, so any journal entry
whose `task` id has no matching entry in that list is provably foreign.
*Alternative considered:* stamp `spec_rel` (or a hash of it) into the journal
at write time and compare on resume. Rejected for this change — it is the
more root-cause fix but requires migrating/tolerating journals already on
disk without the new field, and duplicates most of the same detection value
the id cross-check already provides. Worth reopening as a follow-up if the
id-based check proves to have false positives in practice (see Risks).

**2. Enforcement level: hard error, not a WARN.** Reconciling a foreign
"done" entry silently downgrades a real pending task to complete, which can
fan out work against the wrong branch with no further signal — the exact
failure the brief is protecting against. This matches the existing
precedent in the same resume path: `validate_task_metadata()` (called
immediately after the resume block) already raises `RuntimeError` for
structurally unsafe task state rather than warning. `precheck`'s WARN-only
posture is not reused here because `precheck` is an optional, skippable
pre-launch step (`worktrail-live precheck`) — the guard must also fire
inside `_full_real_inner` itself so it protects `worktrail-compile`'s
inline auto-compile path and headless/background resumes that never call
`precheck` at all.

**3. Location: `_full_real_inner`'s resume block in `live.py`**, immediately
after `reconcile_from_journal()` returns (so it has both `entries` and the
now-reconciled `tasks` available) and before `validate_task_metadata()`
runs. Not `precheck` or `stale-spec-check`: both are advisory, operator-run
steps an automated `full-real` invocation (e.g. via `worktrail-compile`'s
auto-compile-on-missing-plan path) can bypass entirely.

**4. Mismatch threshold: any non-event journal entry whose `task` id is
absent from the current `tasks` list.** A partial collision (some entries
match, some don't) is treated the same as a total mismatch — it is not a
safer case, and mixed signal is, if anything, more confusing to unwind
manually.

## Risks / Trade-offs

- **[Risk] A legitimate resume after task ids changed between runs** (e.g.
  spec-to-tasks was re-run and renumbered tasks) **would now hard-block
  instead of silently dropping the stale entries** → **Mitigation:** the
  error message names the exact offending ids and both spec paths, and the
  existing `--fresh` flag (`resume=False`, discards the journal and starts
  over) remains the operator's escape hatch — this trades a silent, possibly
  wrong resume for an explicit, one-command-fix stop.
- **[Risk] False positive from manual journal edits or a genuinely renamed
  spec folder** (same content, moved path) → **Mitigation:** same as above;
  the error is actionable and the operator can either re-run with `--fresh`
  or move the journal file to the new expected path themselves.
- **[Trade-off] This narrows but does not eliminate the underlying
  collision** — two `--spec` paths with the same trailing name still share
  one journal file; the guard only prevents that shared file from silently
  corrupting either run's task state. The Non-Goals section above records
  the deeper fix as a deliberately deferred follow-up.

## Migration Plan

Purely additive validation logic — no data migration, no journal format
change, no new CLI flags. Existing journals on disk need no changes. Rollback
is a plain revert of the guard.

## Open Questions

None outstanding — enforcement level (hard error), location (`live.py`
resume block), and cross-check basis (task-id membership) are resolved
above.
