# operator-pr-discovery-branch-guard Specification

## Purpose
Prevents the operator-PR-discovery fallback from mistaking an unrelated PR
for a group's own PR, which would wrongly quarantine real implementation
work as "already has an open PR".

## Requirements

### Requirement: Discovered PR must correspond to the group's own branch
When the operator-PR-discovery fallback (`gh pr list --search`) returns one
or more candidate matches for a group, the system SHALL only accept a
candidate as that group's PR when the candidate's head branch equals the
group's own branch (`<run_id>/<group_name>`) or the candidate's base branch
matches the group's target branch. A candidate that satisfies neither check
SHALL be rejected.

#### Scenario: Discovered PR's head branch matches the group's branch
- **WHEN** the operator-PR-discovery search returns a candidate PR whose
  `headRefName` equals the group's own branch (`<run_id>/<group_name>`)
- **THEN** the candidate is accepted as the group's PR, exactly as before
  this change

#### Scenario: Discovered PR's branch does not correspond to the group
- **WHEN** the operator-PR-discovery search returns one or more candidates
  and none of them has a head branch equal to the group's own branch or a
  base branch matching the group's target branch
- **THEN** the system rejects all candidates and falls through to normal PR
  creation (`gh pr create`) as if discovery had found nothing

#### Scenario: Free-text search matches an unrelated pre-existing PR
- **WHEN** the free-text search `"<group-name> <spec-id>"` matches a PR that
  belongs to a different branch and workflow (e.g. a release-promotion PR
  unrelated to this group)
- **THEN** that PR is not accepted as the group's PR, and the group is not
  wrongly recorded as already having an open PR

#### Scenario: No candidates found
- **WHEN** the operator-PR-discovery search returns no candidates
- **THEN** behavior is unchanged from before this change — the system falls
  through to normal PR creation
