## Why

`cluster_detect` decides brief relatedness with a bag-of-words overlap
coefficient at `OVERLAP_THRESHOLD = 0.45`. Lexical overlap and shared work are
different things, and the gap is measured in **both** directions (2026-09-19):

- **Paraphrases are invisible.** Three pairs describing identical work scored
  **0.00-0.12** overlap — far below the threshold, so the rule cannot see them
  at all — while the judgment rated them **0.76-0.86** on `same_work`.
- **High overlap is not relatedness.** Of the 14 highest-overlap real corpus
  pairs, **7 were unrelated** by inspection: two different epics'
  decomposition-gap briefs, a reject-UI brief against a vitest-coverage brief,
  and so on.

The appetite for a semantic decision already exists — `_verify_same_work()`
escalates one narrow band (`LLM_GATE_FLOOR` 0.35 up to `OVERLAP_THRESHOLD`) to
a headless-agent call — but it is aimed at the wrong band. Everything below
0.35, where the paraphrases live, is never offered to anything.

## What Changes

- New `router/relatedness_judgment.py`: two Nouls per pair, `same_work` and
  `should_cluster`, in a single request, plus a **pure** `should_edge()` that
  turns them into an edge. Either signal alone is enough — `same_work` means one
  item subsumes the other, `should_cluster` means doing them apart causes
  rework even though they are distinct — so requiring both would drop exactly
  the case the second question exists for.
- The lexical stage is **demoted to a prefilter, not removed**. It still picks
  which pairs are worth a request (`PREFILTER_FLOOR = 0.05`, far below the edge
  threshold, because a prefilter near 0.45 would reproduce the blindness being
  fixed) and still ranks them, so a bounded budget goes to the most plausible
  candidates first.
- `MAX_JUDGED_PAIRS = 40` per run. Pair count grows quadratically with queue
  size — 68 briefs is 2278 pairs before repo scoping — and a dashboard render
  may not fan out unbounded. Everything beyond the cap keeps the lexical
  decision.
- A judged pair's `focus-overlap` match is replaced by the verdict: a
  `focus-judgment` match when the pair belongs together, and **nothing** when it
  does not, which is how a high-overlap-but-unrelated pair loses its edge.
  Structural matches (duplicate-slug, same-target-spec, related-link) are never
  touched — they are facts about the briefs, not a guess about their text.
- The repo-scoping precondition and the `MIN_FOCUS_TOKENS` thin-brief
  abstention are unchanged: a cross-repo pair is never offered, and a brief too
  thin for the lexical rule to read is one the judgment cannot read either.
- Every judged pair is recorded to the existing cluster telemetry log as a new
  `judged` record kind with both Noul values and the lexical overlap that
  ranked it, so the thresholds can be retuned against real pairs later without
  paying for inference again.
- `router/typesafe.py` is extracted from `risk_judgment.py` so the transport,
  credential, timeout and "what counts as unavailable" rule have one home
  rather than two that drift.

## Impact

- Affected specs: `duplicate-brief-detection` (modified), `brief-relatedness-judgment` (new)
- Affected code: `router/relatedness_judgment.py` (new), `router/typesafe.py`
  (new, extracted), `router/risk_judgment.py` (uses the extracted client),
  `router/cluster_detect.py`, `router/cluster_telemetry.py`
- No change to cluster assembly, the surfaced-cluster shape, the existing
  `_verify_same_work` LLM gate, or `OVERLAP_THRESHOLD` itself.

## Open question answered

The brief asked what per-run request budget is acceptable given a 68-brief
queue and quadratic pair growth. **40 pairs**, ranked by lexical overlap
descending. At the measured ~930 tokens and ~0.55s per pair that is a bounded
worst case per render, and the ranking means the budget is spent where a
reader would look first. The cap, not the floor, is what bounds cost — which is
why the floor can be set low enough to actually reach paraphrases.
