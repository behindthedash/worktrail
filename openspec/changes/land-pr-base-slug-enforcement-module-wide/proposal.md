## Why

`tests/router/test_land_pr_base_slug_enforcement_coverage.py` exists to stop a new `gh` call
site in `land_pr.py` from silently losing `-R <base_slug>`, but it is scoped to exactly one
function: line 41 is `FUNC = land_pr.open_or_update_pull_request`, and both AST walks
(`extract_gh_pr_arg_sites`, `_proves_guarded_by_base_slug`) read `inspect.getsource(FUNC)`. Every
other `gh` call site in the module is unguarded by it.

That is where the failure actually recurred. PR #1157 fixed the missing `-R` on the `gh pr edit`
call inside `open_or_update_pull_request` and the coverage test was written around it; later
`432329ed` ("fix: land-pr fork PR resolution (false merged)", #1265) fixed the same class of bug
in `_checks_registered`, `_watch_ci`, `_pr_is_merged`, `_merge_state_guard`, `_log_excerpt` and
`_review_thread_gate` — all outside `FUNC`, so the coverage test passed throughout. The symptom
was severe and silent: on a `remote.pushDefault=fork` checkout, `gh` resolved bare PR number #15
against the upstream, where an unrelated merged PR #15 made `land_pr` report
`completed_and_merged` for a still-open fork PR (aspens run `go-20260918-183151`).

`432329ed` fixed the sites that existed then; nothing stops the next new `_gh()` call in
`_watch_ci` or a new helper from omitting `base_slug=` exactly as before. (Work-queue brief
`20260918-213254-land-pr-gh-calls-missing`.)

## What Changes

- The coverage test walks the whole `land_pr` module instead of one function: every `_gh(...)`
  call and every `gh` argv list literal built anywhere in `src/worktrail/router/land_pr.py` is
  discovered, keyed stably by enclosing function, and must carry a registered classification.
- Three proof modes instead of two: the existing `requires_base_slug` (a list literal guarded by
  an adjacent `if base_slug:` append) and `url_identified` (the PR is addressed by a full URL),
  plus `keyword_base_slug` for the `_gh(..., base_slug=base_slug)` shape that `432329ed`
  introduced and that is now the module's dominant form.
- A second enforcement covers the other half of the same gap: every `land_pr` helper whose
  signature accepts `base_slug` defaults it to `None`, so an intra-module caller that forgets to
  pass it reintroduces the upstream-resolution bug with no `gh`-level evidence. A new test
  asserts every intra-module call to such a helper actually supplies `base_slug`.
- No behaviour change to `land_pr.py`: all current call sites already satisfy both rules. This
  change makes that state enforced rather than incidental.

## Capabilities

### New Capabilities
- `land-pr-repo-scoping-enforcement`: every `gh` call site and `base_slug`-taking helper call in
  `land_pr` is structurally proven to address the PR's own repo.

### Modified Capabilities

## Impact

- `tests/router/test_land_pr_base_slug_enforcement_coverage.py` (widened from one function to the
  module; new `keyword_base_slug` proof mode; site keys become function-qualified).
- `tests/router/test_land_pr_base_slug_threading_coverage.py` (new).
- `src/worktrail/router/land_pr.py`: not modified. If widening the walk surfaces a site that does
  not satisfy any proof mode, that is a real bug and must be reported, not classified away.
- Related but distinct: `automerge-preflight-resolve-pr-target-remote` fixes the same fork-layout
  class in `automerge_preflight.py`; this change adds no enforcement there.
