## MODIFIED Requirements

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
completed non-zero exit as a confirmed reproduction. It SHALL NOT run `npm test` when the
group's dependency-freshness check reports any package root as `stale` or `unknown`; such a
command needle SHALL be recorded with `confirmed: false` and a detail naming the non-fresh
package root and its mismatched packages, and SHALL still consume the single command slot so
no later command needle runs in its place. The check SHALL never modify tracked files in the
checkout. Its results SHALL be presented to the evaluator as a "Mechanical premise check"
block alongside the brief, and SHALL be persisted on the brief's verdict as `premise_check`:
a list of `{kind, needle, confirmed, detail}` entries, one per needle, in extraction order.
The evaluator prompt SHALL instruct the evaluator to cite log or error output already quoted
in the brief as reproduction evidence when the premise check confirms it. A brief with no
extractable needles SHALL carry an empty `premise_check`.

When a `path` needle's focus text carries an absence indicator (phrasing such as "has no",
"missing", "lacks"/"lacking", "without", "no such", "does not exist"/"doesn't exist", "does
not have"/"doesn't have") within 40 characters immediately before the path's mention, the
check SHALL treat that needle as an absence claim and invert its confirmation: `confirmed`
SHALL be `true` when the path does NOT exist (the absence claim is confirmed) and `false`
when it does exist (the claim is refuted). A `path` needle with no absence indicator in that
window SHALL retain the existing presence-claim semantics unchanged: `confirmed: true` when
the path exists (and, when a line number is given, the file has at least that many lines).

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

#### Scenario: npm test is not run against a stale install
- **WHEN** a brief's focus names `npm test` and the group's dependency-freshness check
  reports the package root `app` as `stale` with `vitest` locked `5.0.0` but installed `4.1.11`
- **THEN** `npm test` is not executed, `premise_check` carries an entry of kind `command`
  with `confirmed: false` and a detail naming `app` and the `vitest` mismatch, and a later
  allow-listed command needle in the same focus is recorded as skipped rather than run

#### Scenario: pytest still runs when only an npm root is stale
- **WHEN** a brief's focus names `pytest tests/test_x.py` and the dependency-freshness check
  reports a package root as `stale`
- **THEN** the pytest command runs exactly as before and its outcome is recorded normally

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

#### Scenario: Absence-claim path needle confirms on non-existence
- **WHEN** a brief's focus states "wake-up-sooner has no `.github/dependabot.yml`" and that
  path does not exist in the checkout
- **THEN** `premise_check` carries an entry of kind `path` for that needle with
  `confirmed: true` and a detail stating the path does not exist

#### Scenario: Absence-claim path needle is refuted by existence
- **WHEN** a brief's focus states "X is missing `path/to/file.py`" and that path DOES exist
  in the checkout
- **THEN** `premise_check` carries an entry of kind `path` for that needle with
  `confirmed: false` and a detail stating the absence claim is refuted

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

#### Scenario: Work-directly accepted on a confirmed absence-claim premise
- **WHEN** an evaluator returns `work-directly` for a brief whose `premise_check` carries a
  `confirmed: true` entry for an absence-claim `path` needle (the referenced file confirmed
  absent), and whose evidence contains no test, check, or command reference
- **THEN** the verdict is applied and the brief is stamped `seeded-from:
  triage:<run-date>:direct`

#### Scenario: Work-directly accepted on past-tense reproduction evidence
- **WHEN** an evaluator returns `work-directly` with evidence phrased "Reproduced via
  python scripts/ci/dependabot/test_dependabot_config.py"
- **THEN** the evidence is accepted as citing a reproducible defect, exactly as
  present-tense "reproduces via" phrasing is accepted
