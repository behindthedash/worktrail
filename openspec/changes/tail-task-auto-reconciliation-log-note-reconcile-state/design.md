## Context

See proposal.md — Why. The one consumer in scope is `_format_unreconciled_tail_note`
(`src/worktrail/orchestrator/live.py:727`), called once, from `_pipeline_scheduler`
(`live.py:6935`), on the value `reconcile_unreconciled_tail_evidence` returns —
a list of the original `{task, worktree, head_sha}` findings each extended with
`reconcile_state` (one of `opened`, `already-open`, `merged`, `quarantined`,
`superseded`) and `reconcile_pr_url` (`integrate.py:2109` docstring; enrichment at
`integrate.py:2309`). A finding whose state is `superseded` also carries
`reconcile_superseded_by`.

Two facts about that call site shape the design:

- The findings reaching the note are **already enriched** — live.py only records and formats
  the value reconciliation returned, never a raw detection list — so a formatter that keys on
  `reconcile_state` is reading the field the spec obliges the pipeline to set, not inferring it.
- The call site prints with a single `print(f"{_ts()} {note}")`, so the note's return shape
  decides how many lines carry a timestamp.

The sibling consumer of the same journal key, `journal_selfcheck.check_repo()`
(`src/worktrail/router/journal_selfcheck.py:192`), already implements the correct reading: it
`continue`s past `merged`, groups `opened`/`already-open`/`superseded` as *reconciling*, and
falls everything else into *manual triage*. Its comment calls the `merged` skip
"belt-and-suspenders: detect_ already stops reporting these" — that assumption is false for this
path (`detect_unreconciled_evidence` never consults `reconcile_state`; it returns raw findings),
which is why the console consumer sees `merged` findings at all.

## Goals / Non-Goals

**Goals:**

- The run-complete note never asserts an unmerged-commit condition about a finding whose recorded
  outcome is `merged`, and never instructs a reader to reconcile one.
- A `quarantined` (or outcome-less) finding keeps exactly the warning it has today, with the same
  `!!` prefix and the same "reconcile before worktree cleanup" instruction.
- The change is confined to the formatter and its one call site.

**Non-Goals:**

- Changing detection, reconciliation, or the set of findings that reach the note.
- Changing the journal's keys, shape, or retention.
- Changing `journal_selfcheck.check_repo`'s dashboard finding text — it is already conformant.
- Extracting a shared partition helper for the two consumers (see Decisions).
- Suppressing the warning for `opened`/`already-open`/`superseded` findings — those still have an
  unresolved delivery (a PR awaiting merge), so they are reported, just not as manual work.

## Decisions

**D1 — Partition the findings by `reconcile_state`, in the same three-way shape the sibling
consumer uses.** `merged` → nothing outstanding; `opened` / `already-open` / `superseded` →
awaiting an open PR; everything else (`quarantined`, or an absent state on a
pre-reconciliation journal) → manual. *Alternative:* keep one line and only reword its leading
clause when all findings are merged. Rejected because the clause is not the only wrong part —
the `opened` case is equally mis-described by "commits never merged onto base — reconcile before
worktree cleanup", and a single-clause rewrite cannot describe a mixed list honestly.

**D2 — When every finding is `merged`, the note emits nothing (`None`, the existing empty-list
return).** The journal keeps the findings as history, and after this change no consumer reports
them as a problem — matching the dashboard consumer, which already skips them.
*Alternative:* emit a non-`!!` informational line naming worktree cleanup as the only remaining
action. Rejected as speculative: the merged outcome means base already carries the change, so
deleting the tail worktree loses nothing, and the line would add a second, weaker meaning for
"unreconciled" at the one place operators currently read a single strong one.

**D3 — A non-`merged` note stays `!!`-prefixed, split into at most two lines** (manual first,
awaiting-merge second). The call site changes from one `print` to a `splitlines()` loop so each
line carries its own timestamp; leaving the second line untimestamped under a mid-run log would
make it read as a continuation of unrelated output. *Alternative:* renumber into a single line
with two clauses. Rejected — the two groups need different verbs (reconcile vs. wait), and
clause-stacking makes the manual instruction harder to find, which is the one thing the line
exists for.

**D4 — An unknown or absent `reconcile_state` falls into the manual bucket.** The fail-safe
direction is to warn, never to hide: a state this formatter does not recognize must not silently
suppress the run's only signal that a task's commits never reached base.

**D5 — No shared partition helper with `journal_selfcheck`.** Both consumers partition the same
key, but the two produce different prose for different surfaces (a dashboard finding detail vs. a
one-line console note), live in different packages (`router/` vs. `orchestrator/`), and this
change's spec obligations are stated in terms of the messages, not the grouping. A shared
abstraction across module boundaries is a design decision in its own right and belongs in its own
change, not as a side effect of a one-function fix.

## Risks / Trade-offs

- **Suppressing the note for an all-`merged` run hides the fact that a tail worktree's branch is
  not an ancestor of base.** → The journal record is retained, and the merged state is precisely
  the statement that the equivalent change is already on base; the orchestrator's own
  `cleanup_group()` path and `journal_selfcheck` remain the readers for the leftover worktree.
- **A future `reconcile_state` value it does not know about would be reported as manual.** → This
  is the intended fail-safe (D4); over-warning is recoverable, under-warning is the defect this
  change fixes.
- **Two-line output changes the shape of a string the call site previously treated as one line.**
  → Handled at the call site in the same change (D3), and asserted by the call-site test in
  `tests/orchestrator/test_live_tail_reconciliation.py`.

## Open Questions

None.
