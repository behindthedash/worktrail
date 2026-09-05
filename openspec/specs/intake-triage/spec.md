# intake-triage Specification

## Purpose
Separates the work queue into an intake channel and an execution channel: handoff briefs are consumed into OpenSpec changes (folded into an existing change or proposed as a new one) and never worked directly, while unattended execution is initiated only from briefs seeded from specs.
## Requirements
### Requirement: Brief kind is derived from provenance
The system SHALL classify every queued brief as exactly one of two kinds: an **execution** brief, when its frontmatter carries a non-empty `seeded-from:` value, or an **intake** brief otherwise. Classification SHALL be derived at read time from existing frontmatter; no new frontmatter field is required and no existing brief SHALL need rewriting to be classified.

#### Scenario: Handoff capture is intake
- **WHEN** a brief was captured via `worktrail-handoff` and has no `seeded-from:` key
- **THEN** it is classified as an intake brief

#### Scenario: Seeded brief is execution
- **WHEN** a brief carries `seeded-from: <repo>:spec:<id>` (or any non-empty `seeded-from:` value)
- **THEN** it is classified as an execution brief

#### Scenario: Consolidated batch is intake
- **WHEN** a brief carries a `## Consolidated from` section or a `related:` list but no `seeded-from:`
- **THEN** it is classified as an intake brief

### Requirement: Unattended auto-pick never claims an intake brief
The unattended auto-pick used by `worktrail-go auto` (and therefore by every drain one-shot) SHALL skip every intake brief with the recorded skip reason `intake-untriaged`, before any other ranking or gating is applied, and SHALL only ever claim execution briefs. The skip SHALL be visible in the auto-pick miss log exactly like existing skip reasons.

#### Scenario: Queue holds only intake briefs
- **WHEN** `worktrail-go auto` runs against a queue whose every brief is an intake brief
- **THEN** no brief is claimed, and every brief is recorded as skipped with reason `intake-untriaged`

#### Scenario: Execution brief ranks normally
- **WHEN** the queue holds one intake brief and one execution brief that passes all existing gates
- **THEN** the execution brief is claimed and the intake brief is recorded as skipped with reason `intake-untriaged`

### Requirement: Interactive pickup of an intake brief triages it
When a user runs `worktrail-go <brief-id>` and the brief is an intake brief, the system SHALL run the intake-triage evaluation for that single brief and present its verdict for confirmation, instead of dispatching it for implementation. Applying the verdict SHALL follow the same apply semantics as the unattended pre-pass, including landing any resulting pull request through the shared PR-landing pipeline in the same invocation. The session SHALL report the landing outcome the apply step returns; when that outcome is a code defect or blocking review threads, the session SHALL continue the CI watch loop's repair procedure against the reported branch and run record rather than stopping at "PR opened". The brief SHALL NOT be claimed into `picked/` for implementation by this path.

#### Scenario: User names an intake brief
- **WHEN** `worktrail-go 20260826-143940-consolidated-...` is invoked and that brief has no `seeded-from:`
- **THEN** the session evaluates the brief against its repo's active changes and offers fold/propose/work-directly/needs-decision, and no implementation dispatch occurs

#### Scenario: User names an execution brief
- **WHEN** `worktrail-go <brief-id>` is invoked for a brief carrying `seeded-from:`
- **THEN** the brief is claimed and dispatched exactly as before this change

#### Scenario: Work-directly continues into dispatch
- **WHEN** an interactive pickup's applied verdict is `work-directly` and the brief is now
  stamped `seeded-from: triage:<run-date>:direct`
- **THEN** the same invocation claims the brief and proceeds through classification and
  dispatch as it would for an execution brief named directly

#### Scenario: Keep is recorded interactively
- **WHEN** an interactive pickup's verdict is `keep` for a brief not yet due for escalation
- **THEN** the brief gains a `verdict: keep` triage note exactly as a scheduled run would
  write, and the session reports the note and stops

#### Scenario: Confirmed fold lands and is watched in the same invocation
- **WHEN** the user confirms a `fold-into-change` verdict and the apply step returns a PR URL with a landed outcome
- **THEN** the session reports the PR URL and the completion state the pipeline recorded, and stops

#### Scenario: Confirmed propose reports a code defect
- **WHEN** the user confirms a `propose-change` verdict and the apply step returns a PR URL with a code-defect outcome
- **THEN** the session does not stop at the report; it repairs the defect in the reported worktree and re-invokes the landing pipeline against the same run record until a terminal outcome is reached

