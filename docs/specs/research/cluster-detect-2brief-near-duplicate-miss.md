# Investigation: cluster-detect missed a 2-brief near-duplicate pair

Handoff brief: `20260731-171144-dashboard-cluster-detection-missed-a`

> **Status: RESOLVED (historical record).** The thresholds and gating this
> investigation describes have since changed twice: the archived
> `duplicate-brief-detection` change (2026-08-02) made both-null repo pairs
> same-scope, lowered `NEAR_IDENTICAL_THRESHOLD` from 0.75 to 0.50, and
> added an LLM verification gate; the full-recall decision (2026-08-13,
> user-approved for brief `20260731-173028`) then removed the size-2
> near-identical bar entirely, so 2-member components surface at the same
> `OVERLAP_THRESHOLD` (0.45) edge floor as larger ones. See
> `openspec/specs/duplicate-brief-detection/spec.md` for the current
> contract. The body below is preserved as written, describing the
> pre-2026-08-02 behavior it investigated.

## Question

Why did dashboard cluster-detection miss the 2-brief pair
`20260731-161140-promote-contract-sentinel-s-route` vs.
`20260731-142723-complete-the-contract-sentinel-route` (both about finishing
contract-sentinel's route-existence-gate rollout), even though the
focus-overlap signal correctly caught larger clusters (14-brief, 5-brief) the
same session?

## Verified Observations

1. Both briefs carry `repo: null` in frontmatter (read directly from
   `~/work-queue/picked/20260731-142723-complete-the-contract-sentinel-route.md`
   and `.../20260731-161140-promote-contract-sentinel-s-route.md`).
2. `cluster_detect.py::_signal_matches()` (src/worktrail/router/cluster_detect.py:167-171)
   computes `same_repo = repo_a is not None and repo_b is not None and repo_a == repo_b`
   and returns immediately — before computing `same-target-spec`, `related-link`,
   or `focus-overlap` — whenever `same_repo` is False.
3. This exact behavior (null-repo pairs excluded from every repo-scoped signal,
   matching only via `duplicate-slug`) is intentional and already covered by
   existing unit tests: `test_one_null_repo_blocks_repo_scoped_signals` and
   `test_both_null_repos_still_match_duplicate_slug`
   (tests/router/test_cluster_detect.py:319-342). It is not an oversight.
4. Directly computing `_tokenize`/`_overlap_coefficient` on the two briefs'
   real `focus:` text (bypassing the same-repo gate, run from the investigation
   worktree) gives an overlap coefficient of **0.44** — 185 tokens in brief A,
   50 in brief B, 22-token intersection, `22/min(185,50) = 0.44`.
5. `OVERLAP_THRESHOLD = 0.45` (general edge-forming floor, any cluster size)
   and `NEAR_IDENTICAL_THRESHOLD = 0.75` (the additional floor a size-2
   component's edge must clear before `_filter_reportable` surfaces it, per
   REQ-013/014 cited in the module docstring) — cluster_detect.py:42,47,260-281.
6. The two briefs' slugs differ (`complete-the-contract-sentinel-route` vs.
   `promote-contract-sentinel-s-route`), so `duplicate-slug` does not match
   either. Neither brief's frontmatter carried a `related:` field pointing at
   the other at claim time — the `batch:`/`batch-primary:` fields now visible
   on the picked files were written by the operator during Phase 2
   batch-consumption dispatch, after manually noticing the overlap; they are
   not inputs `cluster_detect.py` sees at scan time.
7. Larger clusters (14-brief, 5-brief) the same session were unaffected by any
   of this because `_filter_reportable` surfaces every component of size >= 3
   unconditionally — no near-identical bar applies above size 2.

## Confirmed Root Cause

The pair never forms a `focus-overlap` edge in the live pipeline at all: both
briefs carry `repo: null`, so `_signal_matches`'s same-repo requirement is
False and the function returns before the overlap coefficient is even
computed. This is deliberate, already-tested behavior, not a bug.

That gate is not the whole story, though — it is overdetermined. Even
setting the repo-null gate aside, this specific pair's real overlap
coefficient (0.44) already falls short of the *general* `OVERLAP_THRESHOLD`
(0.45) needed to form any edge, and further short of the `NEAR_IDENTICAL_THRESHOLD`
(0.75) a 2-member component additionally needs to be surfaced. Relaxing the
repo-null gate alone would **not** have caused this pair to surface — the
miss is jointly caused by (a) the repo-null gate and (b) the pair's
genuine token-overlap sitting below both thresholds that matter for a
2-item component, not a single off-by-one threshold bug.

The brief's own hypotheses about "minimum-cluster-size threshold" or "the
detector only running within a size band" are **not confirmed** — there is
no such band; size >= 3 is unconditional and size == 2 uses the documented,
tested near-identical bar.

## Unknowns / Missing Evidence

- How often `repo: null` occurs on genuinely-duplicate cross-cutting/
  workspace-wide briefs workspace-wide (only this one instance was audited).
- The false-positive rate a lowered `NEAR_IDENTICAL_THRESHOLD` (or a relaxed
  same-repo gate) would introduce across the current queue — not measured.

## Hypotheses (unverified)

- Lowering `NEAR_IDENTICAL_THRESHOLD` for 2-member components would surface
  more real near-duplicates like this one, but this pair's 0.44 score means
  the bar would need to drop well below its current 0.75 — a much bigger
  change than a minor tuning, with a real precision/recall tradeoff for every
  future 2-item dashboard cluster.
- Relaxing the same-repo gate so two `repo: null` briefs are compared like
  same-repo pairs might catch more workspace-wide duplicates, but this is an
  explicit, tested design decision, and `repo: null` is common on
  workspace-wide audit-generated briefs (e.g. this very handoff brief,
  `20260731-171144`, also carries `repo: null`) — relaxing it changes
  behavior for every future dispatch involving those briefs.

## Validation Steps (if pursued)

- Threshold change: extend `SizeTwoSurfacingTests`
  (tests/router/test_cluster_detect.py:436-480) with a fixture using these two
  briefs' real focus text at a range of candidate `NEAR_IDENTICAL_THRESHOLD`
  values, and sample-audit the ~70 queued/picked `repo: null` briefs for
  false-positive duplicate flags at each candidate before picking one.
- Repo-gate relaxation: add a fixture pairing two genuinely-unrelated
  `repo: null` briefs with coincidentally overlapping generic vocabulary and
  confirm they do *not* spuriously cluster before shipping the relaxation.

## Recommendation

Both changes under consideration (near-identical threshold, same-repo gating
for null-repo pairs) are intentional, already-tested design choices with
active precision/recall tradeoffs — not a bug fix. This is not "small and
clearly in scope," so this run does not continue inline into an
implementation route. Recommend **Route G (spec change)**: `cluster_detect.py`'s
REQ-011 through REQ-014 imply an existing spec documenting these exact
thresholds; any change to them needs a spec-first update with an explicit
before/after and a stated precision/recall decision, not an ad hoc code edit.
(Route J is a plausible alternative frame since `cluster_detect.py` lives in
GO's own `router/` package, but its specific `routing_cassette_required` gate
targets `classify.py`'s routing table, which this doesn't touch — G's
"intentional behavior change, spec first" framing fits the actual nature of
this change better.)
