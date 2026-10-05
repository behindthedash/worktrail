## 1. Devkit scanner denylist parity

- [ ] 1.1 Apply the dashboard-owned, case-insensitive non-spec directory boundary at the three
      devkit root enumerations: repository-wide requirement-coverage audit, handoff candidate
      classification, and overlap extraction. Do not apply it to OpenSpec `changes/` or `specs/`
      containers. In the existing tests for each scanner, reproduce a denylisted directory with
      otherwise scanner-shaped Markdown; preserve an unmarked, non-denylisted control; and prove
      a runtime addition to the shared set reaches overlap extraction while OpenSpec discovery is
      unchanged.
      (Requirements: Devkit Router Scanners Share the Non-Spec Directory Boundary;
      Non-Denylisted Devkit Children Retain Existing Eligibility.)
      files: src/worktrail/router/check_req_coverage.py, tests/router/test_check_req_coverage.py, src/worktrail/router/classify_handoff.py, tests/router/test_classify_handoff.py, src/worktrail/router/overlap_check.py, tests/router/test_overlap_check.py

## 2. Verification

- [ ] 2.1 [e2e] Run the three focused router test modules, the full pytest suite, and the
      strict OpenSpec validation; confirm all denylisted-directory regressions pass while their
      non-denylisted controls remain visible.
      depends: 1.1
