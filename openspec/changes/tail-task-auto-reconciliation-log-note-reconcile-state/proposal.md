## Why

A completed `full-real` run's console log can instruct the operator to reconcile evidence the
run itself already reconciled. `_format_unreconciled_tail_note` (live.py) renders every
`unreconciled_tail_evidence` finding under one fixed clause — "commits never merged onto base --
reconcile before worktree cleanup" — and annotates each entry's `reconcile_state` only as a bare
suffix. An entry whose reconciliation **merged** therefore reads as unmerged, in a line telling a
human to do work the journal already records as done.

Confirmed twice in the field, on two different triggers:

- Run `go-20261004-093132`, journal `run-model-tier-routing-env-profile-error-provenance.json`:
  tail PR #1433 squash-merged task 1.1's commit, so `c892d718` is not an ancestor of base — and
  the same run's log printed `!! 1 tail task(s) completed with unreconciled evidence (commits
  never merged onto base ...): 1.1 (sha c892d718 ... reconcile=merged)`. The entry carries
  `reconcile_state: merged` and `reconcile_pr_url: PR #1433`.
- Run `full-1791132864` (PR #1429), journal
  `run-pr-landing-pipeline-run-record-failure-detail.json`: tail task 2.1 was verification-only
  and its branch already contained `origin/main`, so its diff against base was empty by
  construction. The log printed `MERGED [tail-2.1 ] -- empty diff vs main, but 2.1 already landed
  on it` and then immediately `!! 1 tail task(s) completed with unreconciled evidence ...: 2.1
  (sha dd8ea73d ... reconcile=merged)`.

Neither run was blocked: both completed `rc=0` and their changes landed. The cost is a false
instruction at the moment an operator reads the run's last line — the one place a "!!" line is
taken at face value.

The behavior is already specified. `openspec/specs/tail-task-auto-reconciliation/spec.md`
requires that the findings be enriched with each attempt's outcome and that the finding message
reflect that outcome "instead of a fixed instruction to reconcile manually". The dashboard-facing
consumer honors it (`journal_selfcheck.check_repo` skips `merged` outright and splits the rest
into manual triage vs. awaiting-merge); the run-complete console consumer never implemented it,
and has no test coverage at all (`rg '_format_unreconciled_tail_note' tests/` is empty).

## What Changes

- **Partition the run-complete note by recorded outcome instead of rendering one fixed clause
  over every finding.** `merged` findings reach base; `opened`/`already-open` findings have a
  reconciliation PR awaiting merge; `superseded` findings ride a descendant's PR; anything else
  (`quarantined`, or no state on a pre-reconciliation journal) is genuinely manual. The partition
  is the one the sibling dashboard consumer already applies to the same journal key.
- **Stop asserting "commits never merged onto base" over findings that merged.** When every
  finding is `merged`, the note emits nothing — the journal keeps the findings as history, and no
  consumer anywhere still reports them as a problem. When they are mixed, only the genuinely
  outstanding findings appear in a message, described according to their own outcome.
- **Keep the manual-triage clause exactly where it applies.** A `quarantined` finding must keep
  the `!!`-prefixed "reconcile before worktree cleanup" instruction it has today; this change
  narrows that clause to the findings it is true of. It does not weaken the warning.
- **Regression coverage for a formatter that had none**, at both the unit and the
  `_pipeline_scheduler` call-site level, on the exact shapes observed in the field (squash-merged
  tail commit; verification-only tail task whose branch already contained base).
- **Non-goals:** changing what is detected or how reconciliation works; changing the journal's
  keys, shape, or retention; changing `journal_selfcheck.check_repo`'s dashboard finding text
  (already conformant); suppressing the `!!` warning for `quarantined` or open-PR findings;
  unifying the two consumers behind a shared helper.

## Capabilities

### New Capabilities

<!-- None: this change fixes behavior already governed by an existing capability. -->

### Modified Capabilities

- `tail-task-auto-reconciliation`: the "Reconciliation outcome is recorded and reported"
  requirement currently obliges only the *dashboard-facing* finding message to reflect the
  recorded outcome. It is widened to every finding message derived from
  `unreconciled_tail_evidence` — including the run-complete console note — and states explicitly
  what a `merged` outcome owes its readers: no assertion that its commits never merged onto base,
  and no instruction to reconcile it.

## Impact

- `src/worktrail/orchestrator/live.py` — `_format_unreconciled_tail_note` (the note), and its
  single call site in `_pipeline_scheduler` (line-per-line emission, so a two-group note carries a
  timestamp on each line).
- `tests/orchestrator/` — new unit coverage for the formatter, plus a call-site assertion in the
  existing `test_live_tail_reconciliation.py`, whose fixtures already drive `_pipeline_scheduler`
  with an injected fake spawn and verifier.
- No new console script, dependency, policy key, journal key, or spec-format change.
  `journal_selfcheck.check_repo` and `integrate.detect_unreconciled_evidence` /
  `reconcile_unreconciled_tail_evidence` are unchanged.
