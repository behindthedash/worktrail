## 1. Same-repo gate and threshold changes

- [x] 1.1 In `src/worktrail/router/cluster_detect.py`, change
      `_signal_matches()`'s same-repo check so a pair where both `repo`
      values are null is treated as same-repo for same-target-spec,
      related-link, and focus-overlap purposes (unchanged for one-null-
      one-non-null pairs, unchanged for duplicate-slug which is already
      repo-independent).
- [x] 1.2 Lower `NEAR_IDENTICAL_THRESHOLD` from `0.75` to `0.50`; add a new
      `LLM_GATE_FLOOR = 0.35` constant, both with docstring/comment
      rationale referencing this change's design.md decisions D2/D3.
- [x] 1.3 Update the module docstring to drop the stale "spec 018"
      reference and cite this change's `duplicate-brief-detection` spec
      instead.

## 2. LLM verification gate

- [x] 2.1 Add a verification function that, given two briefs' focus text,
      invokes the configured headless agent CLI (reusing the existing
      `agent_cli` resolution pattern from `router/policy.py`) with a
      single-turn prompt asking whether the two briefs describe the same
      underlying work, and parses a yes/no verdict from the response.
- [x] 2.2 Wire the verification function into `_filter_reportable()` (or
      equivalent) so it fires only for size-2, null-vs-null candidates with
      focus-overlap coefficient in `[LLM_GATE_FLOOR, NEAR_IDENTICAL_THRESHOLD)`,
      per design.md decision D3.
- [x] 2.3 Apply a 10-second timeout to the verification call; treat
      timeout, non-zero exit, empty/unparseable output, or no configured/
      available agent CLI as a negative verdict (not surfaced), never
      raising out of `compute_clusters()`. Confirm `compute_clusters()`'s
      existing top-level failure wrapper (degrade to `[]` on unexpected
      failure) still applies unchanged, and that a single candidate's
      LLM-call failure does not suppress unrelated clusters in the same
      scan.

## 3. Tests

- [x] 3.1 Add fixtures to `tests/router/test_cluster_detect.py` covering:
      two null-repo briefs matching via focus-overlap (loosened gate);
      one-null-one-real-repo pair still excluded; the lowered
      `NEAR_IDENTICAL_THRESHOLD` surfacing a pair between 0.50 and the old
      0.75; the LLM gate band (0.35 to <0.50) triggering a (mocked)
      verification call.
- [x] 3.2 Add a regression fixture using the real PR #93 pair (both
      `repo: null`, overlap 0.44) asserting it is now surfaced when the
      (mocked) LLM verification call returns positive, and not surfaced
      when it returns negative.
- [x] 3.3 Add fail-open fixtures: mocked timeout, mocked non-zero exit,
      mocked empty output, and no-agent-CLI-configured, each asserting the
      pair is not surfaced and no exception propagates.
- [x] 3.4 Update `test_one_null_repo_blocks_repo_scoped_signals` and
      `test_both_null_repos_still_match_duplicate_slug`
      (tests/router/test_cluster_detect.py:319-342) to reflect the new
      null-vs-null same-repo-gate behavior; extend `SizeTwoSurfacingTests`
      (tests/router/test_cluster_detect.py:436-480) for the new threshold.

## 4. Verification

- [x] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm green.
- [x] 4.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
      and confirm green.