### Requirement: Candidate targets are ranked brief-to-active-change

For each intake brief with a non-null `repo:`, the evaluation SHALL enumerate that repo's active OpenSpec changes (every `openspec/changes/<id>/` with a `proposal.md`, excluding `archive/`), compute a focus-overlap coefficient between the brief's focus-text tokens and each change's feature summary plus task-line tokens, and present the top-K (default 5) changes by coefficient to the evaluator as fold candidates, each with its id, feature summary, and open-task count. A change scoring below a minimum floor of 0.45 SHALL be excluded from the presented candidates regardless of its rank, so a weak, effectively-coincidental lexical match is never offered as a fold target. A brief whose repo has no active changes, or whose active changes all score below the floor, SHALL be evaluated with an empty candidate list. Brief-to-brief clustering SHALL NOT be used to select fold targets.

#### Scenario: Strong overlap with an active change

- **WHEN** an intake brief's focus shares >= 0.45 token overlap with active change `work-queue-dependency-diagnostics` and < 0.1 with every other change
- **THEN** `work-queue-dependency-diagnostics` is the first-ranked fold candidate presented to the evaluator

#### Scenario: Repo has no active changes

- **WHEN** an intake brief names a repo whose `openspec/changes/` contains only `archive/`
- **THEN** the evaluator receives an empty candidate list and may only return `propose-change`, `work-directly`, `needs-decision`, or an existing queue-triage verdict

#### Scenario: Null-repo brief has no candidates

- **WHEN** an intake brief has `repo: null`
- **THEN** no change candidates are enumerated, and the evaluator may return `propose-change` or `fold-into-change` only if its evidence names a repo it verified; otherwise it returns `needs-decision`

#### Scenario: Only weak matches exist

- **WHEN** an intake brief's focus scores below 0.45 token overlap against every one of its repo's active changes (e.g. the strongest match is a title-substring coincidence scoring 0.43)
- **THEN** the evaluator receives an empty candidate list for that brief and `fold-into-change` is not a valid verdict for it, even though the repo does have active changes

### Requirement: Fold and propose are applied as a pull request, fail-closed
Applying a `fold-into-change` verdict SHALL, in a fresh worktree on a branch off the target repo's base branch, append the brief's focus as a `## Folded from <brief-id>` section to the target change's `proposal.md` and append unchecked tasks derived from the brief to its `tasks.md`; applying a `propose-change` verdict SHALL create a new change under the target repo's `openspec/changes/` with proposal, design, specs, and tasks artifacts that pass `openspec validate`. In both cases the system SHALL land the pull request through the shared PR-landing pipeline: the change directory's compile marker SHALL be current and committed before anything is pushed, the PR's labels SHALL come from the preflight gate, the PR SHALL be CI-watched to a classified outcome, and a run record SHALL be finished with a real completion state. The system SHALL close the brief (`status: done`) only after the pull request exists, stamping `triaged-to: <repo>:change:<change-id>` and the pull-request URL in the brief's closure note. The apply result SHALL carry the landing outcome (landed, code defect, review threads blocking, ceiling, or refused) alongside the PR URL. If any step before the pull request fails — including a compile marker that is missing or stale after the compile attempt — the brief SHALL remain in `queue/` unmodified, nothing SHALL be pushed, and the failure SHALL be reported with the branch name.

#### Scenario: Fold succeeds
- **WHEN** `apply --confirm` executes `fold-into-change` targeting `datalena:change:084-automation-health-digest` and the pipeline lands the pull request
- **THEN** the change's `proposal.md`, `tasks.md`, and `.compile-ok` on the PR branch carry the folded content and a current marker, the PR passes CI's scope check without a hand-added commit, and the brief is in `picked/` with `status: done`, `triaged-to: datalena:change:084-automation-health-digest`, and the PR URL in its closure note

#### Scenario: Compile marker cannot be made current
- **WHEN** `apply --confirm` executes `propose-change` and the compile reports scope gaps for the generated change
- **THEN** nothing is pushed, no pull request exists, the brief is still in `queue/` with `status: queued` and unchanged content, and the run reports the compile gap output with the branch name for manual recovery

#### Scenario: Pull request creation fails
- **WHEN** `apply --confirm` executes `propose-change` and PR creation fails after the push
- **THEN** the brief is still in `queue/` with `status: queued` and unchanged content, and the run reports the failure with the branch name for manual recovery

#### Scenario: Proposed change fails validation
- **WHEN** the generated change does not pass `openspec validate`
- **THEN** no commit is made, the brief is unchanged, and the validation output is reported

