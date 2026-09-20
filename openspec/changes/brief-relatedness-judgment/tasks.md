## 1. Shared client

- [x] 1.1 Extract `src/worktrail/router/typesafe.py` from `risk_judgment.py`:
      endpoint/model/credential constants, `is_configured()`, a single-request
      `post(state, questions)`, a `noul(answers, key)` reader that raises on any
      non-numeric shape, and the `JUDGMENT_ERRORS` tuple both backends catch to
      mean "unavailable". Rewire `risk_judgment.py` onto it, re-exporting
      `API_KEY_ENV` so its own readers and tests keep one import.
      (Requirement: Relatedness judgment fails safe and is never required)
  files: src/worktrail/router/typesafe.py, src/worktrail/router/risk_judgment.py

## 2. Relatedness backend

- [x] 2.1 [depends: 1.1] Add `src/worktrail/router/relatedness_judgment.py`:
      the two-Noul question set (`same_work`, `should_cluster`), a pure
      `should_edge()` that forms an edge when EITHER clears
      `JUDGMENT_THRESHOLD`, and `judge_pair()` returning
      `(edge, same_work, should_cluster)` or `None` on a missing credential or
      any failure, with an injectable `asker`.
      (Requirement: Relatedness is decided by reading the pair, not by counting shared words)
      (Requirement: Relatedness judgment fails safe and is never required)
  files: src/worktrail/router/relatedness_judgment.py, tests/router/test_relatedness_judgment.py

## 3. Prefilter, budget, and the verdict's effect on an edge

- [x] 3.1 [depends: 2.1] In `cluster_detect.py` add `JUDGED_MATCH`,
      `JUDGMENT_PREFILTER_FLOOR`, `MAX_JUDGED_PAIRS`, and
      `_judgment_candidates()` — same-repo pairs with at least
      `MIN_FOCUS_TOKENS` on both sides and overlap at/above the floor, ranked
      by overlap descending and capped.
      (Requirement: The lexical stage becomes a bounded prefilter)
  files: src/worktrail/router/cluster_detect.py, tests/router/test_relatedness_judgment.py

- [x] 3.2 [depends: 3.1] Add `_apply_relatedness_judgment()`: replace a judged
      pair's `focus-overlap` match with `JUDGED_MATCH` or with nothing, leave
      every structural match untouched, and stop the pass on the first
      unavailable verdict. Thread `judge_pair_fn`/`log_judged_fn` through
      `_compute_clusters_inner` and `compute_clusters`, both defaulting to
      `None` so every existing caller keeps prior behaviour exactly.
      (Requirement: The judgment replaces the focus signal and nothing else)
      (Requirement: Focus-Overlap Threshold)
  files: src/worktrail/router/cluster_detect.py, tests/router/test_relatedness_judgment.py

- [x] 3.3 [depends: 3.2] Inject both at `dashboard.py`'s `compute_clusters`
      call site — `judge_pair` only when `is_configured()`, and
      `cluster_telemetry.log_judged_pairs` as the sink. The judgment client is
      injected rather than imported by `cluster_detect`, whose own guard test
      rejects any network module in that file.
      (Requirement: Relatedness judgment fails safe and is never required)
  files: src/worktrail/router/dashboard.py, tests/router/test_relatedness_judgment.py

## 4. Telemetry

- [x] 4.1 [depends: 3.2] Add `cluster_telemetry.log_judged_pairs()` writing one
      `judged` record per pair (members, overlap, both Noul values, verdict)
      with one shared timestamp, best-effort like the module's other writers,
      and document the new record kind in its docstring.
      (Requirement: Judged pairs are recorded for later tuning)
  files: src/worktrail/router/cluster_telemetry.py, tests/router/test_relatedness_judgment.py

## 5. Tests

- [x] 5.1 [depends: 3.3, 4.1] Add `tests/router/test_relatedness_judgment.py`
      covering `should_edge`'s union rule and threshold boundary; every
      `judge_pair` failure mode; candidate selection (cross-repo excluded, thin
      brief excluded, below-floor excluded, overlap-descending order, the cap);
      the verdict's effect on an edge in both directions with structural
      matches preserved; no-injection leaving edges untouched; the
      stop-on-first-failure rule; the per-pair record; `compute_clusters`
      end-to-end in both directions; and `log_judged_pairs`' record shape and
      swallowed write failure. Build signals through `_extract_signal` so the
      tests use production's own dict shape.
      (Requirement: Relatedness is decided by reading the pair, not by counting shared words)
      (Requirement: The lexical stage becomes a bounded prefilter)
      (Requirement: The judgment replaces the focus signal and nothing else)
      (Requirement: Judged pairs are recorded for later tuning)
  files: tests/router/test_relatedness_judgment.py

## 6. Verification

- [x] 6.1 [depends: 5.1] [e2e] Run `PYTHONPATH=src pytest -q`,
      `python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .`,
      `python3 scripts/ci/check_shebang_exec_bits.py`, and
      `python3 -m worktrail.orchestrator.orchestrate check`; then exercise
      `judge_pair()` against the live endpoint with a real credential on one
      paraphrase pair and one high-overlap-but-unrelated pair, confirming the
      verdict moves opposite to the lexical coefficient on each.
      (Requirement: Relatedness is decided by reading the pair, not by counting shared words)
  files: src/worktrail/router/relatedness_judgment.py, tests/router/test_relatedness_judgment.py
