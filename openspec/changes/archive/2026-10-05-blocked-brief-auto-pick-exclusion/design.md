## Context

Automatic selection (`dashboard.auto_pick_brief()`) skips a brief when its queue-listing entry
reports `blocked` or `not_yet_due`, and queue-listing data (`work_queue.list_queue()`) computes
those two flags from exactly three frontmatter shapes: an unsatisfied `blocked-by` reference
(`_dependency_diagnostics()`), an open decision (`_awaiting_decision_info()`), and a future
`next-check-after` date (`_is_not_yet_due()`). A brief blocked by anything else -- a
prerequisite tracked in another repo's queue, an upstream pull request that must land first, an
ops action -- has no flag, so `auto_pick_brief()` treats it as fully eligible.

Triage is where such a brief is correctly judged: its premise holds, it is not a duplicate, it
is not directly workable, so the verdict is `keep`. But `_apply_keep()`
(`src/worktrail/workqueue/queue_triage.py:2567`) appends a `## Triage <date>` note and nothing
else, so the brief stays in the eligible pool and the next drain pass claims it and spends a
full headless implementation session reaching the same conclusion.

See `proposal.md` for the full motivation. This document records the decisions behind the
field's shape, its relationship to the existing exclusion fields, and the apply-time
reconciliation.

## Goals / Non-Goals

**Goals:**

- A brief that is valid but externally blocked can be held out of the automatic-pick pool by
  the triage verdict that judged it blocked, until the blocker clears.
- Every existing exclusion mechanism (`blocked-by`, `next-check-after`, `awaiting-decision`)
  keeps its exact meaning; this adds a fourth, narrower one rather than overloading any of them.
- The block is never silently lost: it is preserved on an inconclusive re-evaluation, surfaced
  in operator output, and cleared only on evidence (an explicit clear) or an operator command.

**Non-Goals:**

- A free-text block on capture. `create_handoff.py` gains no `--blocked-on` flag; the field's
  producer is triage (which requires evidence) and its operator path is the clear command. A
  capturing agent with a known external blocker already has `--blocked-by` (for a brief) and
  can file it as context otherwise.
- Turning the field into a timer. It has no self-expiry; unlike `next-check-after` it is not
  "recheck automatically on a date" but "stay blocked until triage or an operator says
  otherwise". A brief whose blocker has a known recheck date should still use
  `next-check-after`.
- Blocking `claim`. The field removes a brief from *unattended* selection, exactly as
  `blocked-by` does; an explicit interactive claim succeeds (with a warning), because a human
  picking a held brief is making a deliberate call.
- Any change to the batch `queue_triage evaluate`→`apply` inventory or its dedup window: a
  `blocked-on:` brief is still triaged on the normal 25-day cadence, which is exactly how the
  block is re-examined.

## Decisions

### A new prose field rather than a reuse of `next-check-after`

The gap is precisely the case `next-check-after` cannot express: a blocker with no known date.
Every other exclusion field is typed against something the queue can resolve (`blocked-by`
against brief IDs, `awaiting-decision` against a decision record), so a prose field is the
honest shape. The rejected alternative -- have the evaluator invent a recheck date (e.g.
`today + 7d`) and reuse `next-check-after` -- couples two unrelated knobs: the triage dedup
window (25 days) is longer than a short recheck horizon, so the brief would return to the
auto-pick pool before triage re-examined it, and the same session-burning failure would recur
on schedule. An indefinite, evidence-cleared field does not have that race.

### A scalar, not a list

`blocked-by` is a list because a brief can have several *resolvable* prerequisites, each of
which the queue checks. An external blocker is a single situation described in prose; a list
of prose entries adds ordering and quoting surface (and a join/split question on the listing
side) for no resolution benefit. One non-empty line is the contract; the value is written
through the existing `_yaml_scalar` quoting path so a colon or leading punctuation is safe.

### The verdict field reconciles the brief's `blocked-on:`, with preserve-as-default

Applying a `keep` is the one site that writes the field from triage. The verdict's
`blocked_on` is tri-state so the fail-open direction is explicit and cheap:

- **non-empty** -- write it (replacing any existing value);
- **empty string** -- remove the field (the evaluator has evidence the blocker cleared);
- **absent** -- leave the brief's existing value alone.

The middle and last cases matter because the evaluator's tool budget is small and an external
condition is often not verifiable from the brief's own repo: "I could not confirm the pull
request merged" must never be read as "it merged". Preserving on absence means a brief stays
blocked until someone positively clears it -- and the mistake it prefers (a brief held too
long) is a queue that stalls on one brief, whereas the mistake it forbids (a brief unblocked
while still blocked) is the session-burning loop this change exists to remove. The prompt
therefore shows the brief's current `blocked-on:` and instructs the evaluator to echo it.

### `work-directly` clears the field when it seeds

`work-directly` converting a brief to an execution brief (`seeded-from:` stamped) is the one
verdict that says "this is directly actionable now". A stale `blocked-on:` left on a freshly
seeded brief would hold it out of automatic selection forever -- the seeded work could never
run. So the accepted-`work-directly` apply removes the field. No other verdict touches it:
`stale-close`/`duplicate-of`/`fold-into-change`/`propose-change` close or re-house the brief
(where the field is moot), and `needs-update`/`needs-decision` leave it, which is the safe
direction (a brief that may still be blocked stays blocked until triage says otherwise).

### The skip reason is `blocked:external`, below the dependency-reference reasons

`_blocked_skip_reason()` already qualifies the coarse `blocked` reason with the dependency
state, keeping `blocked` as the leading segment so `log_auto_pick_miss()`'s `:`-split
aggregation is unchanged. `blocked:external` follows that shape. Precedence is
malformed > ambiguous > external > bare `blocked`: a malformed or ambiguous `blocked-by`
reference is the strictly more broken value and is the one whose repair unblocks the brief, so
naming it is more actionable than naming the external blocker that may sit beside it.

### Clearing has an operator command

Every other exclusion field has an exit that does not require triage to run: `next-check-after`
expires, `recently-released` ages out, `blocked-by` is satisfied by a brief completing,
`awaiting-decision` ends when the decision is answered. An indefinite prose block has none, and
its resolution is observed by a human (the other repo's PR merged, the ops action happened),
often at a moment when triage is not due. `worktrail-work-queue unblock <id>` is that exit --
idempotent, touching only `blocked-on:` -- mirroring how `triage <id> clear` and
`release --next-check-after` give the other queue fields a command instead of a hand edit.

## Risks / Trade-offs

- [A brief is blocked forever because nobody clears it and triage keeps echoing the value] →
  the escalation path still runs: a brief due for escalation is verdicted by the matrix
  (`needs-decision`/`propose-change`/`work-directly`) rather than `keep`, so it surfaces to a
  human, and `unblock` is available at any time. A brief sitting blocked is the intended state
  while its blocker is real.
- [An evaluator sets `blocked_on` for something that is really a queue-brief prerequisite] →
  the prompt states the distinction (queue-brief prerequisites belong in the brief's own
  `blocked-by`); a prose value for a resolvable prerequisite is at worst a coarser block than
  `blocked-by` would give, never a wrong one.
- [An older installed skill text does not know the field] → the field is read by
  `work_queue`/`dashboard` regardless of who wrote it, and the evaluator prompt rule ships with
  the code; a brief already carrying `blocked-on:` is honored even if the running skill text
  predates it.
- [A `keep` that omits `blocked_on` for a brief that is no longer blocked leaves a stale
  block] → accepted: the remedy is the `unblock` command or the next triage run echoing an
  empty value; the opposite failure (clearing a real block) is what the preserve default
  forbids.

## Migration Plan

1. Land the queue-side field/listing/skip-reason/command and the triage-side verdict field,
   prompt rule, and apply reconciliation together -- the spec pins both halves and the batch
   pipeline is otherwise untouched.
2. Rollback is reverting the commit: the `Verdict` field defaults to `None` (no behavior
   change for verdicts that do not carry it), the new listing key is additive, and no existing
   brief is rewritten -- a brief without `blocked-on:` is unaffected.
