## Context

`cluster_detect.py` is a stdlib-only, no-network module (per its own
docstring) invoked synchronously by `dashboard.py::_render_active_specs`-
adjacent orientation-dashboard code on every `/go` invocation — including
`/go auto` and unattended `drain` runs. It extracts a Cluster Signal per
queued brief (repo, target-spec, related-id list, blocked-by-id list, slug,
focus-text tokens), computes pairwise Signal Matches across four signal
types, assembles connected components, and filters which components are
worth surfacing (size >= 3 unconditionally; size == 2 only when
"near-identical").

Investigation PR #93 confirmed a real miss: two briefs about the same work
(both `repo: null`, overlap 0.44) were invisible to the detector because (a)
`_signal_matches()`'s same-repo requirement excludes any pair where either
brief's `repo` is null before same-target-spec/related-link/focus-overlap
are even computed, and (b) `NEAR_IDENTICAL_THRESHOLD = 0.75` is well above
this pair's actual overlap. Both are intentional, tested design choices —
not bugs — with a real precision/recall tradeoff: loosening either surfaces
more true duplicates but risks false-positive clustering across the ~70
`repo: null` briefs typically in the queue at once.

Repo owner's decision: loosen both gates, and add an LLM verification step
so the false-positive risk from loosening is caught before it reaches the
dashboard, rather than accepted as a permanent precision cost.

## Goals / Non-Goals

**Goals:**
- Give `cluster_detect.py`'s existing behavior a committed spec for the
  first time (closes the "spec 018 doesn't exist" gap).
- Let two `repo: null` briefs match on same-target-spec/related-link/
  focus-overlap, not just duplicate-slug.
- Lower the 2-member near-identical bar so pairs like the PR #93 case
  (0.44 overlap) can be considered.
- Add an LLM verification gate that fires only on the narrow band of
  candidates the loosened thresholds newly admit but the old strict bar
  would have rejected, so the fast/offline path is unchanged for every
  candidate outside that band.
- Keep `dashboard.py`'s render path resilient: an LLM call that fails,
  times out, or is unavailable must never crash or hang the dashboard.

**Non-Goals:**
- No change to `duplicate-slug`'s repo-independent matching (already
  correct — this is how the two `repo: null` briefs *could* have matched
  if their slugs had been identical, which they weren't).
- No change to the size >= 3 "always surfaced" rule — this change only
  touches the size == 2 near-identical path and the same-repo gate.
- No general-purpose LLM integration layer for the router package — the
  verification call is scoped narrowly to this one gate, not a reusable
  "ask an LLM" utility for other router modules (a broader utility can be
  extracted later if a second caller emerges).
- No change to `duplicate-brief-detection`'s asymmetric case (one brief
  `repo: null`, the other a real repo string) — out of scope; the gate
  change here only affects null-vs-null pairs.

## Decisions

**D1 — Loosen the same-repo gate for null-vs-null pairs only.**
`_signal_matches()`'s `same_repo` check becomes: pairs where *both* repos
are null are now treated as same-repo for same-target-spec/related-link/
focus-overlap purposes (matching how they already behave for
duplicate-slug). Pairs with one null and one non-null repo remain excluded,
unchanged. *Alternative considered:* treat null as a wildcard that matches
any repo (including non-null) — rejected as much higher false-positive
risk (a `repo: null` brief could then cluster with any same-worded
same-repo brief) with no supporting evidence from the investigation that
this case matters in practice.

**D2 — Lower `NEAR_IDENTICAL_THRESHOLD` from 0.75 to 0.50.**
The PR #93 pair scored 0.44 — below even 0.50 — so this alone would not
have surfaced it; D1 + D2 together do (0.44 overlap on a now-eligible
null-vs-null pair still needs the LLM gate below to actually surface, since
0.44 < 0.50). 0.50 is chosen as a meaningful drop from 0.75 without
collapsing to `OVERLAP_THRESHOLD` (0.45) — collapsing the two thresholds
entirely would mean *every* general match is also treated as "near
identical" for 2-member components, eliminating the extra bar entirely
rather than lowering it. *Alternative considered:* set it equal to
`OVERLAP_THRESHOLD` (0.45) — rejected because that removes the size-2
extra-scrutiny concept altogether, which the investigation's own
recommendation did not ask for.

