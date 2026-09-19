## ADDED Requirements

### Requirement: Every gh call site in land_pr is classified module-wide
The coverage test SHALL discover every `gh` call site in `src/worktrail/router/land_pr.py` —
every call to `_gh()` and every argv list literal beginning a `gh` command — across the whole
module rather than within a single function, keyed by enclosing function so that same-named
variables in different functions are distinct sites. The discovered set SHALL equal the
registered classification table exactly: an unregistered site and a registered site that no
longer exists SHALL each fail.

#### Scenario: A new unclassified gh call fails the test
- **WHEN** a `_gh(...)` call is added to a `land_pr` function other than
  `open_or_update_pull_request` and no classification is registered for it
- **THEN** the coverage test fails naming the new site's function-qualified key

#### Scenario: Same-named variables in different functions are separate sites
- **WHEN** both `open_or_update_pull_request` and `_resume_state` assign a `view_args` gh argv
  list
- **THEN** the extraction reports two distinct sites, each requiring its own classification and
  proof

#### Scenario: A removed call site fails as stale
- **WHEN** a registered site's `gh` call is deleted from `land_pr.py`
- **THEN** the coverage test fails identifying the stale registration

#### Scenario: The _gh helper is not itself a site
- **WHEN** the module-wide walk runs
- **THEN** the `["gh", *args, ...]` construction inside `_gh` is excluded from the discovered
  sites

### Requirement: Each classified site is proven by its actual guarding shape
Every registered site SHALL be proven by one of three modes: `keyword_base_slug`, where the call
passes a `base_slug=` keyword bound to a name rather than a literal `None`; `requires_base_slug`,
where the argv list assignment is immediately followed by an `if base_slug:` block appending
`["-R", base_slug]` to that same variable; or `url_identified`, where the PR is addressed by a
known URL-producing variable and needs no `-R`. A site registered with any other classification
SHALL fail.

#### Scenario: Keyword-guarded site passes
- **WHEN** `_watch_ci` calls `_gh(repo, runner, "pr", "checks", str(pr_number), "--watch",
  base_slug=base_slug)`
- **THEN** the `keyword_base_slug` proof passes for that site

#### Scenario: Keyword dropped from an existing site fails
- **WHEN** a site registered `keyword_base_slug` is changed to omit the `base_slug=` keyword, or
  to pass `base_slug=None`
- **THEN** its proof fails naming that site

#### Scenario: List-literal site loses its guard
- **WHEN** the `if base_slug: view_args += ["-R", base_slug]` block following a
  `requires_base_slug` site's assignment is removed
- **THEN** the proof fails, as it does today for `open_or_update_pull_request`

#### Scenario: URL-identified site stops using a URL
- **WHEN** a `url_identified` site's PR identifier argument is changed from a known URL variable
  to a bare PR number or branch name
- **THEN** the proof fails, directing the site to be reclassified as needing `-R <base_slug>`

### Requirement: base_slug is threaded through every intra-module helper call
A test SHALL assert that every function in `src/worktrail/router/land_pr.py` whose signature
accepts a `base_slug` parameter is called, from every other call site within the module, with
`base_slug` supplied — positionally or by keyword — and never left to its `None` default.

#### Scenario: A helper call omitting base_slug fails
- **WHEN** `_pr_is_merged(repo, pr_number, runner)` is called inside `land_pr()` without the
  `base_slug` argument
- **THEN** the threading test fails naming the caller and the helper

#### Scenario: Current callers pass
- **WHEN** the test runs against `land_pr.py` as of this change
- **THEN** it passes: `_checks_registered`, `_watch_ci`, `_pr_is_merged`, `_merge_state_guard`,
  `_log_excerpt`, `_review_thread_gate`, and `open_or_update_pull_request` are each called with
  `base_slug` supplied

#### Scenario: A newly added base_slug helper is covered automatically
- **WHEN** a new `land_pr` helper declaring `base_slug: str | None = None` is added and called
  without it
- **THEN** the test fails without any registration step, because scope is derived from
  signatures rather than a hand-maintained list
