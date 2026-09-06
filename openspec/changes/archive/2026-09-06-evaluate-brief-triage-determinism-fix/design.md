## Context

`rank_change_candidates()` (`src/worktrail/workqueue/queue_triage.py:769-830`) offers an
evaluator candidate target changes purely by token-overlap coefficient between a brief's focus
text and a candidate's `proposal.md`/`tasks.md` vocabulary. `_has_valid_target()`
(`queue_triage.py:1339-1362`) then only checks that a `fold-into-change` verdict's
`target_change` is one of those presented candidates — it never checks whether the evaluator
actually read that candidate's content before picking it. A candidate can clear the
token-overlap score floor (`_MIN_CANDIDATE_SCORE`) by vocabulary coincidence alone, and nothing
downstream distinguishes that from a genuinely-matched target. The reported incident (two
identical evaluator runs against the same brief+repo, one falling through to `work-directly`,
the other accepting a coincidentally-scored candidate as a fold target) is this gap surfacing:
the evaluator's own judgment is the only thing standing between "read the candidate and
confirmed it fits" and "the candidate happened to score high enough," and that judgment is not
consistent run to run.

This spec already has a solved analogous problem for a different verdict type. `needs-update`'s
`refuted_span` (added by a prior change to this same capability) forces the evaluator to commit
to a verbatim, checkable quote from the brief's own focus text, and
`_needs_update_is_mechanical()` (`queue_triage.py:1701-1714`) re-verifies that quote against
live state at apply time — a stale or fabricated quote fails safe into a filed decision rather
than being trusted. `fold-into-change` has no equivalent commitment device.

## Goals / Non-Goals

**Goals:**
- Force a `fold-into-change` verdict to demonstrate the evaluator read the target change's own
  content, not just its score, before the verdict is accepted.
- Re-verify that demonstration against live on-disk state at apply time, the same way
  `refuted_span` is re-verified, so a fabricated-at-evaluation-time quote (or a target whose
  content has since changed) cannot reach an unattended pull request.
- Reuse the existing minimum-quote-length floor (`_MIN_REFUTED_SPAN_LEN`, 12 characters) rather
  than inventing a new threshold with no evidence behind it.

**Non-Goals:**
- Making the evaluator itself deterministic. It remains an LLM judgment call; this change
  narrows the specific failure mode the incident exposed (accepting a coincidentally-scored,
  unread candidate) rather than eliminating all evaluator variance.
- Changing `rank_change_candidates()`'s scoring or `_MIN_CANDIDATE_SCORE` threshold — the
  candidate list itself is unchanged; only what it takes to *accept and apply* a fold against
  one of those candidates changes.
- Re-ranking or re-scoring candidates at apply time. Apply-time work stays scoped to verifying
  the one quote the verdict already committed to, mirroring `refuted_span`'s scope exactly.

## Decisions

### Add `target_quote`, verified twice, instead of re-scoring at apply time

Considered re-running `rank_change_candidates()` at apply time and refusing the fold if the
target's score dropped. Rejected: score is a continuous, tunable-threshold signal, not a
yes/no fact — a fold that was borderline-valid at evaluation time could flip on an unrelated
scoring change, and this reintroduces exactly the "coincidental score" problem this change is
trying to close, just moved to apply time instead of removed.

Instead, `target_quote` makes the evaluator's claim binary and checkable: either the exact
substring it cited is present in the target's current content, or it isn't. This is the same
shape as `refuted_span`, which this spec already trusts for `needs-update`, so implementing it
means extending an established pattern rather than adding a new verification mechanism:

- **Evaluate time** (`_has_valid_target()`): structural check only — `target_quote` present and
  at least 12 characters. No file access is available or needed here; a quote that is present
  but wrong is still better than an absent one, since it is falsifiable at apply time, whereas
  an absent quote can never be checked at all.
- **Apply time** (`_apply_fold_into_change()`'s `prepare()` callback, which already reads the
  target's `proposal.md`/`tasks.md` before editing them): re-check `target_quote in
  proposal_text` or `target_quote in tasks_text` against the freshly checked-out worktree
  content. A miss returns an error string from `prepare()`, which `_worktree_pr_close()` already
  treats as a fail-closed abort before any commit, push, or PR — no new failure path needs to be
  built, only a new precondition added to the callback that already exists.

### Quote must come from the target, not the brief

`target_quote` is defined as coming from the *candidate's* `proposal.md`/`tasks.md`, not the
brief's own focus text (unlike `refuted_span`, which is a span of the brief's text). The prompt
states this explicitly and apply-time verification checks it against the target change's files
only — a quote that happens to also appear in the brief's focus text (e.g. because the brief
itself echoes generic vocabulary) still has to be found in the target's own files, or it fails.
This is the point of the check: it is meant to catch exactly the failure mode where the
evaluator matched on shared vocabulary without reading the target change's actual, specific
content.

## Migration

None — this only tightens what future `fold-into-change` verdicts must carry. No existing
verdict files or applied changes are affected; a verdict file produced by a not-yet-updated
evaluator that lacks `target_quote` is simply downgraded to `keep` by `_has_valid_target()`,
the same fail-closed behavior an invalid `target_change` already gets.
