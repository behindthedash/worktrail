# land-pr-resume-fast-path Specification

## Purpose
Lets a re-invocation of `land_pr()` against a commit that is already pushed
and already has a PR skip the commit / compile-marker / preflight / push
steps and resume at the PR step, instead of re-paying the full pre-PR gate
for a commit the remote already carries — and stops an already-merged branch
from being sent to `gh pr create` a second time.
## Requirements
### Requirement: Already-pushed commit with an existing PR skips the pre-PR steps

Before running step 1 (commit pending work), the system SHALL probe for a
resume condition and, when every one of the following holds, SHALL skip steps
1-4 (commit, compile-marker gate, preflight gate, push) and continue from
step 5 (open or update the pull request):

- the working tree is clean;
- `HEAD` is on a named branch;
- the push remote's tip for that branch equals the local `HEAD` commit;
- a pull request already exists for that branch.

The pre-PR gate SHALL NOT be run and the branch SHALL NOT be pushed on this
path. Labels for the PR body, the PR update, and the reported outcome SHALL
be computed with the same label-resolution function the preflight pass marker
records its labels from, so the resume path's label set matches what a
non-resumed landing would have produced. The rendered PR body SHALL state
that the gate was not re-run on this invocation rather than asserting gate
evidence this invocation did not observe.

#### Scenario: Interrupted landing is re-invoked against the same pushed commit

- **WHEN** `land_pr()` is invoked with a clean tree, the branch's remote tip
  equal to `HEAD`, and an OPEN pull request already existing for the branch
- **THEN** no commit, no compile-marker compilation, no preflight gate run
  and no push is performed
- **AND** the pull request's title, body and labels are still refreshed and
  CI is still watched to a terminal outcome, exactly as on a non-resumed
  landing

#### Scenario: New local work is present

- **WHEN** the working tree is dirty, or the push remote's tip for the branch
  differs from local `HEAD`
- **THEN** the resume path is declined and steps 1-4 run unchanged

#### Scenario: No pull request exists yet for the branch

- **WHEN** the commit is already pushed but no pull request exists for the
  branch
- **THEN** the resume path is declined and steps 1-4 run unchanged

### Requirement: Resume probe failures fall back to the full pipeline

Every git and `gh` call the resume probe makes SHALL be treated as advisory:
a nonzero exit, a timeout, an OS error, or unparseable output SHALL decline
the resume path rather than fail the invocation or be read as a satisfied
precondition. A declined probe SHALL leave `land_pr()` behaving exactly as it
does without the resume path.

#### Scenario: Remote tip cannot be read

- **WHEN** the remote ref lookup for the branch fails or returns nothing
- **THEN** the resume path is declined and the full pipeline runs, producing
  the same outcome it would produce today

#### Scenario: Pull-request lookup fails

- **WHEN** the pull-request lookup for the branch exits nonzero or returns
  output that cannot be parsed
- **THEN** the resume path is declined and the full pipeline runs

### Requirement: A merged or closed pull request is never sent to PR creation

When the resume path finds an existing pull request for the already-pushed
branch whose state is not OPEN, the system SHALL NOT attempt to create a new
pull request.

A MERGED pull request SHALL be reported as a terminal success: the run record
is started if the caller supplied none, the pull request is recorded on it,
and the record is finished with the merged completion status; the outcome is
`landed`.

A pull request that is CLOSED without being merged SHALL be reported as
`ceiling` with a refused-step naming the closed pull request, so a human
reconciles it rather than the pipeline silently replacing a deliberately
closed pull request.

#### Scenario: Branch was already merged

- **WHEN** the resume path finds a MERGED pull request for the branch
- **THEN** no pull request is created, the run record is completed with the
  merged status and the recorded pull-request URL, and the outcome is
  `landed`

#### Scenario: Pull request was closed without merging

- **WHEN** the resume path finds a CLOSED, unmerged pull request for the
  branch
- **THEN** no pull request is created and the outcome is `ceiling`, carrying
  the closed pull request's URL in its detail

