## MODIFIED Requirements

### Requirement: Repo-grouped inventory with dedup skip
The `evaluate` step SHALL inventory every brief in `$WORK_QUEUE_DIR/queue/`. Before grouping,
it SHALL run repo inference (per the `intake-triage` capability's "Brief repo is inferred
deterministically from its focus") on every brief whose `repo:` is missing, null, or empty,
writing a resolved repo back to the brief; it SHALL also consume any answered
repo-assignment decision linked to such a brief. It SHALL then group briefs by their
`repo:` value (briefs sharing a `repo:` value form one group; briefs still without a
`repo:` value form a single additional group). It SHALL exclude from evaluation any brief
whose body contains a `## Triage <date>` section dated within the configured
`--skip-if-triaged-within-days` window (default 25 days) of the run, except that a section
whose first line is `verdict: repo-inferred` SHALL NOT count toward that window and a brief
that is due for escalation (per "Keep verdicts are bounded and escalate deterministically")
SHALL NOT be excluded by the window.

#### Scenario: Two briefs share a repo
- **WHEN** `evaluate` runs against a queue containing two briefs both with
  `repo: /home/user/projects/example`
- **THEN** both briefs are assigned to the same evaluator group and are evaluated by a single
  spawned agent for that repo

#### Scenario: Brief has no repo
- **WHEN** `evaluate` runs against a queue containing a brief with `repo: null` whose focus
  resolves to no repo
- **THEN** that brief is assigned to the no-repo group and evaluated without a repo-fetch
  step

#### Scenario: Null-repo brief is inferred before grouping
- **WHEN** `evaluate` runs against a queue containing a brief with `repo: null` whose focus
  contains `Repo: worktrail` and the `worktrail` checkout exists under the repos root
- **THEN** the brief's `repo:` is written back as that checkout path, a `verdict:
  repo-inferred` triage note is appended, and the brief is evaluated in the `worktrail`
  group of the same run rather than the no-repo group

#### Scenario: Recently triaged brief is skipped
- **WHEN** `evaluate` runs and a brief's body contains `## Triage 2026-08-01` (with a
  verdict other than `repo-inferred`), the run date is within 25 days of 2026-08-01, and the
  brief is not due for escalation
- **THEN** that brief is excluded from every evaluator group and no evaluator agent is
  spawned or spent on it

#### Scenario: Repo-inferred note does not count as a triage
- **WHEN** a brief's only `## Triage <date>` section begins `verdict: repo-inferred` and
  was written today
- **THEN** the brief is not treated as recently triaged and is evaluated in this run

#### Scenario: Due-for-escalation brief bypasses the dedup window
- **WHEN** a brief's most recent triage note is a `verdict: keep` note dated 3 days ago and
  the brief's consecutive keep count is at the `triage_keep_limit`
- **THEN** the brief is evaluated (or escalated directly) in this run rather than skipped

### Requirement: Evidence-required verdict per brief
For every brief passed to a group's evaluator agent, the `evaluate` step SHALL require a
verdict of exactly one of `keep`, `stale-close`, `needs-update`, `duplicate-of`,
`fold-into-change`, `propose-change`, `work-directly`, or `needs-decision`, and SHALL
require non-empty `evidence` text for every verdict. `fold-into-change` SHALL additionally
require a non-empty `target_change` (`<repo>:change:<id>` naming an active change presented
as a candidate); `propose-change` SHALL additionally require a non-empty `target_repo` and a
kebab-case `proposed_change_name`; `needs-decision` SHALL additionally require a non-empty
`question`. For a brief evaluated in the repo-less (`__none__`) group, the evaluator prompt
SHALL list the known workspace repos (the directory basenames under the configured repos
root), `propose-change` SHALL be valid only when `target_repo` is one of those listed names,
and `fold-into-change` SHALL remain invalid since no candidate changes are presented. A
verdict that is missing, malformed, or missing required evidence or required
target fields SHALL be recorded as `keep` with the evaluator's raw output retained as
evidence text, never silently dropped from the output. Every recorded verdict SHALL carry
the brief's `premise_check` results (empty for a no-repo brief). For a brief in the no-repo
group, any verdict that would be recorded as `keep` — including every fail-open fallback —
SHALL instead be recorded as `needs-decision` with the question "which repo does this brief
belong to?" and the would-be `keep` evidence retained as evidence, so a brief with no repo is
never left idle.

#### Scenario: Well-formed stale-close verdict
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "stale-close", "evidence":
  "PR #42 merged 2026-07-01 delivers this", "confidence": "high"}`
- **THEN** the verdict file records brief `X` as `stale-close` with that evidence

#### Scenario: Well-formed fold verdict
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "fold-into-change",
  "target_change": "worktrail:change:work-queue-dependency-diagnostics", "evidence": "...",
  "confidence": "high"}`
- **THEN** the verdict file records brief `X` as `fold-into-change` with that target

#### Scenario: Fold verdict names a change that was not a candidate
- **WHEN** an evaluator returns `fold-into-change` with a `target_change` that is not an
  active change in the brief's repo
- **THEN** the verdict is recorded as `keep` with the raw verdict retained as evidence

#### Scenario: Undecidable case fails open
- **WHEN** an evaluator cannot find evidence to confirm or refute a repo-resolved brief's
  premise within its tool-call budget and the brief is not due for escalation
- **THEN** the verdict for that brief is `keep`, with the evaluator's stated reason for
  inconclusiveness recorded as evidence

#### Scenario: Malformed verdict from an evaluator
- **WHEN** an evaluator's output for a repo-resolved brief cannot be parsed as a valid
  verdict object
