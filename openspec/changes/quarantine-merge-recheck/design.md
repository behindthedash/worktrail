## Context

`Verifier.verify_one` (`src/worktrail/orchestrator/verify.py:1300`) drives a group's PR through
four gated stages in order: `ensure_mergeable` (resolve `CONFLICTING`, bounded by
`self.max_strikes`) → `wait_and_fix_ci` (block on CI, spawn a ci-fix worker on red, bounded by
`self.max_strikes` strikes) → `resolve_review_threads` → `_merge_with_cumulative_gate` (which
calls `auto_merge`). The moment any stage returns `ok=False`, `verify_one` writes
`quarantined[name] = reason` (lines 1372-1388) and returns — no stage after the failing one runs,
and nothing re-observes the PR's live state before that write.

Each stage's `ok=False` is a verdict about *this run's own bounded polling*, not necessarily about
the PR's actual fate. `wait_and_fix_ci`'s final poll (`_block_on_checks`, itself bounded by
`self.max_polls` with adaptive backoff) can report "still failing" or "timed out" in the same
instant that CI is in fact catching up — and a repo can have its own external auto-merge
automation (`go-policy.yaml`'s `external_automerge` detection, e.g.
`.github/workflows/auto-merge.yml`) watching the same PR independently of this run's poll loop.
That automation does not share this run's budget and keeps acting after this run gives up.

Observed live 2026-08-12/13 on worktrail PR #339 (run `go-20260812-161537`): the 3rd (final)
ci-fix worker applied `go:no-version-bump`, CI went green, and GitHub Actions' own auto-merge
landed the PR at 02:10 — but `wait_and_fix_ci`'s own last poll had already reported failure and
`verify_one` had already recorded `QUARANTINED` moments earlier. The run's journal required hand
reconciliation (the group record patched from `QUARANTINED` to `MERGED`) before the run could
proceed to its held-out tail tasks.

The codebase already has the right primitive for exactly this situation:
`_wait_for_external_merge` (`verify.py:989`) polls a PR's live state (bounded by `self.max_polls`,
same adaptive backoff as `_block_on_checks`) until an externally-armed auto-merge completes. It is
only ever called today from inside `auto_merge()` (`verify.py:1086`), which is only reached once
every earlier stage has already returned `ok=True` — so a group that fails an *earlier* stage
(as in the PR #339 incident, which failed in `wait_and_fix_ci`) never reaches it. `pr_status`
(`verify.py:504`) is the other primitive already in use throughout this file for a one-shot live
`gh pr view`.

## Goals / Non-Goals

**Goals:**
- Before `verify_one` finalizes an *ordinary* quarantine verdict (a plain stage failure — not a
  confirmed self-merge violation, not a post-merge cumulative regression), make exactly one
  passive, last-chance check of the PR's live state.
- If the PR is already `MERGED`, record the group as merged (not quarantined) and run the normal
  merged-path cleanup.
- If the PR shows an auto-merge request armed by something other than this run, give it exactly
  one bounded wait via the existing `_wait_for_external_merge` helper before finalizing
  quarantine.
- Reuse the two primitives that already exist for this (`pr_status`, `_wait_for_external_merge`)
  rather than adding new polling machinery, new timers, or a new budget knob.

**Non-Goals:**
- Do not extend or restart `ensure_mergeable`/`wait_and_fix_ci`/`resolve_review_threads`'s own
  strike or poll budgets. The brief names this as an alternative approach ("extend the poll
  window/strike budget"); it was not chosen because it widens every run's worst-case latency
  (including runs with no external automation at all) to buy a benefit that only applies to repos
  with `external_automerge` configured. A one-shot recheck targets the actual gap precisely and
  costs nothing extra for repos without external automation (their PR is not `MERGED` and has no
  armed auto-merge request, so the recheck falls through immediately).
- Do not touch `self_merged`/`post_merge_regressed` handling. Both already have their own correct,
  more specific verdicts derived from a confirmed merge; a passive recheck must not reinterpret
  either.
- Do not have the recheck itself call `gh pr merge` or arm auto-merge. It only observes.

## Decisions

- **Recheck lives inside `verify_one`'s existing `if not ok:` branch, gated to the plain-failure
  case only.** The branch already distinguishes `is_regression` and `violation` from the ordinary
  case; the recheck is inserted as `elif`-equivalent logic that only runs when neither of those
  two more-specific classifications applies, so it can never override a self-merge violation or a
  post-merge regression verdict.
- **Reuse `_wait_for_external_merge` verbatim rather than writing a new poll.** It already has the
  correct bounded-adaptive-backoff shape and is already exercised by existing tests
  (`auto_merge`'s pre-armed-automerge path). A one-shot `pr_status` call decides which branch to
  take (already `MERGED` vs. armed-but-not-yet vs. neither) before deciding whether to invoke it.
- **On a plain non-merged, non-armed result, fall through to today's quarantine behavior
  unchanged**, including the original failure `reason` string — the recheck adds a possibility, it
  does not remove or reword the existing quarantine path.

## Risks / Trade-offs

- **One extra `gh pr view` call on every ordinary-quarantine path.** Bounded, cheap, and already
  retried internally by `pr_status` (`GH_RETRIES`); negligible against the multi-minute CI waits
  already on this path.
- **The bounded external-merge wait adds latency only when an auto-merge request is actually
  armed.** That is exactly the population this fix targets; a group with no external automation
  never enters that branch.
