## Context

The work queue has exactly one terminal brief status today: `done`. `done()`
(`work_queue.py:1441-1691`) stamps `{"status": "done", "completed-at": ...}`
for every closure mode it supports — `--planning-only`,
`--implementation-complete`, and the triage closures (`--triaged`,
`--triaged-to`, `--duplicate-of`). The mode only varies which *gates* apply
and which extra frontmatter field is written (`triaged-to:`,
`duplicate-of:`).

`consolidate_cluster.execute_consolidation()` folds N briefs into one
wrapper. Per member it: claims it, appends `## Superseded / Consolidated
into <new id>` to the body (`_stamp_superseded`), then calls
`work_queue.py done <id> --planning-only --json` (`_mark_member_done`) —
optionally with a `--note` when the member is itself a nested consolidation
batch, purely to satisfy `done()`'s consolidation-evidence gate.

So the on-disk record of a folded brief and a shipped brief differ only by a
prose footer in the body. Every consumer that asks "is this brief closed?"
asks `fm.get("status") == "done"` and gets `True` for both.

## Goals / Non-Goals

**Goals:**
- Make "absorbed into another brief" a distinct, machine-readable terminal
  status, so a later audit can tell folded briefs from shipped ones without
  parsing prose.
- Keep folded briefs terminal: they must not re-enter dependency, scoring,
  or dedup lookups as open work.
- Route the existing fold path through the new status with no other
  behavior change.

**Non-Goals:**
- Requiring shipping evidence at fold time. The existing gate's own
  reasoning (`_consolidation_closure_missing_evidence` docstring) is that
  gating the authoring-time stamp "would only teach the closer to paste
  ignorable evidence" — the fix is an honest status, not a fake gate.
- Migrating or rewriting the briefs already stamped `done` by past folds.
- Changing `queue_triage.py`'s `fold-into-change` apply action, which
  already closes through the `triaged_to` triage closure and stamps
  `triaged-to:` provenance.
- Any new `work_queue.py` subcommand — this is a mode on the existing
  `done`.

## Decisions

### Decision 1: A new `superseded_by` closure mode on `done()`, not a new subcommand

`done()` already models "closed for a reason other than shipping" via
`triaged`/`triaged_to`/`duplicate_of`. `superseded_by` joins that family:
mutually exclusive with `--planning-only`/`--implementation-complete` at the
CLI, counted as a triage-style closure for the Route-C gate, and waiving the
consolidation-evidence gate on the same grounds `duplicate_of` already does
(the sub-items are carried by the surviving brief, not shipped here).

*Alternative rejected:* a separate `supersede` subcommand. It would have to
duplicate `done()`'s resolve/ownership/backup/write-verify pipeline for no
behavioral gain.

### Decision 2: Stamp `status: superseded`, not `status: done` plus a marker field

Writing `status: done` with a `superseded-by:` field would leave every
existing `status == "done"` consumer reading a fold as shipped work — the
exact defect. The status value itself must differ, and `superseded-by:`
carries the provenance alongside it.

The cost is that `superseded` must be added wherever `done` means terminal.
Those sites are enumerated and finite: `_related_still_open`
(`work_queue.py:1431`), the dependency-reference `state` computation
(`work_queue.py:783`), `score_candidates.py:198`, and
`spec_sync_sweep_dedup.py:66`. `dashboard.py`'s `done` constants describe
*task* stages, not brief statuses, and are untouched.

`done()`'s own return value becomes `{"status": "superseded"}` for this
mode, so callers can tell which closure happened; `superseded` is added to
the CLI's success-status set and to `_BACKUP_ON` so a fold is still backed
up like any other disk mutation.

### Decision 3: The fold path passes the new brief's id as `--superseded-by`

`_mark_member_done` becomes `_mark_member_superseded(member_id, ...,
new_brief_id, note)` and calls `done <id> --superseded-by <new_brief_id>
--json`, accepting `{"status": "superseded"}` as success. The
`## Superseded` body footer stays (it is human-readable provenance the
frontmatter now mirrors), and the nested-consolidation `--note` stays too:
the consolidation gate no longer applies to this closure, but the note is
the only record of which sub-items a nested batch carried, so dropping it
would lose provenance.
