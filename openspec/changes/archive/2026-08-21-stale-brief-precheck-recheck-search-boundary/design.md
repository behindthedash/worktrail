## Context

`check_brief_staleness.py`'s CLI path reads a brief's search boundary via `_read_brief()`
(`check_brief_staleness.py:744-778`), which currently prefers `original-created:` over
`created:` (added by the sibling change `stale-brief-precheck-consolidation-original-created`
to handle consolidated briefs, whose `created:` records consolidation time rather than true
capture time). Neither field ever changes across a brief's lifetime in the queue: `created:`
is stamped once at capture, and `original-created:` (when present) is stamped once at
consolidation. Both are therefore the wrong anchor for a brief that is deliberately released
back to the queue and rechecked on a cadence — `work_queue.py release` already stamps
`released-at:` on every such release (see brief `20260623-093000-datalena-deferred-dep-upgrades`'s
frontmatter, `released-at: '2026-08-12T02:35:29-07:00'`, updated on its 9th recheck), which is
exactly "when was this brief last looked at" — the correct anchor for "what's landed *since*".

See proposal.md for the motivating false-positive and the live incident (brief
`20260812-023701-check-brief-staleness-py-self`, run `go-20260812-022551`).

## Goals / Non-Goals

**Goals:**
- Stop a rechecked brief's own already-cited, already-resolved history from re-surfacing as
  staleness evidence on every subsequent recheck.
- Preserve today's behavior unchanged for a brief that has never been released back to the
  queue (no `released-at:` field) and for a consolidated brief's original-created:-preferred
  boundary until its first post-consolidation release.

**Non-Goals:**
- Changing `RACE_GRACE_SECONDS`, the grace-window computation, or CLI-flag probe extraction
  (PR #327) — unrelated, already-shipped work.
- Changing the auto-mode decision-filing behavior (PR #396) — this change reduces how often
  evidence is (falsely) surfaced in the first place; it does not change what happens once
  evidence is surfaced.
- Backfilling or normalizing `released-at:` onto briefs that predate this field's introduction
  — out of scope; a brief without `released-at:` simply keeps today's
  `original-created:`/`created:` behavior until it is next released.
- Touching `check()`'s own signature or `_normalize_since()` — both already accept an
  arbitrary `since: Any` and normalize whatever they're given; only the value `_read_brief()`
  selects changes.

## Decisions

**Precedence order: `released-at:` > `original-created:` > `created:`.** `released-at:` is
the most recent and most relevant anchor when present — it records the last time a human or
an automated recheck actually looked at this brief, which is precisely "since when should new
evidence count". `original-created:` remains the correct anchor for a consolidated brief that
has not yet been released post-consolidation (it still needs to catch pre-consolidation
history a naive `created:` would exclude). `created:` remains the final fallback for a brief
that has never been consolidated or released. This is a strict linear precedence, not a merge
of the three — using the single most-informative timestamp available keeps the search
boundary computation simple and matches the existing `original-created:` > `created:` pattern
this extends.

**Read `released-at:` the same way `original-created:` is already read.** `_read_brief()`
already calls `read_frontmatter()` and both `check()` and `_normalize_since()` accept either a
quoted-string or a native-PyYAML-`datetime` value transparently — `released-at:` needs no new
parsing path, only one more `fm.get(...)` in the existing precedence chain:
`fm.get("released-at") or fm.get("original-created") or fm.get("created")`.

**No change to `work_queue.py`'s `release` command.** It already stamps `released-at:` on
every release (verified live on brief `20260623-093000-datalena-deferred-dep-upgrades`); this
change only teaches `check_brief_staleness.py` to read a field that already exists on disk.

## Risks / Trade-offs

**[Risk] A brief released very recently (inside `RACE_GRACE_SECONDS` of a delivering commit
merged just after its previous recheck) could theoretically miss that commit if the grace
window is computed from `released-at:` rather than the true moment work landed.** →
Mitigation: `RACE_GRACE_SECONDS` is applied to whichever boundary is selected, identically to
today — this is the same trade-off `original-created:` already accepted for consolidated
briefs, not a new one introduced here.

**[Risk] A brief manually edited to carry a bogus far-future `released-at:` would blind the
check to real evidence.** → Mitigation: `released-at:` is exclusively written by
`work_queue.py release`, not hand-authored in practice; this is the same trust boundary every
other frontmatter field this check reads already carries, and the check remains fail-open and
advisory (a human judges every surfaced match; a false negative here means no prompt, not an
incorrect auto-close).

## Migration Plan

No migration needed. This is purely additive to the existing precedence chain: a brief
without `released-at:` (the common case for a never-released brief) is unaffected. A brief
that already carries `released-at:` from a prior recheck (like
`20260623-093000-datalena-deferred-dep-upgrades`) benefits immediately on its next dispatch,
with no data change required. Roll out is a normal PR merge; no flag.
