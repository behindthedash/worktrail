## Context

See proposal.md — Why for the defect and the live evidence (worktrail PR #958).

Two facts shape the approach:

- `Verifier._wait_for_external_merge()` (`src/worktrail/orchestrator/verify.py:1271-1291`)
  is reached from exactly one caller, `_recheck_merged_before_quarantine` (same file,
  ~line 1319), and only on the branch where the live recheck already found
  `autoMergeRequest` set. Its return is passed straight through as that method's
  boolean: `True` = "merged after all, do not quarantine", `False` = "quarantine".
- The sibling `_block_on_checks` (`verify.py:1124-1153`) already establishes the exact
  classification idiom needed:
  `classify_checks(st.get("statusCheckRollup"), required=self._required_check_names())`
  returning `(any_pending, [failing names])`. `classify_checks` (`verify.py:267-304`)
  already filters informational checks and already treats a required check absent from
  the rollup as pending, so an empty/partial rollup cannot be misread as terminal.
  `_required_check_names()` is memoized per Verifier, so calling it inside the poll loop
  costs nothing after the first call.

Because both the classifier and the required-names source are already in hand and
already correct, this is a small in-loop addition rather than a new mechanism.

## Goals / Non-Goals

**Goals:**

- End the bounded external-merge wait as soon as the required checks are terminal-failed,
  since the armed auto-merge provably cannot complete from that state without a new
  commit this run will not push.
- Make each poll of the wait visible in the run log.

**Non-Goals (design-level, beyond proposal scope):**

- No new abstraction shared between `_block_on_checks` and `_wait_for_external_merge`.
  The two loops read the same classifier but have different exit semantics (green-wins
  vs. merged-wins) and different return shapes (`tuple[bool, list[str] | None]` vs.
  `tuple[bool, str]`); factoring them together would be a larger refactor than the
  defect warrants.
- No new policy key, setting, or opt-out for the early exit. The bail-out fires only in
  a state whose eventual outcome was already fixed at `False`, so there is nothing for a
  user to want to configure.
- No caching or diffing of check state across polls. Each poll classifies the rollup it
  just fetched; the wait already re-fetches `pr_status` every iteration.

## Decisions

**Classify after the MERGED test, not before.** A PR can merge on the same poll in which
its rollup still shows a failed check (e.g. a check that failed on an earlier head, or
an admin merge). MERGED is the authoritative, terminal-positive signal; testing it first
preserves today's behavior in that race and keeps the bail-out strictly subordinate to
it. Alternative — classify first and bail — was rejected because it could turn an
actually-merged PR into a quarantine.

**Bail only on `not pending and failing`.** This is the one state from which the armed
auto-merge cannot proceed. Any `pending` (including "some failing, some still running" —
a re-run can still turn the failure green) keeps waiting exactly as today. `not pending
and not failing` (all green, merge imminent) also keeps waiting — the merge is expected
momentarily, and bailing there would reintroduce the very premature-quarantine bug this
whole capability exists to prevent. Alternative — bail whenever `failing` is non-empty —
was rejected for that reason.

**Reason string names the failing checks.** The returned reason is what
`_recheck_merged_before_quarantine`'s caller records as the quarantine reason, so naming
the checks is what turns "we gave up" into an actionable verdict, and it is what the
delta spec's scenario asserts. Matches how `_block_on_checks` surfaces `failing` upward.

**Log line mirrors `_block_on_checks`'s existing pattern** — `    [{group['name']}] ...
(poll {poll + 1})` — rather than inventing a format. The run log is read by humans
scanning for a group name; a second format for the same kind of wait would be noise.

**Reuse `_required_check_names()` rather than rollup-only classification.** Passing
`required=` is what makes a not-yet-reported required check count as pending. Omitting
it would let a PR whose required workflow has not yet been scheduled — but which has one
unrelated failed check — read as terminal and quarantine early. Its `None` return (query
failed / `gh_repo` unresolved) already degrades to rollup-only classification, the same
posture `_block_on_checks` accepts.

## Risks / Trade-offs

- **A failed required check that a repo's automation later re-runs to green would now be
  quarantined instead of waited out.** → Accepted, and narrow: GitHub's armed auto-merge
  does not itself re-run checks, and a re-run that has started shows the check as pending
  again, which keeps the wait alive. The residual window is a re-run queued externally
  after this run's poll observes the terminal state; quarantine leaves the worktree and
  branch intact, so that case is recoverable rather than lost.
- **Per-poll logging adds up to `max_polls` (360) lines per waiting group.** → Accepted:
  it is one line per poll on a loop whose interval grows toward `poll_interval_max`, and
  the whole point of the change is that a silent multi-hour wait is indistinguishable
  from a hang.

## Migration Plan

None. Behavior-only change inside one method, no persisted state, no config. Rollback is
reverting the commit.
