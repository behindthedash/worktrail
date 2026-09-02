## MODIFIED Requirements

### Requirement: Apply step never closes a brief without an approved verdict
The `apply` step SHALL only ever act on verdicts present in a verdict file supplied via
`--verdict-file`, SHALL require an explicit `--confirm` flag before executing any
`stale-close`, `needs-update`, `duplicate-of`, `fold-into-change`, `propose-change`,
`work-directly`, or `needs-decision` action, and SHALL take no queue-mutating action for any
`keep` verdict. `apply` SHALL NOT expose any flag or code path that closes or edits a brief
without both an existing verdict file entry and the `--confirm` flag. Without `--confirm`,
every planned action — including the branch, target change, and pull-request title a fold or
propose would create — SHALL be printed and nothing SHALL be modified in the queue or in any
target repo. For `fold-into-change` and `propose-change`, a verdict's `repo` value SHALL be
resolved to an on-disk checkout directory the same way the router/dashboard resolve a brief's
`repo:` frontmatter (an absolute or home-relative path resolves directly; a bare name or
`owner/name`-style value resolves by basename under a configurable repos root, defaulting to
`~/projects`) before any worktree or git operation runs against it. A `repo` value that cannot
be resolved to an existing directory SHALL fail with an error action-log entry and SHALL NOT
attempt any worktree or git operation.

#### Scenario: Apply without --confirm is a dry run
- **WHEN** `apply` is invoked with a verdict file but without `--confirm`
- **THEN** every planned action (claim+done for stale-close/duplicate-of, in-place edit for
  needs-update, branch+PR for fold-into-change/propose-change, in-place stamp for
  work-directly, decision envelope for needs-decision) is printed but no brief in `queue/` or
  `picked/` is modified and no target repo is written to

#### Scenario: Apply with --confirm executes stale-close
- **WHEN** `apply --confirm` runs against a verdict file containing a `stale-close` verdict
  for brief `X`
- **THEN** brief `X` is claimed and marked done, with the verdict's evidence recorded as the
  closure note

#### Scenario: Apply with --confirm executes needs-update
- **WHEN** `apply --confirm` runs against a verdict file containing a `needs-update` verdict
  for brief `Y`
- **THEN** a `## Triage <run-date>` section containing the verdict's evidence is appended to
  brief `Y`'s body in place, and brief `Y` remains in `queue/` with `status: queued`

#### Scenario: Apply with --confirm executes fold-into-change
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change`
  verdict for brief `Z`
- **THEN** the fold is executed per the `intake-triage` capability's fail-closed
  pull-request semantics, and brief `Z` is closed only after the pull request exists

#### Scenario: Fold-into-change resolves a bare repo name
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change`
  verdict whose `repo` is a bare name (e.g. `devops`) that uniquely matches a sibling
  checkout under the configured repos root
- **THEN** the worktree, branch, and pull request are created against that matching
  checkout, not against a path relative to the current working directory

#### Scenario: Propose-change resolves a bare repo name
- **WHEN** `apply --confirm` runs against a verdict file containing a `propose-change`
  verdict whose `repo` is a bare name that uniquely matches a sibling checkout under the
  configured repos root
- **THEN** `openspec new change` and the subsequent worktree/PR flow run against that
  matching checkout

#### Scenario: Unresolvable repo value fails closed
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change` or
  `propose-change` verdict whose `repo` value does not resolve to an existing directory,
  either directly or by basename under the configured repos root
- **THEN** the action-log entry for that verdict reports an error status naming the
  unresolvable repo, no worktree is created, and no git or `gh` command runs against a
  guessed path