- **THEN** the verdict file records that brief as `keep` with the raw unparsed text retained
  as evidence, and the brief is never left out of the verdict file

#### Scenario: Repo-less brief proposes into a known repo
- **WHEN** the evaluator for the `__none__` group cites evidence identifying the owning repo
  and returns `propose-change` with `target_repo` equal to one of the known repo names it was
  shown and a kebab-case `proposed_change_name`
- **THEN** the verdict is accepted as-is

#### Scenario: Repo-less brief proposes into an unknown repo
- **WHEN** the evaluator for the `__none__` group returns `propose-change` whose
  `target_repo` is not one of the known repo names it was shown
- **THEN** the verdict is downgraded to `keep` with the evaluator's output as evidence

#### Scenario: Repo-less brief cannot fold
- **WHEN** the evaluator for the `__none__` group returns `fold-into-change`
- **THEN** the verdict is downgraded to `keep`, since no candidate changes were presented for
  a repo-less brief

#### Scenario: Keep for a no-repo brief becomes needs-decision
- **WHEN** an evaluator returns `keep` (or an unparsable verdict) for a brief in the no-repo
  group
- **THEN** the verdict file records that brief as `needs-decision` with the question "which
  repo does this brief belong to?" and the evaluator's evidence or raw output retained as
  evidence

#### Scenario: Premise check travels with the verdict
- **WHEN** a repo-resolved brief's premise check produced two entries and the evaluator
  returns any valid verdict for it
- **THEN** the verdict file's entry for that brief carries both entries under
  `premise_check`

### Requirement: Apply step never closes a brief without an approved verdict
The `apply` step SHALL only ever act on verdicts present in a verdict file supplied via
`--verdict-file`, SHALL require an explicit `--confirm` flag before executing any
`stale-close`, `needs-update`, `duplicate-of`, `fold-into-change`, `propose-change`,
`work-directly`, `needs-decision`, or `keep` action, and for a `keep` verdict SHALL do
nothing beyond appending a `verdict: keep` triage note to the brief's body (never claiming,
closing, moving, or editing the frontmatter of the brief). `apply` SHALL NOT expose any flag
or code path that closes or edits a brief without both an existing verdict file entry and
the `--confirm` flag. Without `--confirm`, every planned action — including the branch,
target change, and pull-request title a fold or propose would create, and the keep note a
`keep` would append — SHALL be printed and nothing SHALL be modified in the queue or in any
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
  work-directly, decision envelope for needs-decision, triage note for keep) is printed but
  no brief in `queue/` or `picked/` is modified and no target repo is written to

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

#### Scenario: Apply with --confirm records keep
- **WHEN** `apply --confirm` runs against a verdict file containing a `keep` verdict for
  brief `K`
- **THEN** a `## Triage <run-date>` section beginning `verdict: keep` and `keep-count: <n>`
  is appended to brief `K`'s body, brief `K` remains in `queue/` with unchanged frontmatter,
  and the action-log entry reports `append-triage-note` / `executed`

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

#### Scenario: Repo-less propose-change stamps the brief's repo before proposing
- **WHEN** `apply --confirm` runs against a verdict file containing a `propose-change`
  verdict evaluated in the repo-less (`__none__`) group whose `target_repo` is a bare name
  that resolves to a checkout under the configured repos root
- **THEN** the brief's `repo:` frontmatter is set to that bare name before any worktree or
  git operation runs, and the `openspec new change` / worktree / PR flow runs against the
  resolved checkout; a later failure in that flow leaves the brief queued with `repo:` still
  stamped

#### Scenario: Repo-less propose-change with an unresolvable target stamps nothing
- **WHEN** `apply --confirm` runs against a repo-less `propose-change` verdict whose
  `target_repo` does not resolve under the configured repos root
- **THEN** the action-log entry reports an error status naming the unresolvable repo, the
  brief's frontmatter is unchanged, and no worktree, git, or `gh` command runs

#### Scenario: Unresolvable repo value fails closed
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change` or
  `propose-change` verdict whose `repo` value does not resolve to an existing directory,
  either directly or by basename under the configured repos root
- **THEN** the action-log entry for that verdict reports an error status naming the
  unresolvable repo, no worktree is created, and no git or `gh` command runs against a
  guessed path

### Requirement: Verdict file and human-readable report
The `evaluate` step SHALL write two outputs for every run: a machine-applyable JSON verdict
file listing every evaluated brief's verdict, evidence, confidence, `premise_check`, and
escalation (reason and matrix row, or none), and a human-readable Markdown report
summarizing the run (briefs evaluated, briefs skipped via dedup, briefs whose repo was
inferred, verdict counts by type, escalation counts by reason and by resulting verdict, and
the full per-brief verdict list with evidence). Neither output SHALL be written to a location
inside the target repos being evaluated. The `--json` run summary SHALL carry the same
escalation counts as the report.

#### Scenario: Successful evaluate run produces both outputs
- **WHEN** `evaluate` completes a run over a non-empty queue
- **THEN** a JSON verdict file and a Markdown report both exist at the run's output directory,
  and the report's verdict counts match the JSON file's contents exactly

#### Scenario: Escalations appear in every output
- **WHEN** an evaluate run escalates one brief by `keep-limit` to `propose-change`
- **THEN** the verdict file entry records `escalation.reason: keep-limit` and its matrix
  row, the report's escalation section shows `keep-limit: 1` and `propose-change: 1`, and
  the `--json` summary carries the same counts
