# Investigation: does the PR #329 forward-reference anti-pattern appear in the other three SKILL.md docs?

**Triggered by:** work-queue brief `20260812-155139`, a follow-up to worktrail-go PR #329
(`fix(go): classify invocation before fetching/printing the dashboard`, merged 82fb955).

**Question:** PR #329 fixed a structural bug in `skills/worktrail-go/SKILL.md`: Phase 1
described a mandatory pre-condition check ("determine this here, before Phase 1b runs") but
wrote the actual command and match semantics out only in Phase 2, reached via a forward
reference ("see Phase 2's Bare or prefix brief ID rule for the exact command"). Phase 1 also
contained a default action ("print `$DASHBOARD_JSON.rendered` verbatim") that a top-to-bottom
read executes before ever reaching Phase 2, so the check was reliably skipped — reproduced
twice against a definitive handoff id. Does the same shape (mandatory check + forward
reference + an earlier default action that fires first) appear in `skills/worktrail-handoff/
SKILL.md`, `skills/worktrail-sdd-workflow/SKILL.md`, or `skills/worktrail-help/SKILL.md`?

## Verified Observations

Read each of the three skill docs top-to-bottom as a fresh agent would, then grepped each for
the literal idiom family PR #329 used (`see Phase`, `see Step`, `for the exact command`,
`before Phase`, `before Step`, `must happen before`, `later section`, `deferred to`) and for
every `must`/`before` occurrence individually, cross-checking each hit's surrounding text for
whether the referenced command was actually inlined at the point of execution.

- **`worktrail-help/SKILL.md`** (53 lines): a pure command reference with no phases, steps, or
  conditional flow — just a table of accepted invocation forms. There is no mandatory
  pre-condition logic of any kind for the anti-pattern to attach to.
- **`worktrail-handoff/SKILL.md`** (223 lines): both the `new` and `consume` workflows are
  strictly sequential, numbered steps (Step 1 → 2 → 3 → 4), and every step that states a
  requirement gives the actual command in the same step (e.g. Step 2's `worktrail-work-queue
  claim` command sits directly under the sentence describing when to run it). No step points
  forward to a later step for its own operative command.
- **`worktrail-sdd-workflow/SKILL.md`** (274 lines): the file's own phase numbering jumps from
  Phase 1 straight to Phase 6 by design — Phases 2-5 (repo resolution, policy load,
  classification, collision guard) are `/go`'s responsibility and already complete before this
  skill is ever dispatched (confirmed against `subagent-prompts.md`'s `#seeded-dispatch`
  section: "The subprocess skips dashboard, picker, repo resolution, policy load, and
  classification"). This is intentional shared numbering with `worktrail-go`'s own phases, not
  a gap. Phase 1's three entry-path branches (seeded-dispatch / handoff-seed / direct-intent)
  are mutually exclusive with no competing default action that fires before a forward-referenced
  check — unlike PR #329's Phase 1, which combined a default print action with a deferred
  check in the same phase. Every `must`/`before` occurrence in Phases 6-8 (scope-completeness
  gate, mandatory pre-PR test gate, review-thread gate) gives its command in the same paragraph
  that states the requirement.

Checked each file's `references/` directory for the same idiom (though the brief's scope is
the three SKILL.md files specifically): `worktrail-handoff/references/handoff-template.md` is a
field-rules document with no procedural flow; `worktrail-sdd-workflow/references/
pipeline-details.md` load-bearing cross-references (e.g. `#new-pipeline`) point to a single
canonical procedure definition rather than duplicating a check's command inline elsewhere and
then deferring to it — the same accepted pattern `routes.md` itself uses for per-route
playbooks, not the same shape as PR #329's bug.

## Unknowns / Missing Evidence

None — this is a bounded, fully-read audit of three short documents plus their SKILL.md-adjacent
references directories. Every file in scope was read in full.

## Hypotheses

None remaining.

## Confirmed Root Cause

Not applicable — no defect found. The distinguishing shape of PR #329's bug (a phase states a
mandatory check "before X happens," defers the check's actual command to a later-numbered phase
via a forward reference, and also contains a default action that executes regardless and
textually precedes the referenced later phase) does not occur in `worktrail-handoff/SKILL.md`,
`worktrail-sdd-workflow/SKILL.md`, or `worktrail-help/SKILL.md`. Cross-file references that do
exist in these docs (e.g. `sdd-workflow`'s `#handoff-seed`/`#seeded-dispatch` pointers into
`subagent-prompts.md`, routes.md's per-route playbook citations) are each the sole definition of
that procedure — not a duplicate-then-deferred command — and none compete with an earlier
default action the way PR #329's Phase 1 did.

## Recommended Next Route

None — no code or doc change is warranted from this audit's findings.

Completion: `investigation_complete`.