#### Scenario: Multi-line evidence is collapsed for the tasks.md checklist item
- **WHEN** `apply --confirm` executes `fold-into-change` for a verdict whose `evidence` spans multiple lines
- **THEN** the target change's `tasks.md` gets a single-line `- [ ] N.1 <collapsed evidence>` checklist item with no embedded newlines, while `proposal.md`'s `## Folded from <brief-id>` section carries the evidence verbatim

#### Scenario: Fetch fails before the worktree is created
- **WHEN** `apply --confirm` executes `fold-into-change` or `propose-change` and `git fetch origin <base branch>` fails
- **THEN** no worktree is created, the brief remains in `queue/` unmodified, and the run reports the fetch failure with the branch name that would have been used

#### Scenario: Target was archived upstream since the last local fetch
- **WHEN** `apply --confirm` executes `fold-into-change` and the target change's directory was archived by a commit on `origin/<base branch>` that the local checkout had not yet fetched
- **THEN** the freshly-fetched worktree no longer has that target's `proposal.md`/`tasks.md`, the fold fails closed with that reported as the error, and the brief remains in `queue/` unmodified

#### Scenario: Propose-change prompt names the compile gate
- **WHEN** `_apply_propose_change()` formats `PROPOSE_CHANGE_PROMPT_TEMPLATE` for a brief
- **THEN** the formatted prompt instructs the agent to run `worktrail-compile` against the new change directory and fix any reported problem, in addition to `openspec validate --strict`

#### Scenario: CI reports a code defect on the landed PR
- **WHEN** the pipeline's CI watch classifies the opened PR's failure as a code defect
- **THEN** the brief is closed against the existing PR URL as before, the apply result reports the code-defect outcome with the failing check names and the surviving worktree path, and the run record is left unfinished for repair

### Requirement: Work-directly converts an intake brief into an execution brief
Applying a `work-directly` verdict SHALL stamp `seeded-from: triage:<run-date>:direct` and
`recommended-route: F` on the brief in place, leaving it in `queue/`, so it becomes claimable
by unattended auto-pick. A `work-directly` verdict SHALL be accepted when the brief names a
single repo and EITHER the evaluator's evidence cites a reproducible defect (a failing test,
failing check, or command output) OR the verdict's `premise_check` carries at least one
confirmed entry; the apply step and its no-confirm preview SHALL both use this combined rule.
A `work-directly` verdict satisfying neither condition SHALL be downgraded to `keep` with the
raw verdict retained as evidence.

#### Scenario: Verified small defect
- **WHEN** an evaluator returns `work-directly` with evidence naming a failing test in the
  brief's repo
- **THEN** the brief gains `seeded-from: triage:2026-08-27:direct` and `recommended-route:
  F`, remains in `queue/`, and is claimable by the next drain iteration

#### Scenario: Work-directly accepted on a confirmed premise alone
- **WHEN** an evaluator returns `work-directly` whose evidence only restates the brief, and
  the verdict's `premise_check` carries a `confirmed: true` entry for a quoted error string
  found in the checkout
- **THEN** the verdict is applied and the brief is stamped `seeded-from:
  triage:<run-date>:direct`

#### Scenario: Work-directly without reproduction evidence
- **WHEN** an evaluator returns `work-directly` whose evidence contains no test, check, or
  command reference and whose `premise_check` has no confirmed entry
- **THEN** the verdict is recorded as `keep` with the raw verdict as evidence and the brief
  is not converted

#### Scenario: Motivating brief converges on the first pass
- **WHEN** a brief with `repo: null` whose focus quotes `close-stale-bookkeeping error: ...
  no TASK-*.md found ...` and ends with `Repo: worktrail, src/worktrail/drain/drain.py
  close-stale resume pass` is evaluated against a checkout containing that error string
- **THEN** the brief's repo is inferred as that checkout, the evaluation's verdict is
  `work-directly`, and applying it stamps `seeded-from: triage:<run-date>:direct`

### Requirement: Needs-decision files a pending decision and keeps the brief queued
Applying a `needs-decision` verdict SHALL file a pending-decision envelope in the human
decision queue whose subject is the brief id and whose question is the evaluator's stated
ambiguity (for example, which repo owns a `repo: null` brief), and SHALL leave the brief in
`queue/` with `status: queued`. A brief with an unresolved pending decision SHALL be skipped
by subsequent triage runs until the decision is answered, and SHALL be skipped by auto-pick
as before. When a brief's pending decision carries the repo-assignment question and has been
answered, the next triage inventory SHALL resolve the answer to a checkout (directly, or by
basename under the repos root), write it into the brief's `repo:` frontmatter, consume the
decision, and evaluate the brief in that repo's group in the same run; an answer that does
not resolve to a checkout SHALL leave the brief and decision untouched and be reported.

