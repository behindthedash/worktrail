## Purpose

Independently proves that the wheel and sdist `setuptools.build_meta`
actually generates for worktrail — the artifacts `pip install worktrail`
consumes — carry the same version and console-script entry points as the
checkout's `pyproject.toml` declarations, catching packaging-syntax or
build-backend drift that a check against only the already-installed
distribution cannot see.

## ADDED Requirements

### Requirement: Hermetic artifact build
When the check performs the wheel/sdist build itself (no pre-built artifact
paths supplied), the system SHALL build into an isolated temporary
directory and SHALL NOT modify the checkout's working tree. In every mode —
whether it builds the artifacts itself or is given paths to already-built
artifacts — the system SHALL NOT modify the canonical editable install or
any package already resolvable on `sys.path` for the invoking Python
environment.

#### Scenario: Build directory is isolated and cleaned up
- **WHEN** the check runs against a checkout with no pre-built artifact
  paths supplied
- **THEN** the wheel and sdist are built into a temporary directory created
  for that run
- **AND** no file under the checkout's working tree is created, modified, or
  deleted as a result
- **AND** the temporary directory is removed after the check completes,
  including when the check fails

#### Scenario: Canonical editable install is untouched
- **WHEN** the check runs in an environment with worktrail already installed
  in editable mode
- **THEN** the installed distribution's metadata (as read by
  `importlib.metadata`) is unchanged after the check completes

#### Scenario: Pre-built artifact paths are read without rebuilding
- **WHEN** the check is given the paths to an already-built wheel and sdist
  instead of building them itself
- **THEN** the check reads those artifacts directly and does not invoke a
  build

### Requirement: Built-artifact metadata extraction
The system SHALL extract the project version and the set of `console_scripts`
entry-point names from the built wheel's `METADATA` and `entry_points.txt`,
and from the built sdist's `PKG-INFO`, without installing either artifact.

#### Scenario: Wheel metadata is read without installation
- **WHEN** a wheel has been built for the checkout
- **THEN** the check reads that wheel's `METADATA` and `entry_points.txt`
  directly from the wheel archive
- **AND** the wheel is not installed into any environment to obtain this
  information

#### Scenario: Sdist metadata is read without installation
- **WHEN** an sdist has been built for the checkout
- **THEN** the check reads that sdist's `PKG-INFO` directly from the sdist
  archive
- **AND** the sdist is not installed or unpacked outside the temporary build
  directory to obtain this information

### Requirement: Version and console-script parity
The system SHALL compare the wheel's and sdist's normalized project version
against the checkout's `pyproject.toml` declared version, and SHALL compare
the wheel's `console_scripts` entry-point name set against the checkout's
declared `[project.scripts]` name set, reporting a failure that identifies
which artifact and which values disagree whenever any comparison does not
match.

#### Scenario: Matching wheel and sdist pass
- **WHEN** the built wheel's and sdist's versions equal the checkout's
  declared version, and the wheel's console-script name set equals the
  checkout's declared `[project.scripts]` name set
- **THEN** the check reports success and exits zero

#### Scenario: Version drift between checkout and a built artifact fails
- **WHEN** the built wheel's or sdist's version does not equal the
  checkout's declared `pyproject.toml` version
- **THEN** the check fails, exits non-zero, and reports which artifact
  (wheel or sdist) disagreed and the two differing version values

#### Scenario: Missing console script in the built wheel fails
- **WHEN** a name declared in the checkout's `[project.scripts]` table is
  absent from the built wheel's `entry_points.txt`
- **THEN** the check fails, exits non-zero, and reports the missing
  script name

#### Scenario: Extra console script in the built wheel fails
- **WHEN** the built wheel's `entry_points.txt` contains a `console_scripts`
  name not declared in the checkout's `[project.scripts]` table
- **THEN** the check fails, exits non-zero, and reports the extra script
  name

### Requirement: Deterministic failure on malformed or missing artifact metadata
The system SHALL fail with a clear, actionable error message — not an
unhandled exception — when a build fails to produce a wheel or sdist, or
when a produced artifact is missing its `METADATA`, `entry_points.txt`, or
`PKG-INFO` file.

#### Scenario: Build failure is reported clearly
- **WHEN** the wheel or sdist build process fails to produce an artifact
- **THEN** the check exits non-zero and prints an error identifying that the
  build itself failed, distinct from a metadata-mismatch failure

#### Scenario: Artifact missing expected metadata file is reported clearly
- **WHEN** a built wheel or sdist archive does not contain its expected
  metadata file
- **THEN** the check exits non-zero and prints an error naming the missing
  file and the artifact it was expected in

### Requirement: CI integration without a new required check
The system SHALL run the built-artifact parity check within the existing CI
build step that already produces the wheel and sdist, and SHALL NOT
introduce a new CI job or a new required status-check name.

#### Scenario: Parity check runs after the existing build step
- **WHEN** CI's existing `Lint, Test & Build` job runs its package-build step
  for a pull request that is not classified bookkeeping-only
- **THEN** the built-artifact parity check runs against the wheel and sdist
  that step produces, within the same job

#### Scenario: No additional required status check is introduced
- **WHEN** the built-artifact parity check is wired into CI
- **THEN** no new job name is added to branch-ruleset required status checks,
  and the existing `Lint, Test & Build` check name continues to be the check
  that reflects this verification's pass/fail result
