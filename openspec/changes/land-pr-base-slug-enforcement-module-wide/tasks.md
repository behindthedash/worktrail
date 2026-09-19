## 1. Widen the enforcement

- [ ] 1.1 In `tests/router/test_land_pr_base_slug_enforcement_coverage.py`, replace the
      single-function scope (`FUNC = land_pr.open_or_update_pull_request`, line 41) with a
      module-wide walk: parse `inspect.getsource(land_pr)` once, and for each
      `ast.FunctionDef` other than `_gh`, discover every `_gh(...)` call and every `gh` argv
      list literal. Key sites as `"<function>.<assigned variable>"`, and for a `_gh(...)` bare
      expression statement as `"<function>.<verb phrase>#<ordinal>"`. Add the
      `keyword_base_slug` proof (a `base_slug=` keyword bound to an `ast.Name`, rejecting a
      literal `None`) alongside the existing `requires_base_slug` and `url_identified` proofs,
      and fail on an unknown classification. Re-key and extend `SITE_CLASSIFICATION` to the
      full module set (the `open_or_update_pull_request` five, `_resume_state.view_args`,
      `_log_excerpt`, `_checks_registered`, the two `_watch_ci` `pr checks` calls and its
      `run rerun`, `_pr_is_merged`, `_merge_state_guard` and its `run rerun`), and update the
      module docstring, which currently states the test is scoped to one function.
      (Requirements: Every gh call site in land_pr is classified module-wide; Each classified
      site is proven by its actual guarding shape.)
      Cover the spec scenarios with negative cases built from mutated source snippets rather
      than by editing `land_pr.py`: an unclassified new site, a stale registration, a dropped
      `base_slug=` keyword, a removed `if base_slug:` guard, and a `url_identified` site
      switched to a bare PR number.
      files: tests/router/test_land_pr_base_slug_enforcement_coverage.py

- [ ] 1.2 Add `tests/router/test_land_pr_base_slug_threading_coverage.py`: AST-walk
      `src/worktrail/router/land_pr.py` for every module-level function whose signature declares
      a `base_slug` parameter, then assert every call to one of those functions from elsewhere
      in the module supplies `base_slug` positionally or by keyword. Scope is derived from
      signatures, not a registration list, so a new helper is covered with no bookkeeping.
      Assert the module passes today, and assert the detector fires on a mutated source snippet
      where a caller drops the argument.
      (Requirement: base_slug is threaded through every intra-module helper call.)
      files: tests/router/test_land_pr_base_slug_threading_coverage.py

## 2. Verification

- [ ] 2.1 [depends: 1.1, 1.2] [e2e] Run `PYTHONPATH=src pytest -q tests/router/`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Confirm
      `src/worktrail/router/land_pr.py` is unmodified (`git diff --stat` shows tests only); if
      either new enforcement flags a real unguarded site, report it rather than classifying it
      as exempt. Run `openspec validate land-pr-base-slug-enforcement-module-wide --strict` and
      `worktrail-compile openspec/changes/land-pr-base-slug-enforcement-module-wide`.