#### Scenario: Null-repo brief
- **WHEN** an evaluator returns `needs-decision` for a `repo: null` brief with the question
  "which repo owns this?"
- **THEN** a pending decision exists naming the brief and question, and the brief remains
  queued

#### Scenario: Decision answered
- **WHEN** the pending repo-assignment decision is answered with `datalena`
- **THEN** the next triage inventory writes the `datalena` checkout path into the brief's
  `repo:`, the decision is consumed, and the brief is evaluated with that repo's active
  changes as candidates in the same run

#### Scenario: Answer names no checkout
- **WHEN** the pending repo-assignment decision is answered with text that resolves to no
  directory under the repos root
- **THEN** the brief keeps its `awaiting-decision:` link and null repo, is not evaluated,
  and the run reports the unresolvable answer

### Requirement: Per-repo WIP cap on active changes
The repo policy SHALL support an integer `max_active_changes` key, defaulting to `0` (no cap). When a repo's count of active OpenSpec changes is greater than or equal to a non-zero cap, applying a `propose-change` verdict for that repo SHALL be downgraded to `keep` with a `## Triage <date>` note stating the cap, the current count, and the top fold candidates, and the run report SHALL count briefs held by the cap per repo. `fold-into-change`, `work-directly`, and `needs-decision` SHALL NOT be affected by the cap.

#### Scenario: Repo over cap
- **WHEN** datalena's policy sets `max_active_changes: 20`, datalena has 49 active changes, and an evaluator returns `propose-change` for a datalena brief
- **THEN** the brief stays queued with a triage note naming the cap and count, and the report shows one brief held by the cap for datalena

#### Scenario: Cap not set
- **WHEN** a repo's policy omits `max_active_changes`
- **THEN** `propose-change` verdicts for that repo are applied without any cap check

#### Scenario: Fold under an over-cap repo
- **WHEN** a repo is over its cap and an evaluator returns `fold-into-change`
- **THEN** the fold is applied normally

### Requirement: Drain pre-passes close the intake loop
`worktrail-drain` SHALL accept `--intake-triage` and `--seed-backlog` flags. When set, before the first drain iteration it SHALL run, respectively, the intake-triage `evaluate` then `apply --confirm` over the queue, and the backlog seeder; each pre-pass SHALL be reported in the drain JSON summary with counts (briefs evaluated, verdicts by type, PRs opened, briefs held by cap, seeds captured) and SHALL never abort the drain on its own failure (the failure is reported and the drain proceeds). Both flags SHALL default to off.

#### Scenario: Both pre-passes enabled
- **WHEN** `worktrail-drain --intake-triage --seed-backlog --max-items 4` runs
- **THEN** the summary contains an `intake_triage` block and a `seed_backlog` block populated before iteration 1, and iteration 1 claims only execution briefs

#### Scenario: Pre-pass fails
- **WHEN** the intake-triage evaluator cannot spawn an agent
- **THEN** the summary's `intake_triage` block records the error, and drain iterations still run

#### Scenario: Flags omitted
- **WHEN** `worktrail-drain` runs without either flag
- **THEN** no pre-pass runs and the summary carries no pre-pass blocks, matching behavior before this change

### Requirement: Brief repo is inferred deterministically from its focus
When a brief has no `repo:` value, the system SHALL attempt to infer one from its focus text
using exactly these rules, in order, stopping at the first rule that yields a result: (a) a
`Repo: <name>` or `repo: <name>` token anywhere in the focus; (b) a known repo name — the
basename of a git checkout directly under the repos root (default `~/projects`) — appearing
as a whole word; (c) a repo-relative path fragment (a token containing `/` or a file
extension) that exists in exactly one checkout under the repos root. A rule yields a result
only when it identifies exactly one repo; zero matches falls through to the next rule and
two or more distinct matches at the same rule leave the repo unresolved (never a guess).
Inference SHALL run at capture time, and again at triage inventory time for every queued
brief still carrying a null `repo:`, before that brief is grouped. A triage-time inference
SHALL write the resolved repo (as an absolute checkout path) into the brief's frontmatter and
append a `## Triage <date>` section whose first line is `verdict: repo-inferred` and which
names the rule that matched, so the inference is visible and is not repeated on later runs.

