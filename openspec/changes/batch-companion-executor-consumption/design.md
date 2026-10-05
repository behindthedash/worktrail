## Context

See proposal.md for the incident and the shipped claim side. The mechanics a consumption path
must respect, verified in this checkout:

- `claim_batch()` stamps both directions of the link: each claimed companion gets
  `batch-primary: <primary-stem>` (`work_queue.py:1444`) and the primary gets a `batch:` list
  of the companion stems actually claimed (`_set_fm_list_field`, `:1455`). Both stamps are
  best-effort -- a stamp failure is reported on the companion entry but never aborts a claim --
  and both briefs land in `picked/` under their own filenames.
- `release()` strips `batch-primary` from a released brief (`work_queue.py:1843-1845`) but the
  primary's `batch:` list keeps the stem, so a stale member is a normal, expected state: the
  file is simply no longer beside the primary.
- The executor's handoff-seed flow is single-brief by construction: `handoff_seed.py` maps one
  path to one seed; `#handoff-seed` Step 3 asserts this dispatch's own claim on one brief,
  Step 4 builds the seed, Step 7 closes that brief with
  `done "<id>" --implementation-complete --run "$RUN" --by "$GO_DISPATCH_ID"`.
- The seed's top-level fields are consumed by the `new` pipeline's brainstorm step
  (`feature_idea`, `constraints`); classification takes the primary's `recommended_route`,
  `change_kind`, and `target_spec` (Step 5).
- `handoffs_consumed` is read by `classifier_coverage.load_actual_routes()` as the "actual"
  route label for a brief -- the strongest evidence class in that audit -- and by nothing else.
  `set` writes a value verbatim, so only `set-list` can produce a list (see the
  `_scalar_list_field_hint` incident note).
- `tests/test_plugin_surface.py` is the repo's lockstep home for skill⇄package drift; the
  defect this change fixes is exactly a promise in one document with no path in the other.

## Goals / Non-Goals

**Goals:**

- Make the documented promise true: a claimed batch reaches the worker as one union request,
  one classification, one run record, one worktree/PR.
- Keep per-brief completion state honest: every consumed brief closed individually with the
  shared run; a companion that did not ride or did not land released, never silently done.
- Keep the seam single-sourced: the claim's own on-disk `batch:` list is the batch, the seed
  mapper remains the one owner of brief→seed mapping, and the module stays read-only.
- Leave the single-brief path byte-identical in behavior.

**Non-Goals:**

- Re-batching or retrying a companion excluded at fold time (it goes back to the queue; the
  next pass may pick it on its own).
- Auto-resolving a companion that classifies elsewhere (a human/agent decides; the fold-time
  release is the whole remedy).
- Changing `claim-batch`, its stamps, the dashboard grouping, the dispatch contract, or
  `handoffs_consumed`'s consumers.
- Withdrawing batching from the front door (see Decisions).

## Decisions

- **Implement the executor path; do not stop auto-folding.** The rejected alternative -- have
  the front door release companions it cannot consume -- would delete a documented feature
  promised in three places while leaving the shipped claim machinery (`claim_batch`,
  `score-candidates --mode batch`, the `batch-primary`/`batch:` stamps, the dashboard
  grouping) with no consumer, and it would make the queue deeper, not shorter: the companions
  exist because a single brief's run can carry them under the same route/worktree/PR. The
  consumption side is the smaller, additive change.

- **The batch is read from the primary brief's own `batch:` frontmatter -- not from the run
  record, and not from a new dispatch token.** The claim wrote that list at claim time from
  the stems it actually claimed, in the file the executor is already handed; the run record's
  `handoffs_consumed` is the field this change is making trustworthy, and reading it back as
  the input channel would invert cause and effect (it is exactly the undocumented channel the
  incident used); a dispatch token would duplicate on-disk state and could drift from it
  between claim and dispatch (e.g. a companion released in between).

- **Companions resolve beside the primary; the mapper follows the primary's own declared
  pointer.** A stem resolves to `<primary-dir>/<stem>.md`. This keeps `handoff_seed.py`'s
  read-only boundary intact: it never lists or globs the queue (which a reverse scan for
  `batch-primary: <primary>` would require, and which could disagree with the primary's own
  record); it follows an explicit link *inside the file it was handed*. A stem that does not
  resolve is an error member, not a failure -- the released-companion state is normal.

- **The seed emits `batch` and leaves the top-level fields to the primary; the executor
  composes the union.** The module answers "what did this claim include"; "what does the run
  execute" is the executor's decision, because the executor is the component that can drop and
  release a member (route/repo mismatch) -- and the documented output shape stays additive for
  every existing consumer. The executor labels each companion's contribution with its brief id
  when composing `feature_idea`/`constraints`, so the worker can tell which brief asked for
  what.

- **A member that cannot ride is released, and release means release.** Error member
  (unresolvable), different `repo`, or route evidence naming a different route: excluded from
  the request and returned to the queue with `--by "$GO_DISPATCH_ID"` (the identity that
  claimed it), reported by id and reason. Forcing it in would either fan a second repo's work
  into this worktree/PR or hand the run a route it is not executing; ignoring it would leave
  it silently picked, which is the bug being fixed.

- **`handoffs_consumed` is written at close, by the executor, as the consumed set.** Claimed ≠
  consumed: an excluded companion never ran and must not be labelled with this run's route in
  `classifier_coverage`'s actual-route join. Writing it at Step 7 (after `$RUN` exists in both
  dispatch forms) via `worktrail-run-record set-list` is idempotent on a re-run and avoids the
  scalar trap `set` would spring.

- **Closure stays per-brief, through the existing gate.** Each consumed brief gets its own
  `done ... --run "$RUN"` (or `--planning-only` on a planning-only run) so the
  implementation-closure evidence gate verifies each closure against the same PR-owning run
  record; a companion whose scope did not land is released instead. This is the doc's own step
  5, now placed in the flow that can apply it.

- **A name-level lockstep guard, not a semantic one.** The three ends (the promise in
  `batch-consumption.md`, the procedure in `#handoff-seed`, the `batch` key in
  `handoff_seed.py`) must keep naming the same seam; anything stronger would test prose. This
  is the cheap regression guard against the exact drift class the incident produced.

## Risks / Trade-offs

- [A companion's scope genuinely belongs to a different route, but its `recommended-route`
  hint is absent] → it folds into the union; the union's classification and the run's normal
  review/PR loop judge the result, and the closing step can still release a companion whose
  scope did not land. Fail-open toward folding, matching the doc's "when in doubt, leave a
  candidate in the queue" only at claim time, where it belongs.
- [The primary's `batch:` stamp failed at claim time (best-effort)] → the seed sees no batch
  and the flow is the unchanged single-brief path; the companions keep their own
  `batch-primary:` stamps, so the dashboard/selfcheck still surface them. Not introduced here;
  not worsened.
- [A union grows the request text beyond what one brainstorm seed comfortably carries] →
  companions are capped at 3 by the claim-side scorer, and each contribution is labeled;
  overflow handling is out of scope rather than guessed at.
- [An agent skips the new prose (docs are not enforcement)] → the same exposure every
  procedure-bearing skill has; the unit/e2e tests pin the code half, and the lockstep guard
  keeps the two halves named consistently.

## Migration Plan

None. Additive seed key, prose steps, and tests; no configuration, schema, journal, or data
migration, and no existing command's interface changes. Rollback is a code revert.
