## ADDED Requirements

### Requirement: The selfcheck skips known non-spec directories
`check_repo` SHALL NOT report a finding for a direct child of `docs/specs/` whose lowercased
directory name is one of the dashboard's known non-spec directory names, as declared by
`dashboard._NON_SPEC_DIRS`. The selfcheck SHALL read that set from `dashboard` rather than
maintaining its own copy, so the two modules cannot diverge.

#### Scenario: ambiguous loose docs in a shared sibling are ignored
- **WHEN** `docs/specs/addenda/` contains two untagged `.md` files that would tie under
  `find_spec_file`
- **THEN** `check_repo` returns no finding for `addenda`

#### Scenario: a clean repo whose only ambiguity is in a non-spec directory exits zero
- **WHEN** every ambiguity in a repo lies under denylisted directories
- **THEN** `check_repo` reports an empty `findings` list and `main` exits `0`

#### Scenario: the denylist is not restated
- **WHEN** a name is added to `dashboard._NON_SPEC_DIRS`
- **THEN** the selfcheck skips that name without any edit to `dashboard_selfcheck.py`

### Requirement: Real ambiguity findings are preserved
The scope filter SHALL be name-based only. A `docs/specs/` child that is not denylisted SHALL
still be checked exactly as before, including a folder that carries no `tasks/`, no `changes/`
and no `user-request.md` -- such a folder is the very case `find_spec_file` refuses on, and it
SHALL NOT be filtered out by a `_is_spec_folder`-style content test.

#### Scenario: a tie in a real spec folder is still reported
- **WHEN** `docs/specs/001-thing/` holds two untagged spec-doc candidates and nothing else
- **THEN** `check_repo` reports one `ambiguous-spec-doc` finding for `001-thing`

#### Scenario: a resolvable spec folder stays clean
- **WHEN** `docs/specs/002-thing/` holds candidates that `find_spec_file` resolves
- **THEN** `check_repo` reports no finding for `002-thing`
