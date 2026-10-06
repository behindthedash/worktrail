## Why

Triage's `keep` verdict is the only honest outcome for a brief that is still valid but cannot
be started right now -- and it is the one verdict that leaves the brief in the automatic-pick
pool. `_apply_keep()` (`src/worktrail/workqueue/queue_triage.py:2567`) appends a
`## Triage <date>` note recording the keep streak and does nothing else to the brief: it keeps
its `kind` and its eligibility, so the next drain pass's `worktrail-go auto` considers it
again and spends a full headless implementation session re-deriving the same conclusion.

The frontmatter forms that currently exclude a brief from that pool are all narrower than
"this brief has an external blocker":

- `blocked-by` is a list of **queue-brief IDs**. `handoff-template.md:8-12` defines it as "the
  IDs of prerequisite briefs that must be `done`"; `work_queue._blocked_by_refs()`
  (`src/worktrail/workqueue/work_queue.py:816`) and `_dependency_diagnostics()` (`:825`)
  resolve each entry against `queue/`/`picked/`, and `auto-mode.md:69-74` documents the
  resulting skip reasons -- `blocked:malformed-dependency` and `blocked:ambiguous-dependency`.
  Every one of those classifies a dependency *reference*, never a free-text blocker.
- `next-check-after` is a **calendar date**. `_is_not_yet_due()`
  (`src/worktrail/workqueue/work_queue.py:861`) and `auto-mode.md:92-93` define the
  `not-yet-due` skip from that date alone.
- `awaiting-decision` is an **open human-decision record**
  (`_awaiting_decision_info()`, `src/worktrail/workqueue/work_queue.py:944`).

A brief blocked by something outside that vocabulary has no representation at all: a
prerequisite living in *another* repo's queue, an upstream pull request or branch that must
land first, an out-of-band operator action. `_blocked_skip_reason()`
(`src/worktrail/router/dashboard.py:2264`) can name a malformed or ambiguous reference, but a
brief blocked externally reports the bare, unexplained `blocked` -- and, because triage's
`keep` cannot record *why* the brief is not actionable, `auto_pick_brief()`
(`src/worktrail/router/dashboard.py:2282`) returns it again on the very next pass. `auto-mode.md`'s
"Skip reasons" enumeration has an entry for every other way a brief leaves the pool; this one
has none.

The five sibling queue-triage proposals under review --
`triage-verdict-file-collision-safety`, `quarantine-recovery-command`,
`brief-triage-capacity-block-exit-two`, `queue-triage-rejected-closure-recovery`,
`scheduled-classifier-false-positive-sweep` -- govern verdict-file safety, orchestrator-group
recovery, evaluator-capacity exit codes, rejected-closure recovery, and classifier false
positives respectively. None governs auto-pick eligibility.

## What Changes

- **A new optional `blocked-on:` frontmatter field names an external blocker in prose.** It is
  for a blocker that is not a queue-brief prerequisite and has no known date -- `blocked-by`
  takes queue-brief IDs only and `next-check-after` takes a date only. A brief carrying a
  non-empty `blocked-on:` is reported `blocked: True` by queue-listing data (so it leaves every
  ready count and the auto-pick eligible pool) and is skipped by automatic selection with the
  new structured reason `blocked:external`, which retains `blocked` as its coarse category so
  miss-log aggregation is unchanged. Like `blocked-by`, the field does not by itself refuse an
  explicit interactive claim, but the claim emits a warning naming the blocker, and both the
  human queue listing and the malformed/ambiguous repair warnings surface it.
- **Triage's `keep` verdict can record the blocker.** `Verdict` gains an optional
  `blocked_on`; the evaluator prompt instructs the evaluator to set it only when the brief is
  valid but externally blocked, to echo the brief's current `blocked-on:` value when the
  blocker is still unresolved, and to return an empty string only with evidence it has
  resolved. Applying a `keep` reconciles the brief's `blocked-on:` field to the verdict
  (non-empty writes, empty clears, absent leaves it unchanged -- fail-open toward *remaining*
  blocked, the cheap direction), and an accepted `work-directly` clears it, since a brief
  being seeded for direct execution is no longer blocked.
- **A new `worktrail-work-queue unblock <id>` command clears the field.** It is the operator's
  clear path for a blocker resolved out of band, and it is idempotent.
- The `worktrail-handoff` brief-format reference and the `worktrail-go` auto-mode reference
  document the field and the skip reason.

## Capabilities

### New Capabilities

- `work-queue-external-blocker`: an optional prose frontmatter field naming a blocker that is
  not a queue-brief prerequisite, its queue-listing/automatic-selection semantics, the
  operator output and claim warning that name it, and the command that clears it.

### Modified Capabilities

- `intake-triage`: a `keep` verdict may record an external blocker (the optional `blocked_on`
  verdict field, the evaluator-prompt rule for when to set, preserve, or clear it, and the
  apply-time reconciliation of the brief's `blocked-on:` field), so a brief that is valid but
  externally blocked can stay out of the automatic-pick pool while it waits.

## Impact

- `src/worktrail/workqueue/work_queue.py` -- `list_queue()`'s per-brief `blocked` flag and a
  new `blocked_on` key, the claim warning, the human listing, and the new `unblock` subcommand
  and its handler.
- `src/worktrail/router/dashboard.py` -- `_blocked_skip_reason()` gains the
  `blocked:external` qualifier (below the two dependency-reference qualifiers).
- `src/worktrail/workqueue/queue_triage.py` -- `Verdict.blocked_on`, its parse site in
  `parse_verdicts()`, the `EVALUATOR_PROMPT_TEMPLATE` rule, `_apply_keep()`'s reconciliation,
  `_apply_work_directly()`'s clear-on-seed, and the keep preview entry.
- `skills/worktrail-handoff/references/handoff-template.md` and
  `skills/worktrail-go/references/auto-mode.md` -- the field and the skip reason.
- Tests: `tests/workqueue/test_work_queue.py`, `tests/router/test_dashboard.py`, and
  `tests/workqueue/test_queue_triage.py`.
