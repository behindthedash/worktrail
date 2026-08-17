## Context

See proposal.md - Why. `ci-watch-loop.md`'s "Waiting for checks" step and
case 1 ("All pass") of the classification step both call `gh` subcommands
that resolve through GitHub's GraphQL API (`gh pr checks`, `gh pr view
--json state,mergedAt,mergeStateStatus,statusCheckRollup,autoMergeRequest,
headRefOid`). The already-documented "Stuck check-run fallback" subsection
handles a *different* failure mode — a check-run's status silently going
stale while the API itself keeps answering — and is not affected by this
change. This change targets the case where the GraphQL API itself is
unreachable (HTTP 503 / GraphQL error body), which the existing retry-then-
stuck-fallback path does not detect or route around, because it assumes the
API answers (just possibly with stale data).

REST equivalents exist for the fields the loop actually consumes:
- `gh api repos/$OWNER/$REPO_NAME/commits/$HEAD_SHA/check-runs` for the
  per-check `name`/`status`/`conclusion` triples `gh pr checks` surfaces.
- `gh api repos/$OWNER/$REPO_NAME/pulls/$PR_NUM` for `state` (open/closed,
  not GraphQL's OPEN/MERGED/CLOSED enum), `merged_at`, `head.sha` (REST's
  `headRefOid`), and `auto_merge` (REST's `autoMergeRequest`, present on
  this same object — no extra call needed).

No REST equivalent exists for `mergeStateStatus` (branch-protection merge
eligibility) or for per-context `statusCheckRollup` history (needed by the
merge-state guard's CANCELLED/SUCCESS same-context pairing). Those stay
GraphQL-only; the design's job is to degrade the guards that depend on them
without blocking the parts of the loop that don't.

## Goals / Non-Goals

**Goals:**
- Give the loop's core waiting and case-1 classification steps a working
  path forward when GraphQL is down, using REST commands that hit a
  different API surface and are unaffected by a GraphQL-specific outage.
- Detect a GraphQL outage by its failure signature (command fails outright)
  rather than confusing it with a pending/slow check (command succeeds but
  reports non-terminal status) — those need different responses.
- Self-heal: prefer GraphQL every time the loop is (re-)entered, and only
  drop into the REST substitute for the specific call that is currently
  failing, so the loop returns to full-fidelity GraphQL data automatically
  once the outage clears rather than latching into a degraded mode.

**Non-Goals:**
- Replacing GraphQL as the default data source. REST lacks
  `mergeStateStatus` and per-context check history; using it unconditionally
  would silently weaken the case-1 merge-state guard (worktrail PR #393)
  every run, not just during an outage.
- Adding REST fallback to `worktrail-check-review-threads` (the review-
  thread gate's backing tool). That tool already has its own no-signal
  path (`checked: false`) for when it cannot answer, which this change
  reuses rather than duplicating with a second REST implementation.
- Building tooling/scripts. This is a procedural doc change only — no
  `src/worktrail/` code changes, consistent with PR #500's precedent for
  this same file.

## Decisions

**Outage detection: failure signature, not timeout.** The existing 3x
`--watch` retry-then-stuck-fallback path already handles "the API answers
but the check-run's status looks wrong/stale." A GraphQL outage looks
different at the `gh` CLI level: the command itself fails (non-zero exit,
stderr carrying an HTTP 5xx or a GraphQL error body) before it ever gets to
reporting per-check status. Route on that distinction: a failed command with
a 5xx/GraphQL-error signature enters the new REST-fallback path; a command
that succeeds but times out pending stays on the existing stuck-check-run
path. Alternative considered: treat every `--watch` timeout as a candidate
for both paths and let the agent disambiguate case-by-case — rejected
because it re-introduces the ambiguity the existing subsection was written
to remove (worktrail PR #498's incident was explicitly "the API keeps
answering, just with stale data" — conflating it with "the API stopped
answering" would blur two fixes with different correct actions).

**Bounded discrete retries, no sleep loop.** `gh api` has no `--watch`
equivalent for polling check-runs to completion. Rather than hand-rolling a
`sleep`-based poll loop — the exact pattern this file already forbids
("never a hand-rolled sleep loop", citing GO v1 defect L7: a foreground
`sleep 30` loop that both exceeded the Bash tool's timeout and violated this
same policy) — the REST substitute re-issues a single-shot
`gh api .../check-runs` call, bounded to the same small retry count already
used elsewhere in this file (3, matching the existing `--watch` retry cap),
with the agent's own turn cadence providing the spacing between attempts
instead of an explicit sleep. Alternative considered: build a bounded
`until`-loop-with-sleep inside one Bash call, staying under the tool's
600000ms timeout — technically survives the timeout defect L7 hit, but
still contradicts this file's own stated rule verbatim; rejected to keep
the rule unambiguous rather than carving out a same-file exception.

**Degrade the merge-state guard instead of skipping case-1 entirely.**
When REST fallback is active, `mergeStateStatus`/`autoMergeRequest`-fidelity
data may be unavailable (a full PR outage, not just the checks endpoint).
Treat that exactly like the review-thread gate's existing `checked: false`
branch: proceed on the REST-available signal (state/merged_at/head_sha,
and REST's own `auto_merge` field when the `pulls/$PR_NUM` call *does*
succeed) and record the reduced-fidelity guard as a note in the eventual
`--merge-result` text rather than blocking completion on data that a live
outage makes unobtainable. Alternative considered: block and escalate to
`blocked_product_decision` whenever GraphQL is down at case-1 time —
rejected as disproportionate; the whole point of this change is that a
transient infra outage should not stall an otherwise-clean merge.

**No new terminal `finish` status.** A GraphQL outage is transient
infrastructure, not a code defect (case 3) or a product decision (case 4).
Model it like case 2 (transient infrastructure failure): keep retrying
without incrementing `PATCH_ITER`, and only fall through to the existing
iteration-ceiling-style stop (`failed_recoverable`) if REST-fallback
retries are themselves exhausted — folding this into the existing ceiling
philosophy rather than inventing a fifth classification bucket that every
future reader of this file would also need to learn.

## Risks / Trade-offs

- [Reduced classification fidelity while GraphQL is down: no
  `mergeStateStatus`/`statusCheckRollup` CANCELLED-pairing detection] →
  Documented as a known, temporary degradation, recorded in the
  `--merge-result` text on completion, and self-healing on the next loop
  entry once GraphQL recovers (the loop always prefers GraphQL first).
- [A GraphQL outage that outlasts the bounded REST-fallback retries still
  leaves the loop with no path forward] → Falls through to the existing
  iteration-ceiling stop (`failed_recoverable`), which already gives a
  human/drain-pass an explicit, inspectable stopping point rather than a
  silent indefinite hang — same outcome the loop already relies on for
  unresolved code defects.
- [False-positive outage detection on an unrelated transient `gh` error]
  → Scope detection narrowly to HTTP 5xx / GraphQL-error response bodies
  (the actual PR #500 signature), not generic non-zero exits, so a
  one-off network blip or auth error doesn't spuriously divert into the
  REST path.

## Migration Plan

N/A — documentation-only change to an existing reference file, adopted the
next time an agent reads `ci-watch-loop.md`. No code deploy, no rollback
beyond reverting the file.