#### Scenario: Repo token anywhere in the focus
- **WHEN** a brief captured from `$HOME` has `repo: null` and its focus ends with
  `Repo: worktrail, src/worktrail/drain/drain.py close-stale resume pass`
- **THEN** the brief's repo resolves to the `worktrail` checkout under the repos root by rule
  (a), regardless of where the token appears in the focus

#### Scenario: Known repo name as a whole word
- **WHEN** a null-repo brief's focus is `datalena CI guard fires on docs-only pushes` and
  `datalena` is a git checkout under the repos root
- **THEN** the repo resolves to that checkout by rule (b)

#### Scenario: Unique path fragment
- **WHEN** a null-repo brief's focus names `src/worktrail/drain/drain.py`, no repo token or
  whole-word repo name is present, and that path exists in exactly one checkout under the
  repos root
- **THEN** the repo resolves to that checkout by rule (c)

#### Scenario: Ambiguous mention stays null
- **WHEN** a null-repo brief's focus names two different known repos as whole words and
  carries no `Repo:` token
- **THEN** the repo stays null and the brief is grouped as a no-repo brief

#### Scenario: Path fragment present in several checkouts stays null
- **WHEN** a null-repo brief's only path fragment is `README.md`, which exists in more than
  one checkout under the repos root
- **THEN** the repo stays null

#### Scenario: Triage-time inference is written back once
- **WHEN** the triage inventory encounters a queued brief with `repo: null` whose focus
  resolves by any rule
- **THEN** the brief's frontmatter `repo:` is set to the resolved checkout path, a
  `## Triage <run-date>` section beginning `verdict: repo-inferred` is appended, the brief is
  evaluated in that repo's group in the same run, and the next run does not re-infer it

### Requirement: Mechanical premise check precedes evaluation
Before the evaluator runs on a repo-resolved intake brief, the system SHALL run a
deterministic premise check against the brief's focus: it SHALL extract quoted error strings
and log lines, `path` and `path:line` references, and named test commands; it SHALL search
the repo checkout for each quoted string (trying the whole string first and, when that has
no hit, stable fragments of it split on ellipses and `: ` separators, each at least 12
characters) and confirm each referenced path exists (and, when a line number is given, that
the file has at least that many lines); and it SHALL run a named command only when it matches
an allow-list of read-only test runners (`pytest`, `python -m pytest`, `npm test`, `go test`,
`cargo test`, `ruff check`, `mypy`), in the checkout, with a bounded timeout, treating a
completed non-zero exit as a confirmed reproduction. The check SHALL never modify tracked
files in the checkout. Its results SHALL be presented to the evaluator as a "Mechanical
premise check" block alongside the brief, and SHALL be persisted on the brief's verdict as
`premise_check`: a list of `{kind, needle, confirmed, detail}` entries, one per needle, in
extraction order. The evaluator prompt SHALL instruct the evaluator to cite log or error
output already quoted in the brief as reproduction evidence when the premise check confirms
it. A brief with no extractable needles SHALL carry an empty `premise_check`.