**D3 — LLM verification gate scope: below `NEAR_IDENTICAL_THRESHOLD`
(0.50) but at/above `OVERLAP_THRESHOLD` (0.45), for size-2 components with
both repos null.**
This is the "maybe" band: candidates that clear the general matching floor
(so a real edge exists) but fall short of the new near-identical bar. Only
candidates in this narrow band trigger an LLM call. The PR #93 pair (0.44)
falls *below* `OVERLAP_THRESHOLD` (0.45) itself — it never even forms a
`focus-overlap` edge under D1+D2 alone. To actually cover the motivating
case, the LLM gate's band is widened downward specifically for
null-vs-null size-2 pairs: any pair scoring at or above a lower floor
(`LLM_GATE_FLOOR = 0.35`, chosen to include the 0.44 case with headroom,
without inviting near-zero-overlap pairs into an LLM call) up to
`NEAR_IDENTICAL_THRESHOLD` (0.50) is eligible for LLM verification, *in
addition to* forming a normal edge at/above `OVERLAP_THRESHOLD` for
non-null-repo pairs. *Alternative considered:* only gate candidates that
already form a `focus-overlap` edge (>= 0.45) — rejected because it would
not have caught the actual motivating case (0.44), defeating the point of
this change.

**D4 — Reuse the existing headless-agent invocation pattern, not a new
LLM/API integration.**
The orchestrator (`spawnlib.py`, `live.py`) already has a provider-agnostic
pattern for shelling out to a configured headless agent CLI (`claude -p`,
`codex exec`, `opencode`, resolved the same way `agent_cli` is resolved
elsewhere in `router/policy.py`/`dashboard.py`). The verification call
reuses that same resolution and a single-turn, non-worktree, non-git
invocation (no file edits, no commits — just a text prompt asking "are
these two briefs describing the same underlying work?" and a
structured yes/no + one-line reason). This keeps the codebase to one
pattern for "call an LLM headlessly" instead of two. *Alternative
considered:* a direct Anthropic/OpenAI API call — rejected; it would add a
second, inconsistent LLM-invocation pattern and a new API-key dependency
this repo doesn't otherwise have, when the CLI-based pattern already
exists and matches whatever agent the user is running worktrail under.

**D5 — Bounded timeout and fail-open (i.e., fail to "not surfaced") on any
LLM-call problem.**
The call runs with a short timeout (10s). Timeout, non-zero exit, empty/
unparseable output, or the configured `agent_cli` being unset/unavailable
all resolve to "not verified" — the candidate is treated exactly as it
would have been *without* this change (i.e., under the old strict
threshold, not surfaced). This is a deliberate precision-over-recall
fallback: an LLM problem degrades the dashboard back to its pre-change
behavior rather than either crashing or guessing yes.

## Risks / Trade-offs

- [Risk] LLM call adds latency to the affected `/go` invocation when the
  maybe-band is non-empty (typically 0–2 pairs per render, per the
  investigation's queue sample). → Mitigation: 10s timeout bound; band is
  narrow by construction (D3); every candidate outside the band is
  unaffected and stays instant.
- [Risk] `/go auto`/`drain` unattended runs now depend on a working headless
  agent CLI for full clustering fidelity. → Mitigation: D5's fail-open
  behavior means an unavailable/broken CLI degrades clustering (misses
  some near-duplicates) rather than breaking the dashboard or the
  unattended run.
- [Risk] LLM verdicts are not deterministic/reproducible the way the rest
  of this stdlib-only module is. → Mitigation: scope is narrow (D3) and
  fail-open (D5); this is an explicit, documented trade-off of the
  precision-gate design, not something this change tries to eliminate.
- [Risk] Loosening D1+D2 without D3 would increase false positives across
  ~70 queued `repo: null` briefs. → Mitigation: D3 is not optional — the
  spec requires the LLM gate for the maybe-band; a future change that
  drops D3 while keeping D1/D2 would need its own precision/recall
  re-justification.

## Migration Plan

No data migration. Pure behavior change in `cluster_detect.py` +
`dashboard.py`'s tolerance of the new (bounded) latency/failure path.
Rollback is a plain revert of this change's commits — no persisted state
depends on the new thresholds or the LLM gate.

## Open Questions

None — the precision/recall direction (loosen + LLM-verify) and every
threshold value above are decided for this change. Any further tuning
(e.g. the exact 0.50/0.35 constants) is expected to happen via a future
change once real-world false-positive/false-negative data exists, not
speculatively here.
