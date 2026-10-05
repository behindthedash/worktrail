## MODIFIED Requirements

### Requirement: Merged fold/propose landing tears down its local branch
When a `fold-into-change` or `propose-change` verdict's landing returns outcome `landed` with
`final_status` `completed_and_merged`, `apply` SHALL, after removing the triage worktree, delete
the local triage branch from the target repo. The deletion SHALL be best-effort: a failed
`git branch -D` SHALL NOT change the verdict's status, `pr_url`, or `landing` entry. When the
landing outcome is `code_defect` or `review_threads_blocking`, the worktree and the local
branch SHALL both be kept. When no pull request URL was obtained, the existing behaviour of
removing the worktree and deleting the branch SHALL be unchanged.

Once a pull request URL has been obtained, a rejected closure SHALL NOT return the brief to
`queue/`. When the brief-closing call that follows a landing (`done`) does not complete,
`apply` SHALL leave the brief claimed in `picked/` with its `status` still `picked` --
carrying the rejected closure, so the stalled-in-flight resume path can close it against the
real pull request instead of the case being re-triaged as fresh work -- SHALL report the
rejection in the action-log entry while keeping that entry's `pr_url`, `branch`, and `landing`
values, and SHALL report `rolled_back: false` for that verdict. `apply` SHALL NOT manufacture
the evidence a rejected closure asks for (a synthesized transcript or equivalent structured
note); the brief's real closure stays outstanding until it is closed against the pull request
that already exists. Only a failure that obtained no pull request URL returns the claimed
brief to `queue/`, and a brief SHALL be released at most once per apply attempt.

#### Scenario: Merged landing deletes worktree and branch
- **WHEN** `land_pr` returns `landed` with `final_status="completed_and_merged"` for a
  `propose-change` verdict
- **THEN** `git worktree remove --force <worktree>` and `git branch -D <branch>` are both run
  against the target repo, the brief is closed with `triaged_to` set to the PR URL, and the
  verdict entry reports `status: executed`

#### Scenario: A rejected closure after a merged landing leaves the brief claimed
- **WHEN** `land_pr` returns `landed` with `final_status="completed_and_merged"` and a PR URL,
  and closing the brief does not complete
- **THEN** the brief SHALL NOT be back in `queue/`, SHALL still be in `picked/` with
  `status: picked`, and the verdict entry SHALL report `status: error` naming the failed
  closure together with `pr_url`, `branch`, `landing`, and `rolled_back: false`

#### Scenario: Branch deletion failure does not change the verdict
- **WHEN** the landing merged and `git branch -D` exits non-zero
- **THEN** the verdict entry still reports `status: executed` with the PR URL and
  `landing.final_status: completed_and_merged`

#### Scenario: Code-defect landing keeps worktree and branch
- **WHEN** `land_pr` returns `code_defect`
- **THEN** neither `git worktree remove` nor `git branch -D` is run and the verdict entry's
  `landing.worktree` names the kept worktree

#### Scenario: No-PR failure still deletes the branch
- **WHEN** `land_pr` returns `refused` with no PR URL
- **THEN** the worktree is removed, `git branch -D <branch>` is run, and the brief is released
  back to `queue/` exactly once
