## Why

`cluster_detect.py` powers the dashboard's queued-brief duplicate/near-duplicate
detection, but it has never had a committed spec in this repo — the module
docstring cites "spec 018" and REQ-011 through REQ-014, but no such artifact
exists here, in this repo's git history, or in the `developer-kit` repo it was
extracted from (verified by search; likely never carried over during
extraction). That leaves the module's behavior undocumented outside code
comments and tests.

Separately, investigation PR #93
(`docs/specs/research/cluster-detect-2brief-near-duplicate-miss.md`) confirmed
the detector missed a real duplicate pair — two briefs about the same
underlying work, both `repo: null`, real focus-text overlap 0.44 — because of
two intentional, already-tested design choices: `repo: null` brief pairs are
excluded from focus-overlap/same-target-spec/related-link matching entirely,
and a 2-member cluster needs 0.75 overlap (vs the general 0.45 floor) to be
surfaced at all. The investigation explicitly declined to treat this as a bug
and recommended a spec-first product decision on the precision/recall
tradeoff before touching either threshold. That decision has now been made:
loosen both gates, and add an LLM verification step as the precision
backstop for the resulting wider candidate net.

## What Changes

- Document `cluster_detect.py`'s current (as-shipped) behavior as a formal
  OpenSpec capability for the first time: four signal types (duplicate-slug,
  same-target-spec, related-link, focus-overlap), connected-components
  clustering, and the size-based reporting threshold (size >= 3 always
  surfaced, size == 2 needs near-identical overlap).
- Loosen the same-repo gate: two `repo: null` briefs may now match via
  same-target-spec, related-link, and focus-overlap, the same as same-repo
  pairs. Only a `blocked-by` relationship or a fully absent `repo` on one
  side while the other is a real string still excludes a pair (asymmetric
  null vs non-null repos remain out of scope for this change).
- Lower the near-identical overlap threshold a 2-member component's
  focus-overlap edge must clear to be surfaced, moving it closer to the
  general `OVERLAP_THRESHOLD` floor.
- **BREAKING** (internal, not a public API): add an LLM-based verification
  gate. It fires only for a candidate pair that clears the *loosened*
  near-identical threshold but would have failed the *old* 0.75 threshold —
  a narrow "maybe" band, not every candidate. A positive verdict surfaces
  the pair; a negative verdict, an LLM-call failure, or unavailability all
  degrade to the pre-LLM heuristic result (i.e. not surfaced, since by
  definition these candidates fail the old strict bar). This is the
  module's first network dependency; `dashboard.py`'s render path stays
  fast and fully offline for every candidate outside the maybe-band.

## Capabilities

### New Capabilities
- `duplicate-brief-detection`: cluster-signal extraction, pairwise signal
  matching (duplicate-slug, same-target-spec, related-link, focus-overlap),
  connected-components cluster assembly, the size-based surfacing threshold,
  and the LLM verification gate for borderline near-identical candidates.

### Modified Capabilities
(none — no existing `openspec/specs/` capability covers this behavior)

## Impact

- `src/worktrail/router/cluster_detect.py` — signal matching (same-repo
  gate), near-identical threshold, new LLM-verification call path, module
  docstring update (drop the stale "spec 018" reference in favor of this
  capability's spec).
- `src/worktrail/router/dashboard.py` — caller of `compute_clusters()`;
  needs to tolerate the new (bounded, rare) LLM-call latency/failure path
  without breaking the dashboard render.
- `tests/router/test_cluster_detect.py` — new fixtures for the loosened
  gate/threshold and the LLM verification gate (including the real PR #93
  missed pair as a regression fixture), plus updates to
  `test_one_null_repo_blocks_repo_scoped_signals` /
  `test_both_null_repos_still_match_duplicate_slug` /
  `SizeTwoSurfacingTests` for the new behavior.
- No changes to `docs/specs/research/cluster-detect-2brief-near-duplicate-miss.md`
  (historical investigation record, not touched by this change).
