## 1. Exempt a superseded CANCELLED run from classify_checks

- [ ] 1.1 In `src/worktrail/orchestrator/verify.py`'s `classify_checks()` (around lines 267-304),
      before the main loop, compute a `superseded_names` set of check names that have at least
      one entry in the rollup that is CheckRun-shaped (`"conclusion" in c or "status" in c`) and
      whose conclusion is not `CANCELLED` (this covers both a settled non-cancelled conclusion
      like `SUCCESS` and a still-pending entry with no conclusion at all, e.g. `IN_PROGRESS`). In
      the main loop's CheckRun branch, after the existing `status in _PENDING_CHECK_STATUS` check
      and before the `conclusion in _FAIL_CONCLUSIONS` check, add: if `conclusion == "CANCELLED"`
      and `name in superseded_names`, skip this entry (continue) without adding it to `failing` or
      setting `pending`. Leave the legacy `StatusContext` branch, the `isRequired`/informational
      carve-out, and the `required`-names pending logic untouched. In
      `tests/orchestrator/test_verify.py`'s `ClassifyChecks` test class, add: a rollup with a
      `CANCELLED` entry and a `SUCCESS` entry for the same name asserts `(False, [])`; a rollup
      with a `CANCELLED` entry and an `IN_PROGRESS` entry for the same name asserts `(True, [])`;
      a rollup with only a `CANCELLED` entry for a name (no superseding entry) asserts unchanged
      failing behavior `(False, ["build"])`; a rollup with two independently `CANCELLED` entries
      under different names asserts both still appear in `failing` (the exemption is scoped to
      per-name matches, not applied across unrelated checks). (Requirements: Pure classification
      of a PR's statusCheckRollup; A superseded CANCELLED run is not a failure)

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends
      on 1.1.
