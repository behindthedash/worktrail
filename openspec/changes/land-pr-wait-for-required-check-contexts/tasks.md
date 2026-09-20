## 1. Gate the CI watch on required-context coverage

- [ ] 1.1 In `src/worktrail/router/land_pr.py`: give `_checks_registered` a
      `required_contexts: list[str] | None = None` parameter -- keep today's behaviour when it
      is `None` or empty, otherwise parse `gh pr checks --json name` stdout and return `True`
      only when every required context appears among the reported names (a reported set missing
      any required context returns `False`, the same informative negative the
      "no checks reported" marker produces; an unparseable response still returns `None`).
      Thread the same parameter through `_watch_ci` and use it in both loops: the grace loop
      keeps polling until `_checks_registered` is not `False` and, when the grace attempts are
      spent, returns the existing `budget_exhausted` result; a zero-exit blocking watch returns
      `settled` only when `_checks_registered` is not `False`, otherwise it continues the
      re-issue loop. In `land_pr()`, before the `_watch_ci` call, resolve the contexts via
      `automerge_preflight.required_status_check_contexts()` against the resolved base slug and
      `request.base_branch`, and pass the result through.
      (Requirement: CI watch settles only on required-context coverage.)
      In a new `tests/router/test_land_pr_required_contexts.py` using `FakeRun` from
      `tests/router/test_land_pr.py`, cover every scenario of that requirement against
      `_checks_registered` and `_watch_ci`: non-required-only checks keep polling; coverage
      arriving on a later poll enters the watch; grace exhausted without coverage returns
      budget exhausted and not settled; a clean watch exit without coverage re-enters and does
      not settle; a clean watch exit with coverage settles; `None` contexts and `[]` contexts
      both reproduce today's behaviour; an already-merged PR settles regardless.
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr_required_contexts.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate land-pr-wait-for-required-check-contexts --strict` and
      `worktrail-compile openspec/changes/land-pr-wait-for-required-check-contexts`.