#### Scenario: Quoted log line confirmed by fragment
- **WHEN** a brief's focus quotes `close-stale-bookkeeping error: datalena
  continue-on-error-required-check-ci-guardrail: no TASK-*.md found ... 2.1, 2.2 ...`, the
  whole line does not appear verbatim in the checkout, and the fragment `no TASK-*.md found`
  does
- **THEN** the verdict's `premise_check` carries an entry of kind `quoted` for that line
  with `confirmed: true` and a detail naming the matching fragment and file

#### Scenario: Path with line reference
- **WHEN** a brief's focus names `src/worktrail/drain/drain.py:1502` and that file exists in
  the checkout with at least 1502 lines
- **THEN** `premise_check` carries an entry of kind `path` with `confirmed: true`

#### Scenario: Allow-listed test command reproduces a failure
- **WHEN** a brief's focus names `pytest tests/drain/test_drain.py -k close_stale` and that
  command exits non-zero within the timeout
- **THEN** `premise_check` carries an entry of kind `command` with `confirmed: true` and
  the exit code and output tail in `detail`

#### Scenario: Non-allow-listed command is never run
- **WHEN** a brief's focus names `rm -rf build && make deploy`
- **THEN** no command is executed, and `premise_check` carries an entry of kind `command`
  with `confirmed: false` and a detail stating it is not an allow-listed test runner

#### Scenario: Command exceeds the timeout
- **WHEN** an allow-listed command does not complete within the bounded timeout
- **THEN** it is terminated, its entry is `confirmed: false` with a timeout detail, and the
  evaluation proceeds

#### Scenario: No repo means no premise check
- **WHEN** a brief is evaluated in the no-repo group
- **THEN** no checkout is searched, no command runs, and `premise_check` is empty

### Requirement: Keep verdicts are bounded and escalate deterministically
Every `keep` verdict, whether from a scheduled run or an interactive pickup, SHALL, when
applied with `--confirm`, append a `## Triage <run-date>` section to the brief whose first
line is `verdict: keep`, followed by `keep-count: <n>` (the number of consecutive `keep`
notes now on the brief, most recent first, with any note carrying a different verdict
ending the streak) and the evidence. A brief SHALL be "due for escalation" when its
consecutive keep count is at or above the repo policy's `triage_keep_limit` (integer,
default 2; the default applies to a brief with no resolvable repo) or its age since
`created:` exceeds the repo policy's `triage_max_queue_age_days` (integer, default 14). A
brief due for escalation SHALL never be recorded as `keep` again: when its evaluated
verdict would resolve to `keep` at apply time (a `keep`, a `work-directly` failing the
acceptance rule, or a `propose-change` held by the WIP cap), the verdict SHALL instead be
chosen by this matrix, in order: repo resolved and `premise_check` has a confirmed entry →
`work-directly`; repo resolved, no confirmed entry, and the repo's active-change count is
under its WIP cap (or the cap is unset) → `propose-change` targeting that repo with a
kebab-case change name derived from the brief id; repo resolved and at or over the cap →
`fold-into-change` into the first-ranked candidate change presented for the brief, or
`needs-decision` asking which change should absorb the brief when no candidate was
presented; repo unresolvable → `needs-decision` with the question "which repo does this
brief belong to?". A brief with an unresolvable repo that is due for escalation SHALL be
verdicted by the matrix without spending an evaluator on it. An escalated verdict SHALL
record the escalation reason (`keep-limit` or `queue-age`) and the matrix row taken, and the
run report and JSON summary SHALL count escalations by reason and by resulting verdict.

#### Scenario: First keep is recorded
- **WHEN** `apply --confirm` executes a `keep` verdict for a brief with no prior triage
  notes
- **THEN** the brief gains a `## Triage <run-date>` section beginning `verdict: keep` and
  `keep-count: 1`, stays in `queue/` with `status: queued`, and its frontmatter is unchanged

#### Scenario: Keep limit reached with a confirmed premise
- **WHEN** a brief already carries one `verdict: keep` note, `triage_keep_limit` is 2, the
  evaluator returns `keep` again, and `premise_check` has a confirmed entry
- **THEN** the recorded verdict is `work-directly` with escalation reason `keep-limit`, and
  applying it stamps `seeded-from: triage:<run-date>:direct`

#### Scenario: Keep limit reached, premise unconfirmed, under cap
- **WHEN** a due brief's repo is resolved, `premise_check` has no confirmed entry, and the
  repo is under its `max_active_changes` cap (or the cap is unset)
- **THEN** the recorded verdict is `propose-change` with `target_repo` set to the repo and a
  kebab-case `proposed_change_name` derived from the brief id

#### Scenario: Over cap with a fold candidate
- **WHEN** a due brief's repo is at or over its cap and at least one candidate change was
  presented for the brief
- **THEN** the recorded verdict is `fold-into-change` targeting the first-ranked candidate

#### Scenario: Over cap with no candidate
- **WHEN** a due brief's repo is at or over its cap and no candidate change was presented
- **THEN** the recorded verdict is `needs-decision` asking which change should absorb the
  brief

#### Scenario: Unresolvable repo escalates without an evaluator
- **WHEN** a brief with `repo: null` whose focus resolves to no repo is due for escalation
- **THEN** it is verdicted `needs-decision` with the question "which repo does this brief
  belong to?" and no evaluator agent is spawned for it

#### Scenario: Queue age escalates a never-triaged brief
- **WHEN** a brief created more than `triage_max_queue_age_days` ago has no triage notes and
  the evaluator returns `keep`
- **THEN** the verdict is escalated with reason `queue-age` and chosen by the matrix

#### Scenario: Escalation is reported
- **WHEN** an evaluate run escalates two briefs, one by `keep-limit` to `work-directly` and
  one by `queue-age` to `propose-change`
- **THEN** the verdict file records the reason and row on each, and the report and `--json`
  summary show `keep-limit: 1`, `queue-age: 1`, and the resulting verdicts

