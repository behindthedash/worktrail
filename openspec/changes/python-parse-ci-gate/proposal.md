## Why

The required `Lint, Test & Build` job can accept Python source that its configured interpreter
cannot parse. Ruff lint and pytest do not provide a repository-wide parse guarantee, so a tracked
module whose syntax error sits outside the imported and tested graph can remain in the shipped
source tree unnoticed.

CI needs an explicit, deterministic Python parsing gate so an unparseable tracked Python source
file fails before the package is considered healthy.

The gate is defined against the job's own interpreter, not the developer's. That distinction is
load-bearing here: worktrail's source uses PEP 758 unparenthesized multi-exception handlers
(`except A, B:`), a Python 3.14 feature, and `pyproject.toml` declares `requires-python = ">=3.14"`.
Parsing that same source with an older interpreter reports every such handler as a syntax error
against a tree the configured interpreter parses cleanly. Running the gate under the job's
interpreter keeps that class of version mismatch visible as a version mismatch instead of
surfacing it as phantom syntax defects in valid source.

## What Changes

- Add a CI helper that checks the repository's Python source files with the configured Python
  interpreter and reports every parse failure with an actionable path and diagnostic.
- Run that helper in the existing required `Lint, Test & Build` workflow before pytest.
- Add hermetic tests for valid source, invalid source, discovery boundaries, diagnostics, and the
  committed CI workflow invocation.

## Capabilities

### New Capabilities

- `python-source-parse-ci-gate`: CI rejects a non-bookkeeping change when repository Python
  source cannot be parsed by the job's configured interpreter.

### Modified Capabilities

## Impact

- New `scripts/ci/` parse-gate helper and colocated tests.
- `.github/workflows/ci.yml` gains a step in the existing required check; its job-level gating
  behavior remains unchanged.
- No package-source edits: the tracked tree already parses under the configured interpreter, so
  the new gate passes on the current base. See `design.md` for why an earlier draft expected
  otherwise.
