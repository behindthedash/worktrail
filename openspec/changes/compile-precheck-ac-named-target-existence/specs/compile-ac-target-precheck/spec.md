## ADDED Requirements

### Requirement: Compile refuses an acceptance criterion naming an absent update target
`find_missing_ac_targets` SHALL report a finding for each task whose text asks, in one sentence, for
an update to a backticked entity in a backticked repo-relative file path that exists in the target
repository but does not contain that entity. `compile.main()` SHALL print every finding to stderr,
SHALL NOT write the `.compile-ok` marker, and SHALL exit 1, in both its plain and its `--json`
output mode.

#### Scenario: a phantom entry is reported before fan-out
- **WHEN** a task says "Update the `canonical-checkout-drift-sweep.sh` entry in `scripts/README.md`"
  and `scripts/README.md` exists but never mentions `canonical-checkout-drift-sweep.sh`
- **THEN** `find_missing_ac_targets` returns a finding naming the task id, the needle and the file

#### Scenario: a real entry produces no finding
- **WHEN** the same task text is checked against a `scripts/README.md` that does contain
  `canonical-checkout-drift-sweep.sh`
- **THEN** no finding is returned

#### Scenario: compile fails on a finding
- **WHEN** `compile.main()` settles a plan for a change with at least one finding
- **THEN** it exits 1, prints the finding to stderr, and writes no `.compile-ok` marker

#### Scenario: json mode reports the same failure
- **WHEN** the same compile runs with `--json`
- **THEN** stdout is still the parseable plan, the finding goes to stderr, and the exit code is 1

### Requirement: The precheck only fires on an unambiguous update claim
The extraction SHALL require, within a single sentence of the task's text, all three of an
update-verb, a backticked needle, and a backticked repo-relative path. It SHALL NOT report a finding
for additive phrasing, for unbackticked prose, or for a named path that does not exist in the target
repository -- an absent path is left to the existing file-scope and review paths rather than
reported here.

#### Scenario: additive phrasing is ignored
- **WHEN** a task says "Add a `drift-sweep` entry to `scripts/README.md`"
- **THEN** no finding is returned, whether or not the entry already exists

#### Scenario: unbackticked prose is ignored
- **WHEN** a task says "update the drift sweep entry in the scripts readme" with no backticks
- **THEN** no finding is returned

#### Scenario: an absent target file is ignored
- **WHEN** a task says "Update the `foo` entry in `docs/not-created-yet.md`" and that file does not
  exist in the repository
- **THEN** no finding is returned
