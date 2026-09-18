## Why

`has_unresolved_decision()` (`src/worktrail/workqueue/queue_triage.py:452-462`) holds a brief
out of evaluation while its `awaiting-decision` link is `open` **or** `answered`, but
`consume_repo_decision()` (`:756-820`) only ever consumes an answered decision that names a
repo: the canonical repo-assignment question, or a free-form answer carrying a re-home
directive (`_REHOME_DIRECTIVE_RE`). Any other answered decision — "keep under worktrail,
contract change", "proceed", "yes, keep it" — returns `None`, is never resolved, and the brief
stays held forever. Meanwhile the single-brief gate (`worktrail-go <brief-id>` →
`--evaluate-brief-triage`) skips that hold entirely and, seeing no consumed answer, lets the
evaluator re-file the same question as a fresh decision. Verified 2026-09-18 on brief
`20260917-200826-preflight-gate-ignores-git`: decision `…91ad9673b571` was answered "keep under
worktrail, contract change", yet the single-brief path filed `…083c7c7a45b8` asking the
identical question. Source: work-queue brief
`20260918-073506-queue-triage-ignores-answered-decision`.

The archived change `queue-triage-consume-proceed-as-scoped-decision` only handles the
canonical question's "Proceed with the brief as currently scoped" option (and its
implementation never landed — its 1.1 was closed as stale bookkeeping in #1229). The active
change `rehome-directive-rescope-verb-coverage` only widens the re-home verb set. Neither
covers a free-form keep/proceed answer.

## What Changes

- **Answered guidance is consumed.** During inventory, an answered decision that
  `consume_repo_decision()` declines (not the canonical question, no re-home directive) is
  consumed as *answered guidance*: a `verdict: decision-answered` triage note records the
  decision id, question, and answer; the decision is archived (clearing the brief's
  `awaiting-decision` link); `repo:` and grouping are untouched; the brief proceeds to
  evaluation in the same run. The note does not count toward the recent-triage dedup window.
  A directive naming an unknown repo, and an unresolvable canonical answer, stay untouched as
  today (a human correction must still be able to act on them).
- **The evaluator sees the answer.** Each brief's prompt line carries its most recent
  answered decision (question + answer) as settled human guidance, with an explicit rule not
  to re-ask it via `needs-decision`.
- **The single-brief gate honours decisions.** `evaluate_single_brief()` runs the same
  decision-consumption pre-pass whether or not `--triage-repo` was passed, and refuses to
  evaluate a brief whose linked decision is still unresolved afterwards: `--evaluate-brief-triage`
  prints `null`, writes `blocked_pending_decision: <id> (<status>)` to stderr, and exits 2. The
  `worktrail-go` skill text documents this third non-zero exit alongside `blocked_no_capacity`.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `queue-triage`: the "Free-form repo re-home decisions are consumed" requirement no longer
  leaves a directive-less answer untouched; a new requirement defines answered-guidance
  consumption and its surfacing in the evaluator prompt.
- `intake-triage`: a new requirement makes the interactive single-brief pickup consume
  answered decisions and block on unresolved ones instead of re-filing.

## Impact

- `src/worktrail/workqueue/queue_triage.py` — new `consume_answered_guidance()` and
  `PendingDecision`; `group_queue_by_repo()`, `is_recently_triaged()`, `evaluate_group()`
  (prompt brief lines), `EVALUATOR_PROMPT_TEMPLATE`.
- `src/worktrail/router/skill_dispatch.py` — `evaluate_single_brief()` pre-pass and the
  `--evaluate-brief-triage` exit-2 branch.
- `skills/worktrail-go/SKILL.md` — the `blocked_pending_decision` exit case.
- `tests/workqueue/test_queue_triage_inventory.py`, `tests/router/test_skill_dispatch.py` —
  new scenarios.
- Touches the same `queue-triage` requirement text as the active change
  `rehome-directive-rescope-verb-coverage` (verb set only); whichever archives second must
  carry the other's edit.
