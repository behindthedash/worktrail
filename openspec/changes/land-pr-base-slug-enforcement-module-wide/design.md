## Context

`land_pr` runs against checkouts where `origin` is a read-only upstream and the PR lives on a
fork (`remote.pushDefault=fork`). `_push_target()` resolves that fork as `base_slug`, and every
`gh` call that addresses the PR by a bare branch name, PR number, or workflow run id must carry
`-R <base_slug>` or `gh` silently answers about the upstream's same-numbered object.

The module has two distinct shapes for satisfying that contract, added at different times:

- an argv list literal followed by `if base_slug: args += ["-R", base_slug]`
  (`view_args`, `edit_args`, `cmd` in `open_or_update_pull_request`, `view_args` in
  `_resume_state`), and
- `_gh(..., base_slug=base_slug)`, where `_gh` appends the flag (every site `432329ed` touched).

The existing coverage test only understands the first shape and only looks inside
`open_or_update_pull_request`.

## Goals / Non-Goals

- Goal: a new `gh` call site anywhere in `land_pr.py` fails a test until a human classifies it.
- Goal: a new intra-module caller of a `base_slug`-taking helper fails a test until it threads the
  value through.
- Non-goal: changing `land_pr.py`. The module currently complies; this is enforcement only.
- Non-goal: a generic lint over `src/worktrail/router/`. Other modules resolve `owner/repo`
  differently (`automerge_preflight` reads a remote; `check_review_threads` takes `owner`/`name`),
  so one shared rule would be wrong for each of them.
- Non-goal: replacing `tests/router/test_land_pr_fork_repo_resolution.py`. That test proves the
  runtime behaviour on a fork fixture; this one proves no *future* site escapes it.

## Decisions

- **Site keys are function-qualified.** `extract_gh_pr_arg_sites()` keys by bare variable name
  today, which does not survive a module-wide walk: `view_args` exists in both
  `open_or_update_pull_request` and `_resume_state`, and `result`/`status` recur. Keys become
  `"<enclosing function>.<assigned variable>"`, and for a `_gh(...)` call that is a bare
  expression statement (the two `run rerun` sites), `"<enclosing function>.<verb phrase>#<n>"`
  with `n` the ordinal among same-keyed sites in that function, so a key is stable under
  reordering of unrelated code.
- **Three proof modes, one per real shape.** `keyword_base_slug` proves the call passes a
  `base_slug=` keyword whose value is a `Name` (not a literal `None`); `requires_base_slug` keeps
  the existing adjacent-`if base_slug:` list-append proof; `url_identified` keeps the existing
  known-URL-variable proof. An unclassifiable site fails rather than defaulting to exempt —
  fail-closed is the whole point of the pattern.
- **`_gh` itself is not a site.** The walk skips the body of `_gh`, which constructs `["gh",
  *args, ...]` as the shared helper rather than as a call site.
- **Helper threading is a separate test file.** The parameter-threading rule walks signatures and
  call sites, not argv shapes, and lives in
  `tests/router/test_land_pr_base_slug_threading_coverage.py` so the two enforcements fail with
  distinct, self-describing names.
- **`_review_thread_gate` is covered by the threading rule, not the gh rule.** It issues no `gh`
  call; it splits `base_slug` into `owner`/`name` for `check_review_threads.check`. Its
  `base_slug` parameter puts it in the threading test's scope, which is the correct guard.

## Risks / Trade-offs

- The classification table must be updated whenever a `gh` call is added or renamed in
  `land_pr.py` — deliberate friction, and the same cost the existing test already imposes for one
  function.
- AST-shape matching is brittle to a refactor that builds argv indirectly (a helper returning a
  list, a comprehension). Such a refactor makes the extraction find fewer sites, which the
  stale-entry assertion turns into a failure rather than silent under-coverage.
