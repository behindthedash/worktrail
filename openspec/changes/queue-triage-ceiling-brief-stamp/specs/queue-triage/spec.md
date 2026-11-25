## ADDED Requirements

### Requirement: A landing ceiling with no pull request is reported and recorded on the brief
When `router.land_pr` returns a non-`refused` outcome with no pull request URL for a
`fold-into-change` or `propose-change` apply, the apply SHALL report the failure with that
outcome's refused step and detail instead of an opaque no-URL message: the error SHALL name
`land_pr`, the outcome, the refused step when the outcome carries one, and the detail (falling
back to the outcome's merge result, and then to a plain statement that no URL was returned). The
verdict's action-log entry SHALL report `rolled_back: true`, the branch, and a `landing` sub-dict
carrying the outcome, run, final status, merge result, failing checks, refused step, and detail,
so a caller can tell that the brief was returned to `queue/` and why.

Before returning the brief to `queue/`, the apply SHALL record the failed landing on that brief:

- the frontmatter fields `landing-failed-step` (the refused step, or the outcome when it has
  none), `landing-failed-detail` (the detail or merge result, collapsed to a single line, or the
  outcome when neither exists), and `landing-branch` (the triage branch the landing used); and
- a `## Triage <date>` note whose first non-blank line is `verdict: <the applied verdict type>`
  and whose body names the step, the detail, and the branch, and states that no pull request was
  obtained and the brief was returned to the queue.

The recording SHALL happen after the landing attempt and before the release, SHALL be attempted
at most once per apply attempt, and SHALL change nothing else about the brief beyond that
release. Because the note is an ordinary triage outcome, `is_recently_triaged()` SHALL treat the
released brief as recently triaged, so a later `evaluate` within its dedup window excludes the
brief from its evaluation groups and reports it as skipped rather than re-proposing the same
change. The apply step SHALL NOT consult that window, so replaying the same verdict file after
the cause is fixed remains possible.

The release back to `queue/` (exactly once), the worktree and local-branch teardown, every
failure that obtained a pull request URL, and the `refused` landing branch SHALL be unchanged. A
failure to locate or write the brief SHALL be reported in the action-log entry and SHALL NOT
prevent the release.

#### Scenario: A PR-creation ceiling names its cause and stamps the brief
- **WHEN** `land_pr` returns outcome `ceiling` with no `pr_url`, refused step `pr_create`, and
  detail `gh pr create failed: could not add label: go:risk-low not found`
- **THEN** the verdict entry reports `status: error`, `rolled_back: true`, an error naming
  `ceiling`, `pr_create`, and the label failure, and `landing.outcome: ceiling`
- **AND** the brief is back in `queue/` with `status: queued`, carrying
  `landing-failed-step: pr_create`, a `landing-failed-detail` containing the label failure,
  `landing-branch` naming the triage branch, and a most-recent `## Triage` note whose first line
  is `verdict: <the applied verdict type>` and which names the step, the detail, and the branch

#### Scenario: A ceiling with no detail still reports and stamps what it has
- **WHEN** `land_pr` returns outcome `ceiling` with no `pr_url`, no refused step, no detail, and
  no merge result
- **THEN** the error names `ceiling` and that no pull request URL was returned
- **AND** the released brief carries `landing-failed-step: ceiling` and a `## Triage` note
  recording the same ceiling

#### Scenario: A stamped ceiling is skipped by a later evaluate
- **WHEN** a brief released by a ceiling sits in `queue/` inside the evaluation dedup window
- **THEN** `evaluate` excludes it from the evaluation groups and reports it as skipped, so the
  same change is not re-proposed on the strength of the failed apply

#### Scenario: Replaying the verdict after a fix still applies
- **WHEN** the cause named by the ceiling is fixed and the same verdict file is applied again,
  even though the brief now carries the ceiling's triage note
- **THEN** the apply proceeds, and a landing that obtains a pull request URL closes the brief
  with `triaged-to` set to that URL as an ordinary successful apply

#### Scenario: A fold-into-change ceiling records the fold verdict
- **WHEN** the ceiling above occurs for a `fold-into-change` verdict
- **THEN** the `## Triage` note's `verdict:` line reads `fold-into-change` and the remaining
  record is identical in shape to the `propose-change` case

#### Scenario: A refused landing is not stamped
- **WHEN** `land_pr` returns outcome `refused` with no pull request URL
- **THEN** the error names the refused step and detail, the brief is released as before, and the
  brief carries no `landing-failed-*` field and no new `## Triage` note

#### Scenario: A stamping failure is reported and does not block the release
- **WHEN** the brief cannot be located or the stamp write fails
- **THEN** the action-log entry reports the stamping failure and the brief is still released
  back to `queue/` exactly once

#### Scenario: Failures with a pull request URL are unchanged
- **WHEN** `land_pr` returns a pull request URL with outcome `landed`, `code_defect`, or
  `review_threads_blocking`
- **THEN** the existing worktree, branch, closure, and rejected-closure behaviour is unchanged
  and no landing-ceiling record is written
