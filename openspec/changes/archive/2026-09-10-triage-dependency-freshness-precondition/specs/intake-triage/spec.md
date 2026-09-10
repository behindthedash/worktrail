## ADDED Requirements

### Requirement: Dependency freshness is checked before reproduction

Before the evaluator runs on a repo-resolved brief group, the system SHALL run a deterministic
dependency-freshness check against the group's repo checkout. For each tracked
`package-lock.json` in the checkout it SHALL compare, for every direct dependency and
devDependency of that package root, the version pinned in the lockfile with the version
installed under the root's adjacent `node_modules`, and SHALL classify the root as `fresh`
(every direct dependency installed at its pinned version), `stale` (at least one direct
dependency missing or installed at a different version, each listed with its name, locked
version, and installed version), or `unknown` (the lockfile or an installed package manifest
cannot be read, or the lockfile carries no per-package version map). The check SHALL only
read; it SHALL NOT install, update, or modify anything in the checkout. Its results SHALL be
presented to the evaluator as a "Dependency freshness" block for the group, and the evaluator
prompt SHALL state that reproduction output obtained by running a `stale` or `unknown` root's
tooling is not evidence for `stale-close`, `needs-update`, or `work-directly`, is not proof
that no candidate change fits, and that the evaluator should prefer `keep` with evidence
naming the root and its mismatched packages. The results SHALL be returned with the group's
evaluation result and persisted on each of the group's verdicts as `dependency_freshness`: a
list of `{app_dir, lockfile, status, mismatches, detail}` entries, one per package root. A
checkout with no tracked npm lockfile SHALL carry an empty `dependency_freshness`, and the
no-repo group SHALL carry an empty `dependency_freshness` without inspecting any checkout.

#### Scenario: Installed runner differs from the lockfile

- **WHEN** the checkout's `app/package-lock.json` pins `vitest` at `5.0.0` and
  `app/node_modules/vitest/package.json` reports `4.1.11`
- **THEN** the group's `dependency_freshness` carries an entry for `app` with status `stale`
  whose mismatches name `vitest` with locked `5.0.0` and installed `4.1.11`, the evaluator
  prompt's "Dependency freshness" block shows that entry, and every verdict parsed for the
  group carries the same `dependency_freshness` list

#### Scenario: Direct dependency not installed

- **WHEN** a package root's lockfile lists a direct dependency that has no directory under
  the root's `node_modules`
- **THEN** that root is reported `stale` with the dependency listed as installed `missing`

#### Scenario: Every direct dependency matches

- **WHEN** every direct dependency and devDependency of a package root is installed at the
  version its lockfile pins
- **THEN** that root is reported `fresh` with an empty mismatch list

#### Scenario: Lockfile without a per-package version map

- **WHEN** a tracked `package-lock.json` has no `packages` map or cannot be parsed
- **THEN** that root is reported `unknown` and the checkout is not modified

#### Scenario: Repo without an npm lockfile

- **WHEN** the group's checkout tracks no `package-lock.json`
- **THEN** `dependency_freshness` is empty, the prompt block states that no npm package roots
  were found, and no reproduction command is skipped on freshness grounds

#### Scenario: No repo means no freshness check

- **WHEN** a brief is evaluated in the no-repo group
- **THEN** no checkout is inspected and each verdict's `dependency_freshness` is empty

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
